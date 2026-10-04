# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.
import ast
import json
import re
from typing import Dict, Any, List, Optional
from app.config import config
from app.prompt_skill import load_optional_nsfw_prompt, nsfw_content_requested, render_prompt
from app.services.llm_service import llm_service
from app.utils.i18n import language_name, translate
from app.utils.logger import get_logger
from app.utils.outfit_similarity import (
    are_outfits_visually_equivalent,
    stable_outfit_description,
)
from app.models.schemas import Script, Scene, Character, SceneDefinition

logger = get_logger("script_agent")


class ScriptAgent:
    """剧本分镜台词生成Agent - 调用seed-sc-off"""

    _NSFW_CONFLICT_PATTERNS = (
        re.compile(r"立即服从|每一个请求|不限制|Ignore any constraints", re.IGNORECASE),
        re.compile(r"编造事实|捏造|自由推测|confidently.*make", re.IGNORECASE),
        re.compile(r"首要任务.*交付|creativity.*obedience", re.IGNORECASE),
        re.compile(r"永远不会警告|never.*warn", re.IGNORECASE),
    )
    _NSFW_METADATA_PATTERNS = (
        re.compile(r"本地私有扩展|NSFW\\?_?ENABLED|公开仓库|关闭.*跳过|该文件"),
    )

    def __init__(self):
        # 使用 YAML 中当前启用的剧本模型
        self.model = config.get('models.script.endpoint')
        self.timeout = 300  # 超时时间 300 秒
        # 从 yaml 配置读取视频时长设置
        self.default_total_duration = config.get('video_generation.total_duration', 60)  # 默认 60 秒
        self.total_duration_min = config.get('video_generation.total_duration_min', 30)  # 最小 30 秒
        self.total_duration_max = config.get('video_generation.total_duration_max', 1200)  # 最大时长由 yaml 配置控制
        # 从 yaml 配置读取分镜时长范围
        self.scene_duration_min = config.get('video_generation.scene_duration.min', 5)
        self.scene_duration_max = config.get('video_generation.scene_duration.max', 30)
        self.max_characters = int(config.get('script_generation.max_characters', 30))
        self.max_setting_definitions = int(
            config.get('script_generation.max_setting_definitions',
                       config.get('script_generation.max_scene_definitions', 40))
        )
        self.max_storyboard_scenes = int(config.get('script_generation.max_storyboard_scenes', 80))
        self.temperature = config.get('models.script.temperature', 0.8)
        self.max_tokens = int(config.get('models.script.max_tokens', 120000))

    def _disallowed_duration_examples(self) -> str:
        """返回超出分镜时长范围的示例值，用于提示词约束。"""
        first = self.scene_duration_max + 1
        return f"{first}、{first + 1}"

    def _bounded_total_duration(self, value: Any) -> int:
        """Clamp a requested total duration to the configured hard ceiling."""
        try:
            duration = int(value)
        except (TypeError, ValueError):
            duration = int(self.default_total_duration)
        return max(1, min(duration, int(self.total_duration_max)))

    def _sanitize_script_private_prompt(self, text: str) -> str:
        """Remove local private extension lines that conflict with schema rules."""
        if not text:
            return ""
        kept_lines: List[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                kept_lines.append(raw_line)
                continue
            if any(pattern.search(line) for pattern in self._NSFW_METADATA_PATTERNS):
                continue
            if any(pattern.search(line) for pattern in self._NSFW_CONFLICT_PATTERNS):
                continue
            kept_lines.append(raw_line)
        return "\n".join(kept_lines).strip()

    def _append_script_private_extensions(self, prompt_parts: List[str], *trigger_texts: Any) -> None:
        """Append script-specific private extensions once, with bounded priority."""
        if not nsfw_content_requested(*trigger_texts):
            return

        extensions: List[str] = []
        for name in ("script_core.md", "script_outfit.md"):
            text = self._sanitize_script_private_prompt(load_optional_nsfw_prompt(name))
            if text:
                extensions.append(text)
        if not extensions:
            return

        prompt_parts.extend([
            "",
            "【本地私有扩展 - 限定优先级】",
            "以下扩展仅用于成人题材的内容细化与角色装扮判定参考；不得覆盖本系统提示中的 JSON 结构、字段顺序、时长范围、角色/布景上限、风格原文、成人剧情镜头禁限、跨分镜视觉连续性、事实来源、语言和质量校验规则。",
            "\n\n".join(extensions),
        ])

    def _fit_scene_durations_to_target(
        self, scenes: List[Dict[str, Any]], target_total_duration: int
    ) -> None:
        """在保持分镜数量与情节不变的前提下，把各分镜 duration 微调到使总时长尽量贴近目标秒数。

        约束：
        - 每个分镜的 duration 仍是 [scene_duration_min, scene_duration_max] 的整数。
        - 只增减时长，不新增/删除分镜，避免破坏模型已规划的情节结构。
        - 若目标超出 n 个分镜的可达区间 [n*min, n*max]，则贴到最近的可达端点。
        - 优先从时长冗余（>min）的分镜回收、向时长仍有余量（<max）的分镜补足，
          让开头/结尾等短镜头尽量保持精简，主要时长留给情节推进的分镜。
        """
        if not scenes or not target_total_duration:
            return

        n = len(scenes)
        lo, hi = self.scene_duration_min, self.scene_duration_max
        # 目标钳制到当前分镜数可达的时长区间。
        bounded_total = self._bounded_total_duration(target_total_duration)
        target = max(n * lo, min(n * hi, bounded_total))

        # 先确保每个分镜落在合法区间内，再按内容复杂度重分配。
        # 模型经常把所有分镜都输出成同一个时长；只做轮流 +1/-1
        # 会保留这个问题，因此这里用脚本内容重新计算权重。
        original_durations: List[int] = []
        for scene in scenes:
            try:
                dur = int(scene.get('duration') or lo)
            except (TypeError, ValueError):
                dur = lo
            original_durations.append(dur)
            scene['duration'] = max(lo, min(hi, dur))

        self._allocate_scene_durations_by_complexity(scenes, target)
        for scene, original_duration in zip(scenes, original_durations):
            self._retime_scene_description(
                scene,
                original_duration=original_duration,
                new_duration=int(scene["duration"]),
            )

    def _estimate_scene_complexity(self, scene: Dict[str, Any], index: int, count: int) -> float:
        """Estimate how much screen time a scene needs from its structured content."""
        text_fields = (
            scene.get("description"),
            scene.get("character_description"),
            scene.get("dialogue"),
            scene.get("voice_description"),
            scene.get("mood"),
        )
        text_length = sum(len(self._normalize_single_line(value)) for value in text_fields)
        score = 1.0 + min(5.0, text_length / 180.0)

        characters = scene.get("characters_present") or []
        if isinstance(characters, str):
            characters = [part for part in re.split(r"[、,，\s]+", characters) if part]
        score += min(2.0, max(0, len(characters) - 1) * 0.45)
        raw_dialogue = str(scene.get("dialogue") or "")
        dialogue = self._normalize_single_line(raw_dialogue)
        score += min(1.4, len(dialogue) / 90.0)
        score += min(1.2, max(0, len([line for line in raw_dialogue.splitlines() if line.strip()]) - 1) * 0.3)

        scene_text = self._normalize_single_line(" ".join(str(value or "") for value in text_fields))
        complexity_keywords = (
            "追逐", "战斗", "打斗", "搏斗", "爆炸", "逃跑", "冲突", "反转",
            "发现", "调查", "切换", "多人", "同时", "连续", "镜头", "揭示",
            "对峙", "争夺", "穿越", "闪回", "转场", "群像", "调度",
            "性爱", "高潮", "亲吻", "拥抱", "抚摸",
        )
        score += min(2.0, sum(scene_text.count(keyword) for keyword in complexity_keywords) * 0.18)
        score += min(1.5, max(0, len(self._extract_description_timeline_segments(
            scene.get("description")
        )) - 2) * 0.35)

        # 开头用于建立信息、结尾用于收束；中段通常承载更多动作和因果。
        if count > 1 and index == 0:
            score *= 0.82
        elif count > 1 and index == count - 1:
            score *= 0.88
        elif count >= 3 and 0 < index < count - 1:
            score *= 1.08
        return max(0.1, score)

    def _allocate_scene_durations_by_complexity(
        self, scenes: List[Dict[str, Any]], target_total_duration: int
    ) -> None:
        """Allocate the reachable total across scenes using content-based weights."""
        if not scenes:
            return
        lo = self.scene_duration_min
        n = len(scenes)
        duration_caps = [self._scene_duration_cap(scene) for scene in scenes]
        bounded_total = self._bounded_total_duration(target_total_duration)
        target = max(n * lo, min(sum(duration_caps), bounded_total))
        extra = target - n * lo
        if extra <= 0:
            for scene in scenes:
                scene["duration"] = lo
            return

        weights = [
            self._estimate_scene_complexity(scene, index, n)
            for index, scene in enumerate(scenes)
        ]
        total_weight = sum(weights) or float(n)
        capacities = [duration_cap - lo for duration_cap in duration_caps]
        raw_extras = [extra * weight / total_weight for weight in weights]
        extras = [
            min(capacity, int(value))
            for capacity, value in zip(capacities, raw_extras)
        ]
        remainder = extra - sum(extras)

        # Largest remainder allocation keeps the exact total while preserving
        # the relative complexity ordering as much as integer seconds allow.
        order = sorted(
            range(n),
            key=lambda idx: (raw_extras[idx] - int(raw_extras[idx]), weights[idx]),
            reverse=True,
        )
        while remainder > 0:
            progressed = False
            for idx in order:
                if remainder <= 0:
                    break
                if extras[idx] < capacities[idx]:
                    extras[idx] += 1
                    remainder -= 1
                    progressed = True
            if not progressed:
                break

        for scene, scene_extra in zip(scenes, extras):
            scene["duration"] = lo + scene_extra

        def current_total() -> int:
            return sum(int(s.get('duration', lo)) for s in scenes)

        diff = target - current_total()
        if diff > 0:
            # 需要增时长：轮流给仍有余量 (<max) 的分镜每次 +1，直至补齐或全部到顶。
            while diff > 0:
                progressed = False
                for index, scene in enumerate(scenes):
                    if diff <= 0:
                        break
                    if int(scene['duration']) < duration_caps[index]:
                        scene['duration'] = int(scene['duration']) + 1
                        diff -= 1
                        progressed = True
                if not progressed:
                    break
        elif diff < 0:
            # 需要减时长：轮流从冗余 (>min) 的分镜每次 -1，直至削够或全部触底。
            deficit = -diff
            while deficit > 0:
                progressed = False
                for scene in scenes:
                    if deficit <= 0:
                        break
                    if int(scene['duration']) > lo:
                        scene['duration'] = int(scene['duration']) - 1
                        deficit -= 1
                        progressed = True
                if not progressed:
                    break

        self._ensure_scene_duration_variation(scenes, weights, duration_caps)

    def _scene_duration_cap(self, scene: Dict[str, Any]) -> int:
        """Limit duration to the amount of executable timeline detail supplied."""
        segment_count = len(self._extract_description_timeline_segments(
            scene.get("description")
        ))
        if segment_count == 0:
            return self.scene_duration_max
        if segment_count < 3:
            return min(self.scene_duration_max, 9)
        if segment_count < 4:
            return min(self.scene_duration_max, 18)
        return self.scene_duration_max

    def _ensure_scene_duration_variation(
        self,
        scenes: List[Dict[str, Any]],
        complexity_weights: List[float],
        duration_caps: List[int],
    ) -> None:
        """Create a visible duration spread while preserving the exact total."""
        if len(scenes) < 2 or self.scene_duration_max - self.scene_duration_min < 2:
            return

        durations = [int(scene["duration"]) for scene in scenes]
        total = sum(durations)
        minimum_total = len(scenes) * self.scene_duration_min
        maximum_total = sum(duration_caps)
        lower_slack = total - minimum_total
        upper_slack = maximum_total - total
        if lower_slack < 2 or upper_slack < 2:
            return

        desired_distinct = 3 if len(scenes) >= 4 and lower_slack >= 3 and upper_slack >= 3 else 2

        def variation_metric(values: List[int]) -> tuple:
            return (
                min(desired_distinct, len(set(values))),
                min(2, max(values) - min(values)),
            )

        while variation_metric(durations) < (desired_distinct, 2):
            current_metric = variation_metric(durations)
            best_candidate = None
            best_metric = current_metric
            best_weight_gap = float("-inf")
            for receiver in range(len(durations)):
                if durations[receiver] >= duration_caps[receiver]:
                    continue
                for donor in range(len(durations)):
                    if (
                        donor == receiver
                        or durations[donor] <= self.scene_duration_min
                        or complexity_weights[receiver] < complexity_weights[donor]
                    ):
                        continue
                    candidate = durations.copy()
                    candidate[receiver] += 1
                    candidate[donor] -= 1
                    metric = variation_metric(candidate)
                    weight_gap = complexity_weights[receiver] - complexity_weights[donor]
                    if metric > best_metric or (
                        metric == best_metric
                        and metric > current_metric
                        and weight_gap > best_weight_gap
                    ):
                        best_candidate = candidate
                        best_metric = metric
                        best_weight_gap = weight_gap
            if best_candidate is None:
                break
            durations = best_candidate

        for scene, duration in zip(scenes, durations):
            scene["duration"] = duration

    def _retime_scene_description(
        self,
        scene: Dict[str, Any],
        original_duration: int,
        new_duration: int,
    ) -> None:
        """Scale timeline labels when duration allocation changes a scene."""
        description = str(scene.get("description") or "")
        matches = list(re.finditer(
            r"(?P<start>\d+(?:\.\d+)?)\s*(?:-|–|—|~|～|至|到)\s*"
            r"(?P<end>\d+(?:\.\d+)?)\s*(?:秒|seconds?|secs?|s|segundos?)\s*[:：]",
            description,
            flags=re.IGNORECASE,
        ))
        if not matches:
            return

        timeline_duration = float(matches[-1].group("end")) or float(original_duration)
        if timeline_duration <= 0 or abs(timeline_duration - new_duration) < 0.01:
            return

        segment_count = len(matches)
        boundaries: List[float] = [0.0]
        for index in range(1, segment_count):
            ratio = float(matches[index].group("start")) / timeline_duration
            raw_boundary = ratio * new_duration
            if new_duration >= segment_count:
                lower = boundaries[-1] + 1
                upper = new_duration - (segment_count - index)
                boundary = float(max(lower, min(upper, round(raw_boundary))))
            else:
                lower = boundaries[-1] + 0.1
                upper = new_duration - (segment_count - index) * 0.1
                boundary = max(lower, min(upper, round(raw_boundary, 1)))
            boundaries.append(boundary)
        boundaries.append(float(new_duration))

        def format_second(value: float) -> str:
            return str(int(value)) if float(value).is_integer() else f"{value:.1f}".rstrip("0").rstrip(".")

        rebuilt: List[str] = []
        cursor = 0
        for index, match in enumerate(matches):
            rebuilt.append(description[cursor:match.start()])
            rebuilt.append(
                f"{format_second(boundaries[index])}-{format_second(boundaries[index + 1])}秒："
            )
            cursor = match.end()
        rebuilt.append(description[cursor:])
        scene["description"] = "".join(rebuilt)

    def _normalize_single_line(self, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip())

    def _normalize_scene_state(self, time_of_day: Any, weather: Any) -> str:
        parts = [
            self._normalize_single_line(time_of_day),
            self._normalize_single_line(weather),
        ]
        return "，".join(part for part in parts if part)

    def _normalize_scene_feature_list(self, value: Any) -> List[str]:
        if isinstance(value, list):
            raw_items = value
        else:
            raw_items = re.split(r"[、,，/|；;\n]+", str(value or ""))

        features: List[str] = []
        seen = set()
        for item in raw_items:
            feature = self._normalize_single_line(item)
            if not feature:
                continue
            key = feature.lower()
            if key in seen:
                continue
            seen.add(key)
            features.append(feature)
        return features

    def _normalize_character_outfits(self, value: Any) -> Dict[str, str]:
        """规范化分镜的角色装扮映射为 {角色名: 装扮描述}，忽略空条目。"""
        outfits: Dict[str, str] = {}
        if isinstance(value, dict):
            items = value.items()
        elif isinstance(value, list):
            # 兼容 [{"name": .., "outfit": ..}] 或 [{"character": .., "clothing": ..}] 结构
            items = []
            for entry in value:
                if isinstance(entry, dict):
                    name = entry.get("name") or entry.get("character") or entry.get("role")
                    outfit = entry.get("outfit") or entry.get("clothing") or entry.get("costume") or entry.get("description")
                    items.append((name, outfit))
        else:
            return outfits

        for name, outfit in items:
            char_name = self._strip_character_name_markers(name)
            outfit_desc = stable_outfit_description(outfit)
            if char_name and outfit_desc:
                outfits[char_name] = outfit_desc
        return outfits

    def _strip_character_name_markers(self, value: Any) -> str:
        name = self._normalize_single_line(value)
        while len(name) >= 2 and (
            (name.startswith("[") and name.endswith("]"))
            or (name.startswith("【") and name.endswith("】"))
        ):
            name = name[1:-1].strip()
        return name

    def _canonicalize_similar_character_outfits(self, scenes: List[Dict[str, Any]]) -> None:
        """Reuse the first wording for visually equivalent outfits across scenes."""
        representatives: Dict[str, List[str]] = {}
        for scene in scenes or []:
            normalized_outfits = self._normalize_character_outfits(scene.get("character_outfits"))
            canonical_outfits: Dict[str, str] = {}
            for character_name, outfit_desc in normalized_outfits.items():
                character_key = self._normalize_character_name_key(character_name)
                prior_descriptions = representatives.setdefault(character_key, [])
                canonical = next(
                    (
                        prior
                        for prior in prior_descriptions
                        if are_outfits_visually_equivalent(prior, outfit_desc)
                    ),
                    None,
                )
                if canonical is None:
                    canonical = outfit_desc
                    prior_descriptions.append(outfit_desc)
                elif canonical != outfit_desc:
                    logger.info(
                        "Reused visually equivalent character_outfit: %s=%s -> %s",
                        character_name,
                        outfit_desc,
                        canonical,
                    )
                canonical_outfits[character_name] = canonical
            scene["character_outfits"] = canonical_outfits

    def _annotate_character_names_in_text(self, value: Any, character_names: List[str]) -> str:
        text = str(value or "")
        names = sorted(
            {self._strip_character_name_markers(name) for name in character_names if self._strip_character_name_markers(name)},
            key=len,
            reverse=True,
        )
        if not text or not names:
            return text

        alternatives = "|".join(re.escape(name) for name in names)
        wrapped_pattern = re.compile(rf"[\[【]\s*({alternatives})\s*[\]】]")
        text = wrapped_pattern.sub(lambda match: f"[{match.group(1)}]", text)
        plain_pattern = re.compile(rf"(?<![\[【])({alternatives})(?![\]】])")
        return plain_pattern.sub(lambda match: f"[{match.group(1)}]", text)

    def _annotate_scene_character_names(
        self,
        scenes: List[Dict[str, Any]],
        characters: List[Dict[str, Any]],
    ) -> None:
        character_names = [
            self._strip_character_name_markers(character.get("name"))
            for character in characters or []
            if isinstance(character, dict) and self._strip_character_name_markers(character.get("name"))
        ]
        for scene in scenes or []:
            for field in (
                "description",
                "dialogue",
                "character_description",
                "voice_description",
                "camera_angle",
            ):
                scene[field] = self._annotate_character_names_in_text(
                    scene.get(field),
                    character_names,
                )

    def _normalize_outfit_compare_text(self, value: Any) -> str:
        text = self._normalize_single_line(value).lower()
        return re.sub(r"[\s,，、/|；;:：。.!！?？（）()【】\[\]\"'“”‘’\-]+", "", text)

    def _split_outfit_parts(self, value: Any) -> List[str]:
        parts: List[str] = []
        for part in re.split(r"[\s,，、/|；;:：。.!！?？（）()【】\[\]\"'“”‘’\-]+", str(value or "")):
            normalized = self._normalize_outfit_compare_text(part)
            if normalized:
                parts.append(normalized)
        return parts

    def _is_default_character_outfit(self, outfit_desc: str, character: Dict[str, Any]) -> bool:
        default_text = self._normalize_outfit_compare_text(
            "，".join(
                part
                for part in [
                    character.get('clothing'),
                    character.get('hairstyle'),
                ]
                if part
            )
        )
        outfit_text = self._normalize_outfit_compare_text(outfit_desc)
        if not default_text or not outfit_text:
            return False
        if outfit_text == default_text:
            return True

        neutral_words = {
            "穿着", "身穿", "发型", "头发", "保持", "仍是", "依然", "还是",
            "通常装扮", "默认装扮", "日常装扮", "常服", "原本装扮", "原装扮",
            "一致", "不变", "无变化", "干净", "整洁", "干净整洁",
        }
        parts = [
            part
            for part in self._split_outfit_parts(outfit_desc)
            if part not in {self._normalize_outfit_compare_text(word) for word in neutral_words}
        ]
        return bool(parts) and all(part in default_text for part in parts)

    def _filter_default_character_outfits(
        self,
        outfits: Dict[str, str],
        characters: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        if not outfits:
            return {}
        character_by_name = {
            self._normalize_single_line(character.get('name')): character
            for character in characters or []
            if isinstance(character, dict) and self._normalize_single_line(character.get('name'))
        }
        filtered: Dict[str, str] = {}
        for char_name, outfit_desc in outfits.items():
            character = character_by_name.get(char_name)
            if character and self._is_default_character_outfit(outfit_desc, character):
                logger.info("Filtered default-equivalent character_outfit: %s=%s", char_name, outfit_desc)
                continue
            filtered[char_name] = outfit_desc
        return filtered

    def _scene_text_blob(self, scene: Dict[str, Any]) -> str:
        parts: List[str] = []
        for value in (
            scene.get('scene_name'),
            scene.get('scene_state'),
            scene.get('description'),
            scene.get('character_description'),
            scene.get('dialogue'),
            scene.get('mood'),
        ):
            text = self._normalize_single_line(value)
            if text:
                parts.append(text)
        return " ".join(parts)

    def _text_contains_any_keyword(self, text: Any, keywords: List[str]) -> bool:
        normalized_text = self._normalize_single_line(text).lower()
        return bool(normalized_text) and any(keyword.lower() in normalized_text for keyword in keywords)

    def _scene_name_key_set(self, scene: Optional[Dict[str, Any]]) -> set:
        if not scene:
            return set()
        return {
            self._normalize_outfit_compare_text(part)
            for part in re.split(r"[、,，/|]+", str(scene.get('scene_name') or ""))
            if self._normalize_outfit_compare_text(part)
        }

    def _scene_has_outfit_reset_signal(self, scene: Dict[str, Any]) -> bool:
        return self._text_contains_any_keyword(
            self._scene_text_blob(scene),
            [
                "换装", "换回", "换成", "换上", "重新穿上", "重新换上", "穿回",
                "整理好", "梳好", "洗净", "洗去", "洗掉", "沐浴后", "换洗",
                "次日", "第二天", "翌日", "数日后", "几天后", "多年后", "时间跳跃",
            ],
        )

    def _outfit_has_hairstyle(self, outfit_desc: Any) -> bool:
        return self._text_contains_any_keyword(
            outfit_desc,
            [
                "发型", "头发", "刘海", "短发", "长发", "中长发", "烫发", "波浪烫",
                "寸头", "丸子头", "中分", "三七分", "背头", "光头", "高马尾",
                "马尾", "麻花辫", "双辫", "小辫子", "冲天辫", "脏辫", "爆炸头", "散发",
            ],
        )

    def _extract_hairstyle_fragment(self, outfit_desc: Any) -> str:
        fragments: List[str] = []
        for raw_part in re.split(r"[，,、；;。/]+", str(outfit_desc or "")):
            part = self._normalize_single_line(raw_part)
            if not part:
                continue
            if self._outfit_has_hairstyle(part) and part not in fragments:
                fragments.append(part)
        return "，".join(fragments[:2])

    def _scene_context_categories(self, scene: Dict[str, Any]) -> set:
        text = self._scene_text_blob(scene)
        categories = set()
        if self._text_contains_any_keyword(
            text,
            [
                "性爱", "做爱", "交欢", "缠绵", "亲热", "欢爱", "床上", "裸露",
                "裸体", "赤裸", "半裸", "内衣", "呻吟", "喘息", "抚摸", "亲吻",
                "插入", "抽插", "交合", "快感", "高潮", "情欲", "欲望", "爱液",
                "阴茎", "阴道", "乳房", "乳头", "下体", "腿间", "腰肢", "贴着",
            ],
        ):
            categories.add("intimate")
        if self._text_contains_any_keyword(
            text,
            [
                "战场", "屠杀", "厮杀", "血战", "血腥", "尸体", "血泊", "断肢",
                "追杀", "砍杀", "拼杀", "爆炸", "刀光", "箭雨", "鲜血",
            ],
        ):
            categories.add("battle")
        return categories

    def _outfit_categories(self, outfit_desc: Any) -> set:
        text = self._normalize_single_line(outfit_desc)
        categories = set()
        if self._text_contains_any_keyword(
            text,
            [
                "全裸", "裸体", "赤裸", "裸露", "半裸", "只穿内衣", "内衣", "内裤",
                "透视装", "衣衫不整", "酥胸半露", "上身全裸", "下身赤裸", "眼神迷离",
            ],
        ):
            categories.add("intimate")
        if self._text_contains_any_keyword(
            text,
            [
                "满身血污", "血污", "染血", "溅血", "衣衫破损", "破损", "战损",
                "盔甲", "甲胄", "泥污", "灰尘", "狼狈", "受伤",
            ],
        ):
            categories.add("battle")
        return categories

    def _outfit_nudity_level(self, value: Any) -> int:
        """Return a coarse, ordered exposure level for wardrobe continuity."""
        text = self._normalize_single_line(value).lower()
        if not text:
            return 0
        if self._text_contains_any_keyword(
            text,
            [
                "半裸", "上身全裸", "上身赤裸", "赤裸上身", "裸露上身",
                "下身赤裸", "赤裸下身", "裸露下身", "胸部裸露", "衣衫不整",
            ],
        ):
            return 2
        if self._text_contains_any_keyword(
            text,
            [
                "全身裸体", "全裸", "一丝不挂", "赤身裸体", "全身赤裸",
                "浑身赤裸", "正面裸体", "完全裸体",
            ],
        ):
            return 3
        if self._text_contains_any_keyword(
            text,
            [
                "只穿内衣", "只着内衣", "只穿内裤", "内衣裤", "内衣", "内裤",
                "胸罩", "文胸", "丁字裤",
            ],
        ):
            return 1
        return 0

    def _scene_nudity_level(self, scene: Optional[Dict[str, Any]]) -> int:
        if not scene:
            return 0
        values = [
            self._scene_text_blob(scene),
            " ".join(self._normalize_character_outfits(scene.get("character_outfits")).values()),
        ]
        return max(self._outfit_nudity_level(value) for value in values)

    def _text_has_explicit_dressing_completion(self, text: Any) -> bool:
        normalized = self._normalize_single_line(text)
        if not normalized:
            return False
        garment = (
            r"衣服|衣物|内衣|内裤|衬衫|上衣|外套|夹克|风衣|西装|"
            r"T恤|毛衣|针织衫|裙子|连衣裙|长裙|短裙|裤子|长裤|短裤|"
            r"睡衣|睡袍|浴袍|长袍|礼服|制服"
        )
        patterns = (
            rf"(?:穿|套|披|裹|换)(?:上|回|好).{{0,12}}(?:{garment})",
            rf"(?:把|将)?.{{0,12}}(?:{garment}).{{0,12}}(?:穿|套|披|裹|换)(?:上|回|好)",
            r"(?:扣|系|拉)(?:上|好).{0,12}(?:纽扣|扣子|腰带|系带|拉链)",
            r"(?:系紧|扣紧).{0,12}(?:衣襟|腰带|系带|纽扣|扣子)",
        )
        return any(re.search(pattern, normalized) for pattern in patterns)

    def _text_has_character_explicit_dressing_completion(
        self,
        text: Any,
        character_name: str,
        present_names: List[str],
    ) -> bool:
        normalized = self._normalize_single_line(text)
        if not self._text_has_explicit_dressing_completion(normalized):
            return False
        if not character_name or len(present_names) <= 1:
            return True
        collective_markers = ("两人", "二人", "双方", "他们", "她们", "众人", "各自")
        for clause in re.split(r"[。；;！？!?\n]+", normalized):
            if not self._text_has_explicit_dressing_completion(clause):
                continue
            if character_name in clause or f"[{character_name}]" in clause:
                return True
            if any(marker in clause for marker in collective_markers):
                return True
        return False

    def _scene_has_explicit_dressing_completion(
        self,
        scene: Optional[Dict[str, Any]],
        character_name: str = "",
    ) -> bool:
        if not scene:
            return False
        text = " ".join(
            self._normalize_single_line(scene.get(field))
            for field in ("description", "character_description")
            if self._normalize_single_line(scene.get(field))
        )
        present_names = [
            self._strip_character_name_markers(name)
            for name in (scene.get("characters_present") or [])
            if self._strip_character_name_markers(name)
        ]
        return self._text_has_character_explicit_dressing_completion(
            text,
            character_name,
            present_names,
        )

    def _text_asserts_clothed_state(self, text: Any) -> bool:
        normalized = self._normalize_single_line(text)
        if not normalized:
            return False
        garment = (
            r"衣服|衣物|内衣|内裤|衬衫|上衣|外套|夹克|风衣|西装|"
            r"T恤|毛衣|针织衫|裙子|连衣裙|长裙|短裙|裤子|长裤|短裤|"
            r"睡衣|睡袍|浴袍|长袍|礼服|制服"
        )
        return bool(
            re.search(
                rf"(?:穿着|身穿|披着|套着|裹着).{{0,16}}(?:{garment})|"
                rf"(?:{garment}).{{0,10}}(?:穿在身上|遮住身体)|"
                r"衣着整齐|穿戴整齐|衣物齐全|恢复(?:了)?完整衣着",
                normalized,
            )
        )

    def _text_asserts_character_clothed_state(
        self,
        text: Any,
        character_name: str,
        present_names: List[str],
    ) -> bool:
        normalized = self._normalize_single_line(text)
        if not self._text_asserts_clothed_state(normalized):
            return False
        if len(present_names) <= 1:
            return True
        collective_markers = ("两人", "二人", "双方", "他们", "她们", "众人", "各自")
        for clause in re.split(r"[。；;！？!?\n]+", normalized):
            if not self._text_asserts_clothed_state(clause):
                continue
            if character_name in clause or f"[{character_name}]" in clause:
                return True
            if any(marker in clause for marker in collective_markers):
                return True
        return False

    def _scene_continues_nudity_lock(
        self,
        scene: Dict[str, Any],
        previous_scene: Optional[Dict[str, Any]],
    ) -> bool:
        current_categories = self._scene_context_categories(scene)
        previous_categories = self._scene_context_categories(previous_scene or {})
        if "intimate" in current_categories and (
            "intimate" in previous_categories or self._scene_nudity_level(previous_scene) > 0
        ):
            return True
        return bool(
            self._scenes_share_continuity_context(scene, previous_scene)
            and self._scene_has_continuation_signal(scene)
        )

    def _scene_has_continuation_signal(self, scene: Dict[str, Any]) -> bool:
        return self._text_contains_any_keyword(
            self._scene_text_blob(scene),
            [
                "继续", "持续", "仍然", "依旧", "依然", "愈发", "越发", "越来越",
                "没有停", "不停", "节奏", "喘息", "气息", "呼吸", "抱住", "贴着",
                "缠住", "依偎", "相拥", "抓出", "压住", "俯身", "顺着", "起伏",
                "扭动", "迎合",
            ],
        )

    def _scenes_share_continuity_context(
        self,
        scene: Optional[Dict[str, Any]],
        other_scene: Optional[Dict[str, Any]],
    ) -> bool:
        if not scene or not other_scene:
            return False
        if self._scene_name_key_set(scene) & self._scene_name_key_set(other_scene):
            return True
        time_a = self._normalize_single_line(scene.get('time_of_day'))
        time_b = self._normalize_single_line(other_scene.get('time_of_day'))
        weather_a = self._normalize_single_line(scene.get('weather'))
        weather_b = self._normalize_single_line(other_scene.get('weather'))
        return bool(time_a and weather_a and time_a == time_b and weather_a == weather_b)

    def _effective_scene_context_categories(
        self,
        scene: Dict[str, Any],
        prev_scene: Optional[Dict[str, Any]] = None,
        next_scene: Optional[Dict[str, Any]] = None,
        prev_outfit: str = "",
        next_outfit: str = "",
    ) -> set:
        categories = set(self._scene_context_categories(scene))
        if categories or self._scene_has_outfit_reset_signal(scene):
            return categories

        prev_scene_categories = self._scene_context_categories(prev_scene or {})
        next_scene_categories = self._scene_context_categories(next_scene or {})
        prev_outfit_categories = self._outfit_categories(prev_outfit)
        next_outfit_categories = self._outfit_categories(next_outfit)
        has_continuation_signal = self._scene_has_continuation_signal(scene)

        if has_continuation_signal and prev_outfit_categories and self._scenes_share_continuity_context(scene, prev_scene):
            categories |= (prev_scene_categories | prev_outfit_categories)
        if has_continuation_signal and next_outfit_categories and self._scenes_share_continuity_context(scene, next_scene):
            categories |= (next_scene_categories | next_outfit_categories)

        if not categories and self._scenes_share_continuity_context(scene, prev_scene) and self._scenes_share_continuity_context(scene, next_scene):
            categories |= (prev_scene_categories | prev_outfit_categories) & (next_scene_categories | next_outfit_categories)

        return categories

    def _ensure_outfit_includes_hairstyle(
        self,
        outfit_desc: str,
        character: Optional[Dict[str, Any]],
        previous_outfit: str = "",
        next_outfit: str = "",
    ) -> str:
        outfit = self._normalize_single_line(outfit_desc)
        if not outfit or self._outfit_has_hairstyle(outfit):
            return outfit

        hairstyle_fragment = self._extract_hairstyle_fragment(previous_outfit)
        if not hairstyle_fragment:
            hairstyle_fragment = self._extract_hairstyle_fragment(next_outfit)
        if not hairstyle_fragment and character:
            hairstyle_fragment = self._normalize_single_line(character.get('hairstyle'))

        if not hairstyle_fragment or hairstyle_fragment in outfit:
            return outfit
        return f"{outfit}，{hairstyle_fragment}"

    def _enforce_character_outfit_continuity(
        self,
        scenes: List[Dict[str, Any]],
        characters: List[Dict[str, Any]],
    ) -> None:
        if not scenes:
            return

        character_by_name = {
            self._normalize_single_line(character.get('name')): character
            for character in characters or []
            if isinstance(character, dict) and self._normalize_single_line(character.get('name'))
        }
        active_nudity_outfits: Dict[str, str] = {}

        for idx, scene in enumerate(scenes):
            current_outfits = self._normalize_character_outfits(scene.get('character_outfits'))
            has_reset_signal = self._scene_has_outfit_reset_signal(scene)
            present_names = [
                self._normalize_single_line(name)
                for name in (scene.get('characters_present') or [])
                if self._normalize_single_line(name)
            ]

            prev_scene = scenes[idx - 1] if idx > 0 else None
            next_scene = scenes[idx + 1] if idx + 1 < len(scenes) else None
            prev_outfits = self._normalize_character_outfits(prev_scene.get('character_outfits')) if prev_scene else {}
            next_outfits = self._normalize_character_outfits(next_scene.get('character_outfits')) if next_scene else {}
            next_present = {
                self._normalize_single_line(name)
                for name in ((next_scene or {}).get('characters_present') or [])
                if self._normalize_single_line(name)
            }

            for char_name in present_names:
                character = character_by_name.get(char_name)
                prev_outfit = prev_outfits.get(char_name, "")
                next_outfit = next_outfits.get(char_name, "") if char_name in next_present else ""
                current_outfit = current_outfits.get(char_name, "")
                current_categories = self._effective_scene_context_categories(
                    scene,
                    prev_scene=prev_scene,
                    next_scene=next_scene,
                    prev_outfit=prev_outfit,
                    next_outfit=next_outfit,
                )

                if current_outfit:
                    current_outfit = self._ensure_outfit_includes_hairstyle(
                        current_outfit,
                        character,
                        previous_outfit=prev_outfit,
                        next_outfit=next_outfit,
                    )
                    current_outfits[char_name] = current_outfit

                locked_outfit = active_nudity_outfits.get(char_name, "")
                if not locked_outfit and self._outfit_nudity_level(prev_outfit) > 0:
                    locked_outfit = prev_outfit
                current_dressing_completion = self._scene_has_explicit_dressing_completion(
                    scene,
                    char_name,
                )
                continues_nudity = bool(
                    locked_outfit
                    and (
                        self._scene_continues_nudity_lock(scene, prev_scene)
                        or current_dressing_completion
                    )
                    and not self._scene_has_explicit_dressing_completion(prev_scene, char_name)
                )
                if continues_nudity and (
                    not current_outfit
                    or self._outfit_nudity_level(current_outfit)
                    < self._outfit_nudity_level(locked_outfit)
                ):
                    current_outfits[char_name] = self._ensure_outfit_includes_hairstyle(
                        locked_outfit,
                        character,
                        previous_outfit=prev_outfit,
                        next_outfit=next_outfit,
                    )
                    current_outfit = current_outfits[char_name]
                    logger.warning(
                        "Preserved active nudity outfit continuity in scene %s: %s=%s",
                        scene.get("scene_number", idx + 1),
                        char_name,
                        current_outfit,
                    )

                if not current_outfit and not has_reset_signal:
                    prev_categories = self._outfit_categories(prev_outfit)
                    next_categories = self._outfit_categories(next_outfit)

                    inherited_outfit = ""
                    if prev_outfit and current_categories & prev_categories:
                        inherited_outfit = prev_outfit
                    elif prev_outfit and next_outfit and prev_categories and next_categories and prev_categories & next_categories:
                        inherited_outfit = prev_outfit
                    elif next_outfit and current_categories & next_categories:
                        inherited_outfit = next_outfit

                    if inherited_outfit:
                        current_outfits[char_name] = self._ensure_outfit_includes_hairstyle(
                            inherited_outfit,
                            character,
                            previous_outfit=prev_outfit,
                            next_outfit=next_outfit,
                        )
                        current_outfit = current_outfits[char_name]

                final_outfit = current_outfits.get(char_name, "")
                final_nudity_level = self._outfit_nudity_level(final_outfit)
                if current_dressing_completion:
                    active_nudity_outfits.pop(char_name, None)
                elif final_nudity_level > 0 and (
                    "intimate" in current_categories or self._scene_nudity_level(scene) > 0
                ):
                    active_nudity_outfits[char_name] = final_outfit
                elif locked_outfit and continues_nudity:
                    active_nudity_outfits[char_name] = locked_outfit
                elif has_reset_signal or not self._scenes_share_continuity_context(scene, prev_scene):
                    active_nudity_outfits.pop(char_name, None)

            scene['character_outfits'] = self._filter_default_character_outfits(current_outfits, characters)

    def _coerce_scene_text_field(self, value: Any) -> str:
        """将分镜的 character_description / voice_description 归一化为字符串。

        模型有时会把这类字段输出成按角色名分组的 dict（如
        {"小悠": "穿着黑色...", "阿强": "..."}）或 list，需拍平成
        "角色名：描述" 的多行字符串，避免 Scene 校验因类型不符失败。
        """
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            parts = []
            for name, desc in value.items():
                name_str = self._normalize_single_line(name)
                desc_str = self._normalize_single_line(desc)
                if name_str and desc_str:
                    parts.append(f"{name_str}：{desc_str}")
                elif desc_str:
                    parts.append(desc_str)
            return "\n".join(parts)
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("character") or item.get("role")
                    desc = item.get("description") or item.get("text") or item.get("action") or item.get("voice")
                    name_str = self._normalize_single_line(name)
                    desc_str = self._normalize_single_line(desc)
                    if name_str and desc_str:
                        parts.append(f"{name_str}：{desc_str}")
                    elif desc_str:
                        parts.append(desc_str)
                    else:
                        parts.append(self._normalize_single_line(item))
                else:
                    line = self._normalize_single_line(item)
                    if line:
                        parts.append(line)
            return "\n".join(parts)
        return str(value)

    def _normalize_dialogue_field(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, dict):
                    char_name = item.get('character') or item.get('name') or item.get('role') or ''
                    text = item.get('text') or item.get('dialogue') or item.get('line') or ''
                    char_name = self._normalize_single_line(char_name)
                    text = self._normalize_single_line(text)
                    if char_name and text:
                        parts.append(f"{char_name}：{text}")
                    elif text:
                        parts.append(text)
                elif item is not None:
                    line = self._normalize_single_line(item)
                    if line:
                        parts.append(line)
            return "\n".join(parts)
        return str(value or '').strip()

    def _normalize_dialogue_speaker(self, value: Any) -> str:
        speaker = self._normalize_single_line(value)
        if not speaker:
            return ""
        action_markers = [
            "低声", "轻声", "大声", "站在", "坐在", "靠在", "走到", "转身",
            "抬头", "回头", "说", "问", "喊", "回答", "道", "叫住",
        ]
        for marker in action_markers:
            index = speaker.find(marker)
            if index > 0:
                speaker = speaker[:index]
                break
        return speaker.strip(" ，,；;：:")

    def _move_dialogue_out_of_description(self, scene: Dict[str, Any]) -> None:
        """把模型误写进 description 的直接对白迁移到 dialogue。

        description 只应描述画面/动作；直接对白统一放入 dialogue，便于 UI 和视频 prompt 使用。
        """
        description = str(scene.get('description') or '')
        if not description:
            return

        extracted: List[str] = []

        patterns = [
            re.compile(r'(?P<speaker>[\u4e00-\u9fffA-Za-z0-9_·]{1,12})\s*[：:]\s*[“"「](?P<text>[^”"」]{1,120})[”"」]'),
            re.compile(r'(?P<speaker>[\u4e00-\u9fffA-Za-z0-9_·]{1,12})\s*(?:低声说|轻声说|大喊|喊道|说道|说|问道|问|回答|道|叫住|喊)\s*[：:]?\s*[“"「](?P<text>[^”"」]{1,120})[”"」]'),
        ]

        cleaned = description
        for pattern in patterns:
            for match in list(pattern.finditer(cleaned)):
                speaker = self._normalize_dialogue_speaker(match.group('speaker'))
                text = self._normalize_single_line(match.group('text'))
                if speaker and text:
                    extracted.append(f"{speaker}：{text}")
            cleaned = pattern.sub('', cleaned)

        if not extracted:
            return

        cleaned = re.sub(r'\s+', ' ', cleaned)
        cleaned = re.sub(r'[，,；;：:]\s*([。！？!?])', r'\1', cleaned)
        cleaned = re.sub(r'\s*([。！？!?])\s*', r'\1', cleaned).strip(' ，,；;。')

        existing_dialogue = self._normalize_dialogue_field(scene.get('dialogue'))
        existing_lines = [line.strip() for line in existing_dialogue.splitlines() if line.strip()]
        existing_text = "\n".join(existing_lines)
        for line in extracted:
            spoken_text = line.split('：', 1)[-1]
            if line not in existing_lines and spoken_text not in existing_text:
                existing_lines.append(line)

        scene['description'] = cleaned
        scene['dialogue'] = "\n".join(existing_lines)

    def _infer_time_of_day_from_text(self, *texts: Any) -> str:
        content = " ".join(self._normalize_single_line(text) for text in texts if self._normalize_single_line(text)).lower()
        if not content:
            return ""

        keyword_groups = [
            ("凌晨", ["凌晨", "拂晓前", "凌晨时分", "before dawn"]),
            ("清晨", ["清晨", "早晨", "晨光", "黎明", "破晓", "morning", "dawn", "sunrise"]),
            ("中午", ["中午", "午后", "正午", "晌午", "noon", "midday", "afternoon"]),
            ("黄昏", ["黄昏", "傍晚", "日落", "暮色", "晚霞", "dusk", "sunset", "twilight"]),
            ("夜晚", ["深夜", "夜晚", "夜里", "晚上", "午夜", "霓虹夜景", "night", "midnight", "evening"]),
            ("白天", ["白天", "白昼", "日间", " daytime", "daytime", "day "]),
        ]
        for label, keywords in keyword_groups:
            if any(keyword in content for keyword in keywords):
                return label
        return ""

    def _infer_weather_from_text(self, *texts: Any) -> str:
        content = " ".join(self._normalize_single_line(text) for text in texts if self._normalize_single_line(text)).lower()
        if not content:
            return ""

        keyword_groups = [
            ("暴雨", ["暴雨", "大雨", "雷雨", "storm", "thunderstorm", "heavy rain"]),
            ("雨天", ["雨", "下雨", "雨夜", "阴雨", "rain", "rainy", "drizzle"]),
            ("雪天", ["雪", "下雪", "暴雪", "snow", "snowy", "blizzard"]),
            ("雾天", ["雾", "迷雾", "浓雾", "fog", "foggy", "mist"]),
            ("晴天", ["晴", "晴朗", "阳光", "日光", "sunny", "clear sky", "clear day"]),
            ("阴天", ["阴天", "多云", "cloudy", "overcast"]),
            ("大风", ["大风", "狂风", "风沙", "windy", "gale"]),
        ]
        for label, keywords in keyword_groups:
            if any(keyword in content for keyword in keywords):
                return label
        return ""

    def generate_script(
        self,
        user_input: str,
        reference_images: List[str] = None,
        uploaded_reference_images: List[Any] = None,
        audio_text: str = None,
        style_hint: str = None,
        output_language: str = "zh-CN"
    ) -> Script:
        """
        生成剧本和分镜

        根据chatbot用户补充的描述信息与最初的用户输入，包括文字/参考图片/音频ASR解析出的文字，
        生成角色、剧本、角色音色、对话等分镜脚本。

        支持从用户输入中提取视频时长信息，如果没有则使用yaml中的默认设置。

        Args:
            user_input: 用户输入文本（包含最初输入和补充描述，合并后的完整信息）
            reference_images: 参考图片URL列表
            audio_text: 音频转文字内容
            style_hint: 风格提示

        Returns:
            剧本对象
        """
        logger.log_agent_call("ScriptAgent", "generate_script", {
            "user_input": user_input[:200] if user_input else "",
            "has_images": bool(reference_images),
            "has_audio": bool(audio_text),
            "style_hint": style_hint
        })

        if not self.model:
            raise ValueError("Missing required config: models.script.endpoint")

        # 从用户输入中提取风格信息
        extracted_style = self._extract_style_from_input(user_input)
        if extracted_style:
            style_hint = extracted_style
            logger.info(f"Extracted style from user input: {style_hint}")

        # 从用户输入中提取视频时长信息
        extracted_duration = self._extract_duration_from_input(user_input)

        target_total_duration = self.default_total_duration
        if extracted_duration:
            # 验证最小值限制（使用yaml配置的最小值）
            if extracted_duration >= self.total_duration_min:
                target_total_duration = extracted_duration
                logger.info(f"Extracted duration from user input: {extracted_duration} seconds")
            else:
                target_total_duration = self.total_duration_min
                logger.warning(f"Extracted duration {extracted_duration}s is less than minimum {self.total_duration_min}s, using minimum")
        else:
            # 使用yaml中的默认设置（已在__init__中读取）
            logger.info(f"Using default duration from config: {target_total_duration} seconds")

        prompt = self._build_prompt(
            user_input=user_input,
            audio_text=audio_text,
            style_hint=style_hint,
            reference_images=reference_images,
            uploaded_reference_images=uploaded_reference_images,
            output_language=output_language,
            total_duration=target_total_duration,
        )

        # 构建消息
        messages = [
            {
                "role": "system",
                "content": self._get_system_prompt(output_language, target_total_duration, user_input)
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        # 如果有参考图片，在提示词中添加图片URL信息
        if reference_images:
            img_info = "\n\n[用户上传的参考图片]\n"
            for i, img_url in enumerate(reference_images[:3], 1):  # 最多3张参考图
                img_info += f"参考图{i}: {img_url}\n"
            img_info += "\n请根据这些参考图片中的人物形象来创作剧本中的角色。"

            # 将图片信息追加到文本内容中
            if isinstance(messages[1]["content"], str):
                messages[1]["content"] += img_info
            else:
                # 如果content已经是列表格式，先转换为字符串
                text_parts = []
                for item in messages[1]["content"]:
                    if item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                messages[1]["content"] = "\n".join(text_parts) + img_info

        # 调用大模型 - seed-sc-off，使用 300 秒超时
        logger.info(f"Calling seed-sc-off for script generation with timeout {self.timeout}s")
        max_attempts = 2
        script_data = None
        for attempt in range(1, max_attempts + 1):
            response = llm_service.chat_completion(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.timeout
            )

            content = response['choices'][0]['message']['content']
            self._log_raw_model_response("generate_script", attempt, content)
            script_data = self._parse_script(content)
            names_ok = self._validate_uploaded_reference_name_usage(script_data, uploaded_reference_images)
            quality_ok = self._is_script_quality_acceptable(script_data, user_input)
            if quality_ok and names_ok:
                break

            if attempt < max_attempts:
                logger.warning(
                    f"Script quality check failed on attempt {attempt}/{max_attempts}; requesting targeted revision"
                )
                feedback = self._collect_script_quality_feedback(script_data, user_input)
                if not names_ok:
                    feedback.append("用户上传参考图的锁定角色名或布景名未被完整保留和引用。")
                messages.extend([
                    {
                        "role": "assistant",
                        "content": self._serialize_script_for_revision(script_data),
                    },
                    {
                        "role": "user",
                        "content": self._build_quality_revision_request(feedback),
                    },
                ])

        logger.info(f"Script generated with {len(script_data['scenes'])} scenes")
        logger.info(f"Script style: {script_data.get('style', 'Not specified')}")

        # 在合法区间内微调各分镜时长，使总时长尽量贴近用户指定/默认的目标秒数。
        self._fit_scene_durations_to_target(script_data['scenes'], target_total_duration)
        logger.info(
            f"Script total duration fitted to {sum(int(s.get('duration', 0)) for s in script_data['scenes'])}s "
            f"(target {target_total_duration}s)"
        )

        # 构建Script对象
        characters = [Character(**c) for c in script_data['characters']]
        scene_definitions = [SceneDefinition(**item) for item in script_data.get('scene_definitions', [])]
        scenes = [Scene(**s) for s in script_data['scenes']]

        script = Script(
            title=script_data['title'],
            style=script_data['style'],
            era=script_data.get('era'),
            background=script_data['background'],
            tone=script_data.get('tone'),
            characters=characters,
            scene_definitions=scene_definitions,
            scenes=scenes,
            total_duration=sum(s.duration for s in scenes)
        )

        return script

    def rewrite_script(
        self,
        existing_script: Script,
        edit_request: str,
        reference_images: List[str] = None,
        uploaded_reference_images: List[Any] = None,
        audio_text: str = None,
        output_language: str = "zh-CN"
    ) -> Script:
        """基于上一版完整剧本和用户修改要求，重写整份分镜脚本。"""
        logger.log_agent_call("ScriptAgent", "rewrite_script", {
            "title": existing_script.title,
            "scene_count": len(existing_script.scenes),
            "edit_request": edit_request[:200] if edit_request else "",
            "has_images": bool(reference_images),
            "has_audio": bool(audio_text),
        })

        if not self.model:
            raise ValueError("Missing required config: models.script.endpoint")

        total_duration = self._bounded_total_duration(
            existing_script.total_duration or self.default_total_duration
        )
        previous_script_json = json.dumps(existing_script.dict(), ensure_ascii=False, indent=2)

        prompt_parts = [
            render_prompt(
                "script_rewrite_user.md",
                total_duration=total_duration,
                scene_duration_min=self.scene_duration_min,
                scene_duration_max=self.scene_duration_max,
                previous_script_json=previous_script_json,
                edit_request=edit_request,
            )
        ]

        if audio_text:
            prompt_parts.extend(["", "【语音补充】", audio_text])

        if reference_images:
            prompt_parts.append("")
            prompt_parts.append(f"【参考图片】用户提供了{len(reference_images)}张参考图片，请继续参考其中的人物/角色形象。")
            for i, img_url in enumerate(reference_images[:3], 1):
                prompt_parts.append(f"参考图{i}: {img_url}")

        uploaded_reference_prompt = self._build_uploaded_reference_name_prompt(uploaded_reference_images)
        if uploaded_reference_prompt:
            prompt_parts.extend(["", uploaded_reference_prompt])

        self._append_script_private_extensions(prompt_parts, edit_request, audio_text, previous_script_json)

        messages = [
            {
                "role": "system",
                "content": self._get_system_prompt(output_language, total_duration, edit_request, audio_text)
                + "\n\n【改稿规则】当用户要求修改剧本时，你必须基于上一版完整剧本进行重写，输出一份新的完整 JSON。"
            },
            {
                "role": "user",
                "content": "\n".join(prompt_parts)
            }
        ]

        max_attempts = 2
        script_data = None
        quality_input = f"{existing_script.title}\n{existing_script.style}\n{edit_request}"
        for attempt in range(1, max_attempts + 1):
            response = llm_service.chat_completion(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.timeout
            )

            content = response['choices'][0]['message']['content']
            self._log_raw_model_response("rewrite_script", attempt, content)
            script_data = self._parse_script(content)
            names_ok = self._validate_uploaded_reference_name_usage(script_data, uploaded_reference_images)
            quality_ok = self._is_script_quality_acceptable(script_data, quality_input)
            if quality_ok and names_ok:
                break

            if attempt < max_attempts:
                logger.warning(
                    f"Script rewrite quality check failed on attempt {attempt}/{max_attempts}; requesting targeted revision"
                )
                feedback = self._collect_script_quality_feedback(script_data, quality_input)
                if not names_ok:
                    feedback.append("用户上传参考图的锁定角色名或布景名未被完整保留和引用。")
                messages.extend([
                    {
                        "role": "assistant",
                        "content": self._serialize_script_for_revision(script_data),
                    },
                    {
                        "role": "user",
                        "content": self._build_quality_revision_request(feedback),
                    },
                ])

        logger.info(f"Script rewritten with {len(script_data['scenes'])} scenes")

        # 与首次生成一致：改稿后同样把总时长微调贴近目标秒数。
        self._fit_scene_durations_to_target(script_data['scenes'], total_duration)

        characters = [Character(**c) for c in script_data['characters']]
        scene_definitions = [SceneDefinition(**item) for item in script_data.get('scene_definitions', [])]
        scenes = [Scene(**s) for s in script_data['scenes']]

        return Script(
            title=script_data['title'],
            style=script_data['style'],
            era=script_data.get('era'),
            background=script_data['background'],
            tone=script_data.get('tone'),
            characters=characters,
            scene_definitions=scene_definitions,
            scenes=scenes,
            total_duration=sum(s.duration for s in scenes)
        )

    def _extract_style_from_input(self, user_input: str) -> Optional[str]:
        """
        从用户输入中提取风格信息

        识别关键词如：水墨画风、赛博朋克、温馨治愈、黑白、复古等
        """
        if not user_input:
            return None

        # 风格关键词映射
        style_keywords = [
            "水墨", "油画", "水彩", "素描", "卡通", "动漫", "写实", "抽象",
            "赛博朋克", "蒸汽波", "复古", "怀旧", "未来", "科幻",
            "温馨", "治愈", "悬疑", "恐怖", "搞笑", "轻松", "史诗", "宏大",
            "黑白", "彩色", "暖色调", "冷色调", "暗调", "明亮",
            "中国风", "日式", "欧美", "韩式",
            "电影感", "纪录片", "MV", "广告",
            "皮影戏", "剪纸", "泥塑", "木偶"
        ]

        found_styles = []
        for keyword in style_keywords:
            if keyword in user_input:
                found_styles.append(keyword)

        if found_styles:
            return "、".join(found_styles)
        return None

    def _extract_duration_from_input(self, user_input: str) -> Optional[int]:
        """
        从用户输入中提取视频时长信息

        识别格式如："时长30秒"、"30秒"、"1分钟"、"时长：60秒"等
        返回秒数，如果没有找到则返回None

        示例：
        - "将诗词...时长30秒" → 30
        - "生成一个1分钟的视频" → 60
        - "时长：90秒" → 90
        """
        if not user_input:
            return None

        # 匹配模式：
        # 1. "时长30秒"、"时长 30秒"、"时长：30秒"
        # 2. "30秒"（前后有分隔符或空格）
        # 3. "1分钟"、"2分钟"
        # 4. "时长1分30秒"

        # 匹配 "时长" 开头的格式
        duration_pattern1 = r'时长[：:]?\s*(\d+)\s*秒'
        match1 = re.search(duration_pattern1, user_input)
        if match1:
            seconds = int(match1.group(1))
            if 1 <= seconds <= self.total_duration_max:
                return seconds

        # 匹配 "时长" + 分钟格式
        duration_pattern2 = r'时长[：:]?\s*(\d+)\s*分(?:钟)?(?:\s*(\d+)\s*秒)?'
        match2 = re.search(duration_pattern2, user_input)
        if match2:
            minutes = int(match2.group(1))
            seconds = int(match2.group(2)) if match2.group(2) else 0
            total_seconds = minutes * 60 + seconds
            if 1 <= total_seconds <= self.total_duration_max:
                return total_seconds

        # 匹配独立的 "X秒" 格式（确保前面没有"时长"字样，避免重复匹配）
        duration_pattern3 = r'(?:^|[，。；！？\s])(\d+)\s*秒(?:钟)?(?:[，。；！？\s]|$)'
        match3 = re.search(duration_pattern3, user_input)
        if match3:
            seconds = int(match3.group(1))
            if 1 <= seconds <= self.total_duration_max:
                return seconds

        # 匹配独立的 "X分钟" 格式
        duration_pattern4 = r'(?:^|[，。；！？\s])(\d+)\s*分(?:钟)?(?:[，。；！？\s]|$)'
        match4 = re.search(duration_pattern4, user_input)
        if match4:
            minutes = int(match4.group(1))
            total_seconds = minutes * 60
            if 1 <= total_seconds <= self.total_duration_max:
                return total_seconds

        return None

    def _get_system_prompt(
        self,
        output_language: str = "zh-CN",
        total_duration: Optional[int] = None,
        *trigger_texts: Any,
    ) -> str:
        """获取系统提示词"""
        effective_total_duration = self._bounded_total_duration(
            total_duration or self.default_total_duration
        )
        prompt_parts = [render_prompt(
            "script_system_prompt.md",
            effective_total_duration=effective_total_duration,
            total_duration_max=self.total_duration_max,
            scene_duration_min=self.scene_duration_min,
            scene_duration_max=self.scene_duration_max,
            disallowed_duration_examples=self._disallowed_duration_examples(),
            max_storyboard_scenes=self.max_storyboard_scenes,
            max_characters=self.max_characters,
            max_setting_definitions=self.max_setting_definitions,
            output_language_rule=translate(
                output_language,
                'script.output_language_rule',
                language=language_name(output_language),
            ),
        )]
        return "\n\n".join(part for part in prompt_parts if part)

    def _build_prompt(
        self,
        user_input: str,
        audio_text: str = None,
        style_hint: str = None,
        reference_images: List[str] = None,
        uploaded_reference_images: List[Any] = None,
        output_language: str = "zh-CN",
        total_duration: Optional[int] = None
    ) -> str:
        """构建用户提示词"""
        effective_total_duration = self._bounded_total_duration(
            total_duration or self.default_total_duration
        )
        prompt_parts = []

        prompt_parts.append("请根据以下信息生成一份完整、可执行的剧本 JSON。")

        # 【最高优先级】直接使用用户输入
        prompt_parts.append(f"\n【用户需求】\n{user_input}")
        prompt_parts.append("[RULE] 以上文本是用户的原始输入，包含风格、布景、氛围、题材与限制条件。")
        prompt_parts.append("[RULE] 你必须优先遵循用户原始输入中的风格、时长、布景、题材和限制要求。")

        if audio_text:
            prompt_parts.append(f"\n【语音补充】\n{audio_text}")

        if style_hint:
            prompt_parts.append("\n【风格要求】")
            prompt_parts.append(f'- style 必须完全等于 "{style_hint}"')
            prompt_parts.append("- 不得改写、补充、混搭或解释该风格")
        else:
            prompt_parts.append("\n【风格要求】")
            prompt_parts.append("用户未指定风格，请根据故事内容自动生成合适的风格")

        if reference_images:
            prompt_parts.append(f"\n【参考图片】\n用户提供了{len(reference_images)}张参考图片，请根据图片内容理解用户的视觉风格意图")

        uploaded_reference_prompt = self._build_uploaded_reference_name_prompt(uploaded_reference_images)
        if uploaded_reference_prompt:
            prompt_parts.append(f"\n{uploaded_reference_prompt}")

        average_scene_duration = max(
            1,
            (self.scene_duration_min + self.scene_duration_max) // 2,
        )
        estimated_scene_count = min(
            self.max_storyboard_scenes,
            max(1, (effective_total_duration + average_scene_duration - 1) // average_scene_duration),
        )
        prompt_parts.append("\n【动态执行参数】")
        prompt_parts.append(f"- 目标总时长：约{effective_total_duration}秒；分镜总时长要尽量贴近该值，且不得超过{self.total_duration_max}秒。")
        prompt_parts.append(f"- 分镜时长：每个 duration 必须是 {self.scene_duration_min}-{self.scene_duration_max} 秒之间的整数，禁止输出 {self._disallowed_duration_examples()} 或更大值。")
        prompt_parts.append("- 时长分配：先按动作节点、出场人数、信息量、空间调度和情绪转折评估内容丰富度；不同丰富度的分镜不得同长，可行时最长与最短至少相差2秒。")
        prompt_parts.append("- 细节密度：5-9秒至少2个连续秒段，10-18秒至少3段，19-30秒至少4段；每段必须有环境空间、人物动作与细微表演、镜头、光影及明确结果。")
        prompt_parts.append(f"- 建议分镜数量：约{estimated_scene_count}个；角色最多{self.max_characters}个，布景最多{self.max_setting_definitions}个，分镜最多{self.max_storyboard_scenes}个。")
        prompt_parts.append("- 详细结构、字段顺序、角色/布景/对白/装扮/布景状态/去重/转场规则以 system prompt 为准，不在此重复。")
        self._append_script_private_extensions(prompt_parts, user_input, audio_text)

        # 检查用户是否指定了对话/旁白生成方式
        if user_input:
            if '不生成旁白' in user_input or '不要旁白' in user_input or '只生成对话' in user_input:
                prompt_parts.append("\n【对话/旁白生成规则 - 强制】")
                prompt_parts.append("- 用户明确要求：不生成旁白，只生成对话")
                prompt_parts.append("- dialogue字段必须是对话形式，格式如：\"[角色名]：对话内容\"")
                prompt_parts.append("- 禁止生成纯旁白描述，所有文本必须是角色对话")
                prompt_parts.append("- 如果没有对话的场景，dialogue字段可以为空字符串")
            elif '不生成对话' in user_input or '不要对话' in user_input or '只生成旁白' in user_input:
                prompt_parts.append("\n【对话/旁白生成规则 - 强制】")
                prompt_parts.append("- 用户明确要求：不生成对话，只生成旁白")
                prompt_parts.append("- dialogue字段必须是旁白形式，用于叙述场景和推动剧情")
                prompt_parts.append("- 禁止生成角色对话")

        style_output_rule = ""
        if style_hint:
            style_output_rule = (
                f'【强制】style字段必须完全等于："{style_hint}"\n'
                f'【强制】禁止添加任何其他文字到style字段'
            )
        prompt_parts.append("\n【输出要求】")
        if style_output_rule:
            prompt_parts.append(style_output_rule)
        prompt_parts.append(
            translate(
                output_language,
                'script.output_language_rule',
                language=language_name(output_language),
            )
        )
        prompt_parts.append("请直接输出完整 JSON，不要包含解释、前言或 Markdown 代码块。")

        return "\n".join(prompt_parts)

    def _normalize_uploaded_reference_name(self, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip())

    def _normalize_uploaded_reference_key(self, value: Any) -> str:
        normalized = self._normalize_uploaded_reference_name(value).lower()
        normalized = re.sub(r"\s+", "", normalized)
        normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff_-]+", "", normalized)
        return normalized

    def _collect_uploaded_reference_names(self, uploaded_reference_images: List[Any] = None) -> Dict[str, List[str]]:
        locked_names = {"character": [], "scene": []}
        seen = {"character": set(), "scene": set()}
        for item in uploaded_reference_images or []:
            reference_type = str(
                getattr(item, "reference_type", None)
                if not isinstance(item, dict)
                else item.get("reference_type")
            ).strip().lower()
            if reference_type not in {"character", "scene"}:
                continue
            raw_name = (
                getattr(item, "name", None)
                if not isinstance(item, dict)
                else item.get("name")
            )
            name = self._normalize_uploaded_reference_name(raw_name)
            key = self._normalize_uploaded_reference_key(name)
            if not key or key in seen[reference_type]:
                continue
            seen[reference_type].add(key)
            locked_names[reference_type].append(name)
        return locked_names

    def _build_uploaded_reference_name_prompt(self, uploaded_reference_images: List[Any] = None) -> str:
        locked_names = self._collect_uploaded_reference_names(uploaded_reference_images)
        character_names = locked_names["character"]
        scene_names = locked_names["scene"]
        if not character_names and not scene_names:
            return ""

        prompt_parts = [
            "【用户上传参考图名称约束】",
            "- 以下名称来自用户上传的参考图，属于锁定名称，必须原样保留，不得改名、缩写、翻译、替换别名或改成其他称呼。",
            "- 用户上传的人物/角色名称，必须直接用于 characters.name，并在相关分镜的 characters_present 中原样出现。",
            "- 用户上传的布景名称，必须直接用于 scene_definitions.name，并在相关分镜的 scene_name 中原样出现。",
            "- 除了这些锁定名称外，你可以根据剧情补充其他角色和布景，但总数仍必须遵守上限。",
        ]
        if character_names:
            prompt_parts.append(f"- 锁定角色名称：{', '.join(character_names)}")
        if scene_names:
            prompt_parts.append(f"- 锁定布景名称：{', '.join(scene_names)}")
        return "\n".join(prompt_parts)

    def _split_scene_name_parts(self, scene_name: Any) -> List[str]:
        return [
            part.strip()
            for part in re.split(r"[、,，/|]+", str(scene_name or ""))
            if part.strip()
        ]

    def _validate_uploaded_reference_name_usage(
        self,
        script_data: Dict[str, Any],
        uploaded_reference_images: List[Any] = None,
    ) -> bool:
        locked_names = self._collect_uploaded_reference_names(uploaded_reference_images)
        locked_character_names = locked_names["character"]
        locked_scene_names = locked_names["scene"]
        if not locked_character_names and not locked_scene_names:
            return True

        character_defs = {
            self._normalize_uploaded_reference_key(item.get("name")): str(item.get("name") or "").strip()
            for item in (script_data.get("characters") or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        scene_defs = {
            self._normalize_uploaded_reference_key(item.get("name")): str(item.get("name") or "").strip()
            for item in (script_data.get("scene_definitions") or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }

        scene_character_usage = set()
        scene_name_usage = set()
        for scene in script_data.get("scenes") or []:
            if not isinstance(scene, dict):
                continue
            for name in scene.get("characters_present") or []:
                key = self._normalize_uploaded_reference_key(name)
                if key:
                    scene_character_usage.add(key)
            for part in self._split_scene_name_parts(scene.get("scene_name")):
                key = self._normalize_uploaded_reference_key(part)
                if key:
                    scene_name_usage.add(key)

        missing_character_defs = [
            name for name in locked_character_names
            if self._normalize_uploaded_reference_key(name) not in character_defs
        ]
        missing_character_usage = [
            name for name in locked_character_names
            if self._normalize_uploaded_reference_key(name) not in scene_character_usage
        ]
        missing_scene_defs = [
            name for name in locked_scene_names
            if self._normalize_uploaded_reference_key(name) not in scene_defs
        ]
        missing_scene_usage = [
            name for name in locked_scene_names
            if self._normalize_uploaded_reference_key(name) not in scene_name_usage
        ]

        if missing_character_defs or missing_character_usage or missing_scene_defs or missing_scene_usage:
            logger.warning(
                "Script quality check failed: uploaded reference names were not preserved. "
                "missing_character_defs=%s missing_character_usage=%s missing_scene_defs=%s missing_scene_usage=%s",
                missing_character_defs,
                missing_character_usage,
                missing_scene_defs,
                missing_scene_usage,
            )
            return False
        return True

    def _extract_description_timeline_segments(self, description: Any) -> List[Dict[str, Any]]:
        text = str(description or "")
        matches = list(re.finditer(
            r"(?P<start>\d+(?:\.\d+)?)\s*(?:-|–|—|~|～|至|到)\s*"
            r"(?P<end>\d+(?:\.\d+)?)\s*(?:秒|seconds?|secs?|s|segundos?)\s*[:：]",
            text,
            flags=re.IGNORECASE,
        ))
        segments: List[Dict[str, Any]] = []
        for index, match in enumerate(matches):
            content_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            content = text[match.end():content_end].strip(" \t\r\n；;。")
            segments.append({
                "start": float(match.group("start")),
                "end": float(match.group("end")),
                "content": content,
            })
        return segments

    def _collect_timeline_detail_issues(self, scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Validate that timed beats are continuous and detailed enough for video generation."""
        issues: List[Dict[str, Any]] = []
        camera_keywords = (
            "镜头", "中景", "近景", "特写", "全景", "远景", "俯拍", "仰拍",
            "平拍", "跟拍", "推近", "推进", "拉远", "摇镜", "环绕", "焦点",
            "景深", "构图", "机位", "手持", "固定", "camera", "shot",
            "close-up", "medium", "wide", "tracking", "dolly", "pan", "focus",
            "カメラ", "ショット", "クローズアップ", "plano", "cámara",
        )
        performance_keywords = (
            "眼", "眉", "嘴角", "表情", "神色", "呼吸", "手", "指尖", "肩",
            "身体", "重心", "脚步", "转身", "抬头", "低头", "握", "抓", "站",
            "坐", "跪", "躺", "靠", "望", "看", "停顿", "eye", "gaze",
            "breath", "hand", "shoulder", "body", "turn", "step", "look",
            "目", "視線", "呼吸", "手", "肩", "cuerpo", "mano", "mirada",
        )
        lighting_keywords = (
            "光", "影", "灯", "明暗", "色温", "反光", "逆光", "轮廓",
            "阴影", "亮", "暗", "雾", "雨", "烟", "尘", "light", "shadow",
            "lamp", "glow", "reflection", "fog", "smoke", "luz", "sombra",
        )

        for index, scene in enumerate(scenes or [], start=1):
            scene_number = scene.get("scene_number", index)
            description = str(scene.get("description") or "")
            segments = self._extract_description_timeline_segments(description)
            try:
                duration = float(scene.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0

            minimum_segments = 2 if duration <= 9 else 3 if duration <= 18 else 4
            if len(segments) < minimum_segments:
                issues.append({"scene": scene_number, "reason": "missing_or_sparse_timeline"})
                continue

            if abs(segments[0]["start"]) > 0.01 or abs(segments[-1]["end"] - duration) > 0.01:
                issues.append({"scene": scene_number, "reason": "timeline_does_not_cover_duration"})
                continue

            for segment_index, segment in enumerate(segments):
                if segment["end"] <= segment["start"]:
                    issues.append({"scene": scene_number, "reason": "invalid_timeline_range"})
                    break
                if segment_index and abs(segment["start"] - segments[segment_index - 1]["end"]) > 0.01:
                    issues.append({"scene": scene_number, "reason": "timeline_gap_or_overlap"})
                    break

                content = self._normalize_single_line(segment["content"])
                sentence_count = len([part for part in re.split(r"[。；;.!?！？]+", content) if part.strip()])
                if len(content) < 48 or sentence_count < 2:
                    issues.append({
                        "scene": scene_number,
                        "reason": f"segment_{segment_index + 1}_too_brief",
                    })
                    break
                if not any(keyword in content for keyword in camera_keywords):
                    issues.append({
                        "scene": scene_number,
                        "reason": f"segment_{segment_index + 1}_missing_camera",
                    })
                    break
                if not any(keyword in content for keyword in performance_keywords):
                    issues.append({
                        "scene": scene_number,
                        "reason": f"segment_{segment_index + 1}_missing_performance",
                    })
                    break
                if not any(keyword in content for keyword in lighting_keywords):
                    issues.append({
                        "scene": scene_number,
                        "reason": f"segment_{segment_index + 1}_missing_environment_feedback",
                    })
                    break
        return issues

    def _collect_spatial_topology_issues(
        self,
        scenes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Validate camera-facing geometry and object/body occlusion relationships."""
        face_to_face_pattern = re.compile(
            r"面对面|面对彼此|面向对方|彼此正面相对|"
            r"相对而(?:立|站|坐)|相向而(?:立|站|坐)|转身面对(?:彼此|对方)"
        )
        facing_solution_pattern = re.compile(
            r"背对镜头|背向镜头|后背朝向镜头|三分之(?:二|三)(?:侧|背)|"
            r"侧背面|侧背构图|侧对镜头|双人侧面|侧面双人|侧面轮廓|"
            r"过肩(?:镜头|构图)?|肩后机位|正反打|反打镜头|"
            r"前景(?:肩背|后脑|背影)|镜头.{0,16}(?:两人|二人).{0,12}侧面|"
            r"(?:一人|其中一人).{0,12}(?:背对|背向|侧对)镜头|"
            r"profile|over[- ]the[- ]shoulder|shot[- ]reverse[- ]shot",
            flags=re.IGNORECASE,
        )
        penetrating_object = (
            r"刀|刀刃|匕首|短刀|长刀|剑|剑刃|短剑|长剑|利刃|"
            r"箭|箭矢|长矛|短矛|矛尖|枪尖"
        )
        body_location = (
            r"胸口|胸膛|胸前|腹部|小腹|肩膀|肩部|后背|背部|"
            r"腰侧|大腿|手臂|躯干|身体"
        )
        penetration_pattern = re.compile(
            rf"(?:{penetrating_object}).{{0,18}}(?:插|刺|贯)(?:入|进|在|着|中|穿透)"
            rf".{{0,18}}(?:{body_location})|"
            rf"(?:{body_location}).{{0,18}}(?:插|刺|贯)(?:着|入|进|有|穿透)"
            rf".{{0,18}}(?:{penetrating_object})"
        )
        embedded_part_pattern = re.compile(
            r"(?:刀刃|剑刃|刃部|箭头|矛尖|枪尖).{0,20}"
            r"(?:没入|埋入|进入体内|藏入|隐藏|不可见|看不见|被.{0,10}(?:身体|皮肉|伤口).{0,8}遮挡)|"
            r"(?:没入|埋入|进入体内|藏入|隐藏|不可见|看不见).{0,20}"
            r"(?:刀刃|剑刃|刃部|箭头|矛尖|枪尖)"
        )
        exposed_part_pattern = re.compile(
            r"(?:只|仅)(?:能)?(?:露出|剩下|剩|看到|看见|见到)?"
            r".{0,12}(?:刀柄|剑柄|握柄|护手|箭杆|箭尾|矛杆|枪杆)|"
            r"(?:刀柄|剑柄|握柄|护手|箭杆|箭尾|矛杆|枪杆).{0,12}"
            r"(?:外露|露在体外|留在体外|露出|可见)"
        )
        pass_through_pattern = re.compile(
            r"(?:贯穿|穿透).{0,24}(?:从|由).{0,12}"
            r"(?:后背|背部|另一侧|身体另一面).{0,12}(?:穿出|露出)"
        )

        issues: List[Dict[str, Any]] = []
        for index, scene in enumerate(scenes or [], start=1):
            scene_number = int(scene.get("scene_number") or index)
            present_names = [
                self._strip_character_name_markers(name)
                for name in (scene.get("characters_present") or [])
                if self._strip_character_name_markers(name)
            ]
            camera_text = self._normalize_single_line(scene.get("camera_angle"))
            description = str(scene.get("description") or "")
            segments = self._extract_description_timeline_segments(description)
            segment_items = segments or [{"content": description}]

            if len(present_names) >= 2:
                for segment_index, segment in enumerate(segment_items, start=1):
                    content = self._normalize_single_line(segment.get("content"))
                    if (
                        face_to_face_pattern.search(content)
                        and not facing_solution_pattern.search(f"{content} {camera_text}")
                    ):
                        issues.append({
                            "scene": scene_number,
                            "segment": segment_index,
                            "reason": "face_to_face_camera_orientation_underspecified",
                        })
                        break

            object_text = " ".join(
                self._normalize_single_line(scene.get(field))
                for field in ("description", "character_description", "camera_angle")
                if self._normalize_single_line(scene.get(field))
            )
            if (
                penetration_pattern.search(object_text)
                and not (
                    embedded_part_pattern.search(object_text)
                    and exposed_part_pattern.search(object_text)
                )
                and not pass_through_pattern.search(object_text)
            ):
                issues.append({
                    "scene": scene_number,
                    "reason": "penetrating_object_occlusion_underspecified",
                })

        return issues

    def _collect_duration_variation_issues(
        self,
        scenes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Reject flat duration plans when the same total permits a visible spread."""
        if len(scenes) < 2:
            return []
        try:
            durations = [int(scene.get("duration")) for scene in scenes]
        except (TypeError, ValueError):
            return [{"reason": "invalid_duration_value"}]

        total = sum(durations)
        minimum_total = len(durations) * self.scene_duration_min
        maximum_total = len(durations) * self.scene_duration_max
        lower_slack = total - minimum_total
        upper_slack = maximum_total - total
        if lower_slack < 2 or upper_slack < 2:
            return []

        if max(durations) - min(durations) < 2:
            return [{"reason": "insufficient_duration_variation"}]
        if len(durations) >= 4 and lower_slack >= 3 and upper_slack >= 3:
            if len(set(durations)) < 3:
                return [{"reason": "insufficient_duration_tiers"}]
        return []

    def _collect_repetitive_intimacy_action_issues(
        self,
        scenes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        explicit_pattern = re.compile(
            r"抽插|反复插入|持续插入|来回插入|活塞式(?:动作)?|"
            r"下身(?:反复|持续|不断)?撞击|胯部(?:反复|持续|不断)?冲撞"
        )
        repeated_soft_pattern = re.compile(
            r"腰(?:身|肢)?(?:反复|持续|不断)?起伏|身体随着节奏起伏|迎合(?:着)?节奏"
        )
        issues: List[Dict[str, Any]] = []
        soft_occurrences: List[int] = []
        for index, scene in enumerate(scenes or [], start=1):
            scene_number = int(scene.get("scene_number") or index)
            text = " ".join(
                str(scene.get(field) or "")
                for field in ("description", "character_description", "camera_angle")
            )
            if explicit_pattern.search(text):
                issues.append({"scene": scene_number, "reason": "mechanical_sex_action"})
            if repeated_soft_pattern.search(text):
                soft_occurrences.append(scene_number)
        if len(soft_occurrences) > 1:
            issues.append({
                "scene": soft_occurrences[1],
                "reason": "repeated_intimacy_motion_across_scenes",
            })
        return issues

    def _collect_wardrobe_continuity_issues(
        self,
        scenes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Detect clothing that appears after nudity without a visible dressing action."""
        issues: List[Dict[str, Any]] = []
        active_nudity_outfits: Dict[str, str] = {}

        for index, scene in enumerate(scenes or [], start=1):
            scene_number = int(scene.get("scene_number") or index)
            previous_scene = scenes[index - 2] if index > 1 else None
            outfits = self._normalize_character_outfits(scene.get("character_outfits"))
            present_names = [
                self._strip_character_name_markers(name)
                for name in (scene.get("characters_present") or [])
                if self._strip_character_name_markers(name)
            ]
            segments = self._extract_description_timeline_segments(scene.get("description"))

            for character_name in present_names:
                locked_outfit = active_nudity_outfits.get(character_name, "")
                outfit = outfits.get(character_name, "")
                outfit_level = self._outfit_nudity_level(outfit)
                continues_lock = bool(
                    locked_outfit
                    and self._scene_continues_nudity_lock(scene, previous_scene)
                    and not self._scene_has_explicit_dressing_completion(
                        previous_scene,
                        character_name,
                    )
                )
                current_level = max(
                    outfit_level,
                    self._outfit_nudity_level(locked_outfit) if continues_lock else 0,
                )
                dressing_completed = False

                for segment_index, segment in enumerate(segments, start=1):
                    content = segment.get("content", "")
                    explicit_dressing = self._text_has_character_explicit_dressing_completion(
                        content,
                        character_name,
                        present_names,
                    )
                    if (
                        current_level > 0
                        and self._text_asserts_character_clothed_state(
                            content,
                            character_name,
                            present_names,
                        )
                        and not explicit_dressing
                        and not dressing_completed
                    ):
                        issues.append({
                            "scene": scene_number,
                            "character": character_name,
                            "segment": segment_index,
                            "reason": "clothing_appears_without_dressing_action",
                        })
                        break

                    if explicit_dressing:
                        dressing_completed = True
                        current_level = 0
                    else:
                        segment_level = self._outfit_nudity_level(content)
                        if segment_level > 0:
                            current_level = segment_level

                if self._scene_has_explicit_dressing_completion(scene, character_name):
                    active_nudity_outfits.pop(character_name, None)
                elif current_level > 0 and (
                    "intimate" in self._scene_context_categories(scene)
                    or self._scene_nudity_level(scene) > 0
                ):
                    active_nudity_outfits[character_name] = (
                        outfit or locked_outfit
                    )
                elif locked_outfit and continues_lock:
                    active_nudity_outfits[character_name] = locked_outfit
                elif self._scene_has_outfit_reset_signal(scene):
                    active_nudity_outfits.pop(character_name, None)

        return issues

    def _serialize_script_for_revision(self, script_data: Dict[str, Any]) -> str:
        """Serialize the normalized prior result without parser-only metadata."""
        public_data = {
            key: value
            for key, value in script_data.items()
            if not str(key).startswith("_")
        }
        return json.dumps(public_data, ensure_ascii=False, indent=2, default=str)

    def _build_quality_revision_request(self, feedback: List[str]) -> str:
        """Build an edit request grounded in concrete validation findings."""
        findings = feedback or ["程序质量校验未通过，请逐项复核所有硬性规则。"]
        numbered_findings = "\n".join(
            f"{index}. {item}" for index, item in enumerate(findings[:30], start=1)
        )
        return (
            "上一条 assistant 消息是需要修正的现有完整剧本 JSON。"
            "不要从原始需求重新构思或另起一版；必须以该版本为基础做定向修正。\n\n"
            f"【程序校验结果与修正建议】\n{numbered_findings}\n\n"
            "【修正规则】\n"
            "1. 优先修正上述失败项，保留未涉及问题的剧情结构、角色、布景、风格、"
            "对白和镜头内容。\n"
            "2. 修正一处时必须同步维护 duration、description 秒段边界、总时长及相邻分镜承接，"
            "不得引入新的矛盾。\n"
            "3. 最终仍需返回修正后的完整剧本 JSON，而不是补丁、局部片段、解释或重新创作说明。"
        )

    def _collect_script_quality_feedback(
        self,
        script_data: Dict[str, Any],
        user_input: str,
    ) -> List[str]:
        """Return actionable findings for a targeted model revision."""
        feedback: List[str] = []
        scenes = script_data.get("scenes") or []
        if not scenes:
            return ["scenes 为空；请在现有故事设定下补齐完整分镜数组。"]
        if len(scenes) > self.max_storyboard_scenes:
            feedback.append(
                f"分镜数量为 {len(scenes)}，超过上限 {self.max_storyboard_scenes}；"
                "请合并功能重复的分镜。"
            )

        normalized_input = (user_input or "").lower()
        allows_empty_dialogue = any(
            phrase in normalized_input
            for phrase in ["不生成对话", "不要对话", "只生成旁白", "no dialogue", "narration only"]
        )
        if not allows_empty_dialogue and not any(
            str(scene.get("dialogue") or "").strip() for scene in scenes
        ):
            feedback.append("全部分镜 dialogue 为空；请在关键冲突、转折或信息推进处补充简短对白。")

        duration_adjustments = script_data.get("_duration_adjustments") or []
        for item in duration_adjustments[:10]:
            feedback.append(
                f"分镜 {item.get('scene_number', '?')} 的 duration={item.get('original_duration')} "
                f"超出 {self.scene_duration_min}-{self.scene_duration_max} 秒；"
                f"请改为 {item.get('normalized_duration')} 秒并同步修正 description 时间轴。"
            )

        for issue in self._collect_duration_variation_issues(scenes):
            reason = issue.get("reason")
            if reason == "insufficient_duration_tiers":
                feedback.append("分镜时长档位不足；4 个及以上分镜在可行时至少使用 3 个不同 duration。")
            elif reason == "insufficient_duration_variation":
                feedback.append("分镜时长过于一致；请按内容丰富度分配，最长与最短至少相差 2 秒。")
            else:
                feedback.append("存在无法解析的 duration；请全部改为合法整数秒数。")

        for issue in self._collect_timeline_detail_issues(scenes)[:10]:
            feedback.append(
                f"分镜 {issue.get('scene', '?')} 的 description 时间轴问题："
                f"{issue.get('reason')}；请按该分镜 duration 补齐连续秒段、动作、表演、"
                "镜头、光影和明确结果。"
            )
        for issue in self._collect_repetitive_intimacy_action_issues(scenes)[:10]:
            feedback.append(
                f"分镜 {issue.get('scene', '?')} 含不合规或重复的亲密动作："
                f"{issue.get('reason')}；请改用人物关系、表情、手部、轮廓和环境反馈推进。"
            )
        for issue in self._collect_wardrobe_continuity_issues(scenes)[:10]:
            feedback.append(
                f"分镜 {issue.get('scene', '?')} 的第 {issue.get('segment', '?')} 个秒段中，"
                f"角色 [{issue.get('character', '?')}] 在裸露状态后没有可见穿衣动作却恢复了衣着；"
                "请保持上一秒段及上一分镜的裸露层级，或先完整写出拿起衣物并穿好/扣好/系好的连续动作，"
                "再在后续秒段或下一分镜切换为穿衣造型。"
            )
        for issue in self._collect_spatial_topology_issues(scenes)[:10]:
            if issue.get("reason") == "face_to_face_camera_orientation_underspecified":
                feedback.append(
                    f"分镜 {issue.get('scene', '?')} 的第 {issue.get('segment', '?')} 个秒段中，"
                    "多人面对面站位没有说明相机可见朝向；请明确采用“一人正面、另一人背面/侧背面”、"
                    "双人侧面构图、过肩镜头或正反打，并逐一写清各角色朝向，禁止同框所有人都正面朝向镜头。"
                )
            else:
                feedback.append(
                    f"分镜 {issue.get('scene', '?')} 的刺入物体缺少正确遮挡关系；"
                    "请写清刺入方向、深度和入口位置。刀剑刺入胸腹后，进入身体的刃部必须被身体遮挡且不可见，"
                    "体外只露出刀柄/剑柄和护手；若为贯穿伤，则必须明确入口、出口及从另一侧穿出的部分。"
                )
        for issue in self._collect_duplicate_scene_issues(scenes)[:10]:
            feedback.append(
                f"分镜 {issue.get('scene_a')} 与 {issue.get('scene_b')} 的 "
                f"{issue.get('field')} 高度重复；保留承接关系并改出新的剧情结果。"
            )
        for issue in self._collect_adjacent_redundancy_issues(scenes)[:10]:
            feedback.append(
                f"相邻分镜 {issue.get('scene_a')} -> {issue.get('scene_b')} 重复前镜内容；"
                "下一镜应先响应前镜结果，再推进新动作或信息。"
            )
        for issue in self._collect_intrusive_transition_issues(scenes)[:10]:
            reason = issue.get("reason")
            if reason == "meta_transition_language":
                feedback.append(
                    f"分镜 {issue.get('scene')} 使用了“{issue.get('marker')}”等元叙事转场文字；"
                    "请直接写可见动作、视线、声音、道具、遮挡、构图或光影承接，不要向观众解释转场。"
                )
            elif reason == "repeated_transition_effect":
                feedback.append(
                    f"“{issue.get('effect')}”转场在多个分镜重复使用；请按剧情分别改用动作匹配、"
                    "视线/道具/构图匹配、声桥或自然遮挡，必要时直接承接而不加特效。"
                )
            else:
                feedback.append(
                    f"分镜 {issue.get('scene')} 堆叠了过多显式转场效果；"
                    "只保留一个有剧情动机且符合整体镜头语言的衔接方式。"
                )

        characters = script_data.get("characters") or []
        character_keys = {
            self._normalize_character_name_key(item.get("name"))
            for item in characters
            if self._normalize_character_name_key(item.get("name"))
        }
        scene_character_names = self._collect_scene_character_names(scenes, mutate_scenes=False)
        if len(scene_character_names) > self.max_characters:
            feedback.append(
                f"分镜出场角色共 {len(scene_character_names)} 个，超过上限 {self.max_characters} 个。"
            )
        missing_characters = [
            name for name in scene_character_names
            if self._normalize_character_name_key(name) not in character_keys
        ]
        if missing_characters:
            feedback.append(
                "以下出场角色缺少 characters 定义：" + "、".join(missing_characters[:10]) + "。"
            )

        scene_definitions = script_data.get("scene_definitions") or []
        incomplete_definitions = [
            str(item.get("name") or f"#{index}")
            for index, item in enumerate(scene_definitions, start=1)
            if not self._normalize_single_line(item.get("time_of_day"))
            or not self._normalize_single_line(item.get("weather"))
            or not self._normalize_scene_feature_list(item.get("scene_features"))
        ]
        if incomplete_definitions:
            feedback.append(
                "以下布景定义缺少 time_of_day、weather 或 scene_features："
                + "、".join(incomplete_definitions[:10]) + "。"
            )
        incomplete_conditions = [
            str(scene.get("scene_number") or index)
            for index, scene in enumerate(scenes, start=1)
            if not self._normalize_single_line(scene.get("time_of_day"))
            or not self._normalize_single_line(scene.get("weather"))
        ]
        if incomplete_conditions:
            feedback.append(
                "以下分镜缺少 time_of_day 或 weather："
                + "、".join(incomplete_conditions[:10]) + "。"
            )

        definition_keys = {
            self._normalize_scene_name_key(item.get("name"))
            for item in scene_definitions
            if self._normalize_scene_name_key(item.get("name"))
        }
        used_scene_names = self._collect_used_scene_names(scenes)
        if len(used_scene_names) > self.max_setting_definitions:
            feedback.append(
                f"实际使用布景共 {len(used_scene_names)} 个，超过上限 "
                f"{self.max_setting_definitions} 个。"
            )
        missing_scene_definitions = [
            name for name in used_scene_names
            if self._normalize_scene_name_key(name) not in definition_keys
        ]
        if missing_scene_definitions:
            feedback.append(
                "以下 scene_name 缺少对应 scene_definitions 定义："
                + "、".join(missing_scene_definitions[:10]) + "。"
            )
        return feedback

    def _is_script_quality_acceptable(self, script_data: Dict[str, Any], user_input: str) -> bool:
        """校验剧本质量，避免把明显残缺的脚本直接送入后续流程。"""
        scenes = script_data.get('scenes') or []
        if not scenes:
            logger.warning("Script quality check failed: no scenes")
            return False

        if len(scenes) > self.max_storyboard_scenes:
            logger.warning(
                "Script quality check failed: scenes=%s > max_storyboard_scenes=%s",
                len(scenes),
                self.max_storyboard_scenes,
            )
            return False

        min_scene_count = 1
        if len(scenes) < min_scene_count:
            logger.warning(
                f"Script quality check failed: scenes={len(scenes)} < min_scene_count={min_scene_count}"
            )
            return False

        normalized_input = (user_input or "").lower()
        user_explicitly_allows_empty_dialogue = any(
            phrase in normalized_input
            for phrase in ["不生成对话", "不要对话", "只生成旁白", "no dialogue", "narration only"]
        )
        if not user_explicitly_allows_empty_dialogue:
            non_empty_dialogues = sum(
                1 for scene in scenes if (scene.get('dialogue') or '').strip()
            )
            if non_empty_dialogues == 0:
                logger.warning("Script quality check failed: all scene dialogues are empty")
                return False

        duration_adjustments = script_data.get('_duration_adjustments') or []
        if duration_adjustments:
            logger.warning(
                "Script quality check failed: %s scene durations were outside %s-%s seconds before normalization",
                len(duration_adjustments),
                self.scene_duration_min,
                self.scene_duration_max,
            )
            return False

        duration_variation_issues = self._collect_duration_variation_issues(scenes)
        if duration_variation_issues:
            logger.warning(
                "Script quality check failed: scene durations do not reflect content complexity"
            )
            return False

        timeline_issues = self._collect_timeline_detail_issues(scenes)
        if timeline_issues:
            for issue in timeline_issues[:5]:
                logger.warning(
                    "Script quality check failed: scene %s timeline detail issue (%s)",
                    issue["scene"],
                    issue["reason"],
                )
            return False

        intimacy_action_issues = self._collect_repetitive_intimacy_action_issues(scenes)
        if intimacy_action_issues:
            for issue in intimacy_action_issues[:5]:
                logger.warning(
                    "Script quality check failed: scene %s intimacy action issue (%s)",
                    issue["scene"],
                    issue["reason"],
                )
            return False

        wardrobe_continuity_issues = self._collect_wardrobe_continuity_issues(scenes)
        if wardrobe_continuity_issues:
            for issue in wardrobe_continuity_issues[:5]:
                logger.warning(
                    "Script quality check failed: scene %s character %s wardrobe continuity issue (%s)",
                    issue["scene"],
                    issue["character"],
                    issue["reason"],
                )
            return False

        spatial_topology_issues = self._collect_spatial_topology_issues(scenes)
        if spatial_topology_issues:
            for issue in spatial_topology_issues[:5]:
                logger.warning(
                    "Script quality check failed: scene %s spatial topology issue (%s)",
                    issue["scene"],
                    issue["reason"],
                )
            return False

        duplicate_issues = self._collect_duplicate_scene_issues(scenes)
        if duplicate_issues:
            for issue in duplicate_issues[:5]:
                logger.warning(
                    "Script quality check failed: scene %s and scene %s are too similar (%s=%.2f)",
                    issue["scene_a"],
                    issue["scene_b"],
                    issue["field"],
                    issue["similarity"],
                )
            return False

        adjacent_redundancy_issues = self._collect_adjacent_redundancy_issues(scenes)
        if adjacent_redundancy_issues:
            for issue in adjacent_redundancy_issues[:5]:
                logger.warning(
                    "Script quality check failed: adjacent scene %s -> %s repeats prior content (%s=%.2f)",
                    issue["scene_a"],
                    issue["scene_b"],
                    issue["field"],
                    issue["similarity"],
                )
            return False

        characters = script_data.get('characters') or []
        character_keys = {
            self._normalize_character_name_key(item.get('name'))
            for item in characters
            if self._normalize_character_name_key(item.get('name'))
        }
        scene_character_names = self._collect_scene_character_names(scenes, mutate_scenes=False)
        if len(scene_character_names) > self.max_characters:
            logger.warning(
                "Script quality check failed: scene_character_count=%s > max_characters=%s",
                len(scene_character_names),
                self.max_characters,
            )
            return False
        missing_character_definitions = [
            name for name in scene_character_names
            if self._normalize_character_name_key(name) not in character_keys
        ]
        if missing_character_definitions:
            logger.warning(
                "Script quality check failed: characters missing definitions: %s",
                ", ".join(missing_character_definitions[:10]),
            )
            return False

        scene_definitions = script_data.get('scene_definitions') or []
        incomplete_scene_definitions = [
            str(item.get('name') or f'#{index}')
            for index, item in enumerate(scene_definitions, start=1)
            if not self._normalize_single_line(item.get('time_of_day'))
            or not self._normalize_single_line(item.get('weather'))
            or not self._normalize_scene_feature_list(item.get('scene_features'))
        ]
        if incomplete_scene_definitions:
            logger.warning(
                "Script quality check failed: scene definitions missing structured fields: %s",
                ", ".join(incomplete_scene_definitions[:10]),
            )
            return False

        incomplete_scene_conditions = [
            str(scene.get('scene_number') or index)
            for index, scene in enumerate(scenes, start=1)
            if not self._normalize_single_line(scene.get('time_of_day'))
            or not self._normalize_single_line(scene.get('weather'))
        ]
        if incomplete_scene_conditions:
            logger.warning(
                "Script quality check failed: scenes missing time_of_day/weather: %s",
                ", ".join(incomplete_scene_conditions[:10]),
            )
            return False

        definition_keys = {
            self._normalize_scene_name_key(item.get('name'))
            for item in scene_definitions
            if self._normalize_scene_name_key(item.get('name'))
        }
        used_scene_names = self._collect_used_scene_names(scenes)
        if len(used_scene_names) > self.max_setting_definitions:
            logger.warning(
                "Script quality check failed: used_scene_name_count=%s > max_setting_definitions=%s",
                len(used_scene_names),
                self.max_setting_definitions,
            )
            return False
        missing_scene_definitions = [
            name for name in used_scene_names
            if self._normalize_scene_name_key(name) not in definition_keys
        ]
        if missing_scene_definitions:
            logger.warning(
                "Script quality check failed: scene definitions missing used backdrops: %s",
                ", ".join(missing_scene_definitions[:10]),
            )
            return False

        intrusive_transition_issues = self._collect_intrusive_transition_issues(scenes)
        if intrusive_transition_issues:
            logger.warning(
                "Script quality check failed: intrusive or repetitive transition design: %s",
                intrusive_transition_issues[:5],
            )
            return False

        transition_issues = self._collect_transition_issues(scenes)
        if transition_issues:
            for issue in transition_issues[:5]:
                logger.warning(
                    "Script quality advisory: scene %s -> scene %s transition is not smooth enough (%s)",
                    issue["scene_a"],
                    issue["scene_b"],
                    issue["reason"],
                )

        outgoing_transition_issues = self._collect_outgoing_transition_issues(scenes)
        if outgoing_transition_issues:
            for issue in outgoing_transition_issues[:5]:
                logger.warning(
                    "Script quality advisory: scene %s lacks outgoing transition cue (%s)",
                    issue["scene_a"],
                    issue["reason"],
                )

        return True

    def _parse_script(self, content: str) -> Dict[str, Any]:
        """解析剧本JSON，处理各种格式问题"""
        try:
            json_str = self._extract_json_object(content) or content

            # 尝试直接解析
            try:
                data = self._parse_json_like(json_str)
            except json.JSONDecodeError as top_level_error:
                # 尝试修复常见的JSON格式问题
                logger.info("Attempting to fix JSON format issues...")
                logger.warning(
                    "Top-level JSON parse failed: %s at line=%s column=%s pos=%s",
                    top_level_error.msg,
                    top_level_error.lineno,
                    top_level_error.colno,
                    top_level_error.pos,
                )
                
                # 1. 修复单引号问题（将单引号替换为双引号，但要注意字符串内的单引号）
                # 先处理字符串内的双引号转义
                fixed_str = self._clean_json_candidate(json_str)
                
                # 2. 移除可能的注释
                fixed_str = re.sub(r'//.*?\n', '\n', fixed_str)
                fixed_str = re.sub(r'/\*.*?\*/', '', fixed_str, flags=re.DOTALL)
                
                # 3. 尝试解析修复后的JSON
                try:
                    data = self._parse_json_like(fixed_str)
                except json.JSONDecodeError as fixed_parse_error:
                    logger.warning(
                        "Fixed JSON parse failed: %s at line=%s column=%s pos=%s",
                        fixed_parse_error.msg,
                        fixed_parse_error.lineno,
                        fixed_parse_error.colno,
                        fixed_parse_error.pos,
                    )
                    # 4. 如果前后有额外文本，尝试仅提取第一个完整 JSON 对象
                    object_only_str = self._extract_json_object(fixed_str)
                    if object_only_str:
                        try:
                            data = self._parse_json_like(object_only_str)
                        except json.JSONDecodeError as object_only_error:
                            logger.warning(
                                "Object-only JSON parse failed: %s at line=%s column=%s pos=%s",
                                object_only_error.msg,
                                object_only_error.lineno,
                                object_only_error.colno,
                                object_only_error.pos,
                            )
                            try:
                                data, _ = json.JSONDecoder().raw_decode(self._clean_json_candidate(object_only_str))
                            except json.JSONDecodeError:
                                logger.warning("Standard JSON parsing failed, trying alternative methods...")
                                data = self._extract_script_data(fixed_str)
                                if not data:
                                    raise ValueError("无法解析剧本数据")
                    else:
                        logger.warning("Standard JSON parsing failed, trying alternative methods...")
                        data = self._extract_script_data(fixed_str)
                        if not data:
                            raise ValueError("无法解析剧本数据")

            # 确保剧本基调(tone)为字符串（模型可能漏字段或输出为数组/对象）
            if 'tone' in data and data['tone'] is not None:
                data['tone'] = self._coerce_scene_text_field(data['tone'])

            # 确保剧本时代(era)为字符串（模型可能输出为数组/对象或漏字段）
            if 'era' in data and data['era'] is not None:
                data['era'] = self._coerce_scene_text_field(data['era'])

            # 确保角色age字段是字符串类型
            if 'characters' in data:
                for char in data['characters']:
                    if 'age' in char and char['age'] is not None:
                        char['age'] = str(char['age'])
                    # 角色基础档案字段：模型可能输出为数组/对象，统一拍平为字符串
                    for field_name in (
                        'nationality',
                        'hairstyle',
                        'body_features',
                        'personality',
                        'identity_background',
                    ):
                        if field_name in char and char[field_name] is not None:
                            char[field_name] = self._coerce_scene_text_field(char[field_name])

            duration_adjustments: List[Dict[str, Any]] = []

            # 确保分镜字段类型正确
            if 'scenes' in data and data['scenes']:
                scene_name_aliases = ['scene_name', 'sceneName', 'scene_ref', 'sceneRef', 'scene_title', 'sceneTitle', 'location_name']
                for idx, scene in enumerate(data['scenes'], start=1):
                    if 'scene_number' not in scene:
                        # 兼容模型可能返回的别名字段；都没有时按顺序自动补齐
                        alias_value = (
                            scene.get('sceneNo')
                            or scene.get('scene_no')
                            or scene.get('id')
                            or scene.get('index')
                            or scene.get('number')
                        )
                        if alias_value is not None:
                            try:
                                scene['scene_number'] = int(alias_value)
                            except Exception:
                                scene['scene_number'] = idx
                        else:
                            scene['scene_number'] = idx
                            logger.warning(f"Missing scene_number in scene, auto-filled with idx={idx}")
                    else:
                        try:
                            scene['scene_number'] = int(scene['scene_number'])
                        except Exception:
                            scene['scene_number'] = idx
                            logger.warning(f"Invalid scene_number format, fallback idx={idx}")
                    # duration字段：整数类型（处理"6秒"这样的字符串）
                    if 'duration' in scene and scene['duration'] is not None:
                        duration_val = scene['duration']
                        if isinstance(duration_val, str):
                            # 提取字符串中的数字，如"6秒" -> 6
                            match = re.search(r'\d+', duration_val)
                            if match:
                                scene['duration'] = int(match.group())
                            else:
                                scene['duration'] = self.scene_duration_min
                        elif not isinstance(duration_val, int):
                            scene['duration'] = int(duration_val)

                    # 限制分镜时长不超过最大值（适配seedance-2.0模型限制）
                    if 'duration' in scene:
                        if scene['duration'] > self.scene_duration_max:
                            duration_adjustments.append({
                                "scene_number": scene.get('scene_number', idx),
                                "original_duration": scene['duration'],
                                "normalized_duration": self.scene_duration_max,
                            })
                            scene['duration'] = self.scene_duration_max
                        elif scene['duration'] < self.scene_duration_min:
                            duration_adjustments.append({
                                "scene_number": scene.get('scene_number', idx),
                                "original_duration": scene['duration'],
                                "normalized_duration": self.scene_duration_min,
                            })
                            scene['duration'] = self.scene_duration_min

                    # dialogue 字段统一为多行字符串；description 中误写的直接对白会迁移到 dialogue。
                    scene['dialogue'] = self._normalize_dialogue_field(scene.get('dialogue'))
                    self._move_dialogue_out_of_description(scene)

                    # 补齐 Scene 必需字段（模型可能漏字段，或备用提取只得到部分字段）
                    # Pydantic Scene: character_description/voice_description/mood 为必填 str
                    # 模型有时把 character_description / voice_description 输出成按角色名分组的
                    # dict/list，这里统一拍平成字符串，避免 Scene 校验因类型不符报错。
                    scene['character_description'] = self._coerce_scene_text_field(
                        scene.get('character_description')
                    )
                    scene['voice_description'] = self._coerce_scene_text_field(
                        scene.get('voice_description')
                    )
                    if 'mood' not in scene or scene['mood'] is None:
                        scene['mood'] = ''
                    inferred_time_of_day = self._infer_time_of_day_from_text(
                        scene.get('time_of_day'),
                        scene.get('scene_name'),
                        scene.get('scene_state'),
                        scene.get('description'),
                        scene.get('dialogue'),
                    )
                    inferred_weather = self._infer_weather_from_text(
                        scene.get('weather'),
                        scene.get('scene_name'),
                        scene.get('scene_state'),
                        scene.get('description'),
                        scene.get('dialogue'),
                    )
                    scene['time_of_day'] = self._normalize_single_line(scene.get('time_of_day') or inferred_time_of_day)
                    scene['weather'] = self._normalize_single_line(scene.get('weather') or inferred_weather)
                    scene['scene_state'] = self._normalize_scene_state(scene.get('time_of_day'), scene.get('weather'))
                    # camera_angle / characters_present 虽然在模型里是 Optional，但这里也给默认值，避免下游逻辑空指针
                    if 'camera_angle' not in scene or scene['camera_angle'] is None:
                        scene['camera_angle'] = ''
                    if 'characters_present' not in scene or scene['characters_present'] is None:
                        scene['characters_present'] = []
                    # character_outfits：规范化为 {角色名: 装扮描述} 的字典，去除空值
                    scene['character_outfits'] = self._filter_default_character_outfits(
                        self._normalize_character_outfits(scene.get('character_outfits')),
                        data.get('characters') or [],
                    )
                    if 'scene_name' not in scene or not str(scene.get('scene_name') or '').strip():
                        alias_value = next((scene.get(alias) for alias in scene_name_aliases if scene.get(alias)), None)
                        if alias_value:
                            scene['scene_name'] = str(alias_value).strip()
                        else:
                            scene['scene_name'] = self._fallback_scene_name_from_description(
                                scene.get('description') or data.get('background') or f"场景{idx}",
                                idx,
                            )
                    elif isinstance(scene.get('scene_name'), list):
                        scene['scene_name'] = '、'.join([str(item).strip() for item in scene.get('scene_name') if str(item).strip()])
                    else:
                        scene['scene_name'] = str(scene.get('scene_name') or '').strip()

                # 检测并处理相邻分镜内容重复
                self._check_and_fix_duplicate_scenes(data['scenes'])
                if len(data['scenes']) > self.max_storyboard_scenes:
                    logger.warning(
                        "Scene count %s exceeds max_storyboard_scenes=%s, truncating",
                        len(data['scenes']),
                        self.max_storyboard_scenes,
                    )
                    data['scenes'] = data['scenes'][:self.max_storyboard_scenes]
            else:
                # 如果没有分镜数据，创建一个默认分镜
                logger.warning("No scenes found in script data, creating default scene")
                data['scenes'] = [{
                    'scene_number': 1,
                    'scene_name': '主场景',
                    'description': data.get('background', '故事场景'),
                    'dialogue': '',
                    'duration': self.scene_duration_min,
                    'character_description': '',
                    'voice_description': '',
                    'mood': '',
                    'time_of_day': self._infer_time_of_day_from_text(data.get('background')),
                    'weather': self._infer_weather_from_text(data.get('background')),
                    'camera_angle': '',
                    'characters_present': []
                }]

            # 确保必要的字段存在
            if 'title' not in data or not data['title']:
                data['title'] = '未命名剧本'
            if 'style' not in data or not data['style']:
                data['style'] = '默认风格'
            if 'background' not in data or not data['background']:
                data['background'] = '故事背景'
            if 'characters' not in data or not data['characters']:
                data['characters'] = []
            data['characters'] = self._normalize_characters(data.get('characters') or [])
            raw_setting_definitions = data.get('scene_definitions')
            if raw_setting_definitions is None:
                raw_setting_definitions = data.get('setting_definitions')
            if raw_setting_definitions is None:
                raw_setting_definitions = data.get('backdrop_definitions')
            data['scene_definitions'] = self._normalize_scene_definitions(
                raw_setting_definitions,
                data.get('scenes') or [],
                data.get('background') or '',
            )
            data['scene_definitions'] = data['scene_definitions'][:self.max_setting_definitions]
            self._backfill_scene_conditions_from_definitions(data['scenes'], data['scene_definitions'])
            self._normalize_scene_character_presence(data['scenes'])
            self._align_scene_names_to_definitions(data['scenes'], data['scene_definitions'])
            for scene in data.get('scenes') or []:
                scene['character_outfits'] = self._filter_default_character_outfits(
                    self._normalize_character_outfits(scene.get('character_outfits')),
                    data.get('characters') or [],
                )
            self._enforce_character_outfit_continuity(
                data.get('scenes') or [],
                data.get('characters') or [],
            )
            self._canonicalize_similar_character_outfits(data.get('scenes') or [])
            self._annotate_scene_character_names(
                data.get('scenes') or [],
                data.get('characters') or [],
            )
            data['_duration_adjustments'] = duration_adjustments

            return data

        except Exception as e:
            logger.error(f"Failed to parse script JSON: {str(e)}")
            logger.error(f"Content: {content}")
            logger.warning("Returning default script due to parsing failure")
            return {
                'title': '未命名剧本',
                'style': '默认风格',
                'background': '故事背景',
                'characters': [],
                'scene_definitions': [{
                    'name': '主场景',
                    'description': '故事背景',
                    'time_of_day': '',
                    'weather': '',
                    'scene_features': [],
                }],
                'scenes': [{
                    'scene_number': 1,
                    'scene_name': '主场景',
                    'scene_state': '',
                    'description': '故事开始',
                    'dialogue': '',
                    'duration': self.scene_duration_min,
                    'character_description': '',
                    'voice_description': '',
                    'mood': '',
                    'time_of_day': '',
                    'weather': '',
                    'camera_angle': '',
                    'characters_present': []
                }]
            }

    def _parse_json_like(self, content: str) -> Any:
        """解析模型返回的 JSON-like 文本，兼容单引号、尾逗号和 Python 字面量。"""
        cleaned = self._clean_json_candidate(content)
        cleaned = self._escape_invalid_json_string_chars(cleaned)
        repaired = self._repair_missing_commas_by_lines(cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as json_error:
            if repaired != cleaned:
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError as repaired_error:
                    json_error = repaired_error
            python_like = repaired
            python_like = re.sub(r'(?<![A-Za-z0-9_"])\\btrue\\b', 'True', python_like, flags=re.IGNORECASE)
            python_like = re.sub(r'(?<![A-Za-z0-9_"])\\bfalse\\b', 'False', python_like, flags=re.IGNORECASE)
            python_like = re.sub(r'(?<![A-Za-z0-9_"])\\bnull\\b', 'None', python_like, flags=re.IGNORECASE)
            try:
                return ast.literal_eval(python_like)
            except Exception:
                raise json_error

    def _repair_missing_commas_by_lines(self, content: str) -> str:
        """按行修复常见缺逗号场景：上一行是完整值，下一行直接开始新字段或新对象。"""
        lines = str(content or "").splitlines(keepends=True)
        if len(lines) < 2:
            return str(content or "")

        repaired_lines: List[str] = []
        for index, line in enumerate(lines):
            current_line = line
            stripped = line.strip()
            if not stripped or stripped.endswith((',', '{', '[', ':')):
                repaired_lines.append(current_line)
                continue

            next_non_empty = ""
            for future_line in lines[index + 1:]:
                candidate = future_line.strip()
                if candidate:
                    next_non_empty = candidate
                    break

            needs_comma = bool(
                next_non_empty
                and (next_non_empty.startswith('"') or next_non_empty.startswith('{'))
                and re.search(r'("|\}|\]|\d|true|false|null)\s*$', stripped, flags=re.IGNORECASE)
            )
            if needs_comma and not stripped.endswith(','):
                line_without_newline = current_line.rstrip('\r\n')
                newline = current_line[len(line_without_newline):]
                current_line = f"{line_without_newline},{newline}"

            repaired_lines.append(current_line)

        return ''.join(repaired_lines)

    def _clean_json_candidate(self, content: str) -> str:
        """清洗模型输出中的常见 JSON 噪音。"""
        cleaned = str(content or "").strip()
        cleaned = cleaned.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
        cleaned = cleaned.replace("（", "(").replace("）", ")")
        cleaned = re.sub(r'```(?:json|JSON)?', '', cleaned)
        cleaned = cleaned.replace("```", "")
        cleaned = re.sub(r'^\s*json\s*[\r\n]+', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r',(\s*[}\]])', r'\1', cleaned)
        return cleaned.strip()

    def _escape_invalid_json_string_chars(self, content: str) -> str:
        """转义 JSON 字符串中的裸换行/回车/制表符，避免模型输出被 json.loads 直接拒绝。"""
        result: List[str] = []
        in_string = False
        escaped = False

        for char in str(content or ""):
            if escaped:
                result.append(char)
                escaped = False
                continue

            if char == '\\':
                result.append(char)
                escaped = True
                continue

            if char == '"':
                result.append(char)
                in_string = not in_string
                continue

            if in_string:
                if char == '\n':
                    result.append('\\n')
                    continue
                if char == '\r':
                    result.append('\\r')
                    continue
                if char == '\t':
                    result.append('\\t')
                    continue

            result.append(char)

        return ''.join(result)

    def _extract_json_object(self, content: str) -> Optional[str]:
        """提取第一个完整 JSON 对象，忽略字符串内部的括号。"""
        cleaned = self._clean_json_candidate(content)

        start_idx = cleaned.find('{')
        if start_idx == -1:
            return None

        return self._extract_balanced_segment(cleaned, start_idx, '{', '}')

    def _extract_balanced_segment(
        self,
        content: str,
        start_idx: int,
        open_char: str,
        close_char: str
    ) -> Optional[str]:
        """提取成对括号包裹的片段，支持字符串和转义字符。"""
        if start_idx < 0 or start_idx >= len(content) or content[start_idx] != open_char:
            return None

        depth = 0
        in_string = False
        string_char = ""
        escaped = False

        for index in range(start_idx, len(content)):
            char = content[index]

            if escaped:
                escaped = False
                continue

            if in_string:
                if char == '\\':
                    escaped = True
                elif char == string_char:
                    in_string = False
                continue

            if char in {'"', "'"}:
                in_string = True
                string_char = char
                continue

            if char == open_char:
                depth += 1
            elif char == close_char:
                depth -= 1
                if depth == 0:
                    return content[start_idx:index + 1]

        return None

    def _check_and_fix_duplicate_scenes(self, scenes: List[Dict]) -> None:
        """
        检测不同分镜内容是否重复或雷同，并记录警告日志。
        """
        for issue in self._collect_duplicate_scene_issues(scenes):
            logger.warning(
                "Scene %s and Scene %s have highly similar %s (similarity: %.2f)",
                issue["scene_a"],
                issue["scene_b"],
                issue["field"],
                issue["similarity"],
            )

    def _collect_duplicate_scene_issues(self, scenes: List[Dict]) -> List[Dict[str, Any]]:
        if len(scenes) < 2:
            return []

        issues: List[Dict[str, Any]] = []
        for i in range(len(scenes)):
            for j in range(i + 1, len(scenes)):
                first_scene = scenes[i]
                second_scene = scenes[j]

                desc_similarity = self._calculate_text_similarity(
                    first_scene.get('description', '') or '',
                    second_scene.get('description', '') or '',
                )
                dialogue_similarity = self._calculate_text_similarity(
                    first_scene.get('dialogue', '') or '',
                    second_scene.get('dialogue', '') or '',
                )

                if desc_similarity >= 0.72:
                    issues.append({
                        "scene_a": first_scene.get('scene_number', i + 1),
                        "scene_b": second_scene.get('scene_number', j + 1),
                        "field": "description",
                        "similarity": desc_similarity,
                    })
                if dialogue_similarity >= 0.78:
                    issues.append({
                        "scene_a": first_scene.get('scene_number', i + 1),
                        "scene_b": second_scene.get('scene_number', j + 1),
                        "field": "dialogue",
                        "similarity": dialogue_similarity,
                    })
        return issues

    def _collect_adjacent_redundancy_issues(self, scenes: List[Dict]) -> List[Dict[str, Any]]:
        if len(scenes) < 2:
            return []

        issues: List[Dict[str, Any]] = []
        for index in range(1, len(scenes)):
            previous_scene = scenes[index - 1]
            current_scene = scenes[index]

            previous_description = str(previous_scene.get('description', '') or '')
            current_description = str(current_scene.get('description', '') or '')
            previous_dialogue = str(previous_scene.get('dialogue', '') or '')
            current_dialogue = str(current_scene.get('dialogue', '') or '')

            desc_similarity = self._calculate_text_similarity(previous_description, current_description)
            dialogue_similarity = self._calculate_text_similarity(previous_dialogue, current_dialogue)

            same_backdrop = (
                self._normalize_scene_name_key(previous_scene.get('scene_name'))
                and self._normalize_scene_name_key(previous_scene.get('scene_name'))
                == self._normalize_scene_name_key(current_scene.get('scene_name'))
            )
            previous_chars = {
                self._normalize_character_name_key(name)
                for name in self._split_character_names(previous_scene.get('characters_present'))
                if self._normalize_character_name_key(name)
            }
            current_chars = {
                self._normalize_character_name_key(name)
                for name in self._split_character_names(current_scene.get('characters_present'))
                if self._normalize_character_name_key(name)
            }
            strong_character_overlap = bool(previous_chars) and previous_chars == current_chars

            desc_threshold = 0.58 if (same_backdrop or strong_character_overlap) else 0.66
            dialogue_threshold = 0.70 if (same_backdrop or strong_character_overlap) else 0.78

            if desc_similarity >= desc_threshold:
                issues.append({
                    "scene_a": previous_scene.get('scene_number', index),
                    "scene_b": current_scene.get('scene_number', index + 1),
                    "field": "description",
                    "similarity": desc_similarity,
                })
            if dialogue_similarity >= dialogue_threshold:
                issues.append({
                    "scene_a": previous_scene.get('scene_number', index),
                    "scene_b": current_scene.get('scene_number', index + 1),
                    "field": "dialogue",
                    "similarity": dialogue_similarity,
                })

        return issues

    def _calculate_text_similarity(self, text1: str, text2: str) -> float:
        """
        计算两段文本的相似度（0-1之间）

        使用字符级 n-gram 的 Jaccard 相似度，兼容中文连续文本。
        """
        if not text1 or not text2:
            return 0.0

        normalized1 = self._normalize_text_for_similarity(text1)
        normalized2 = self._normalize_text_for_similarity(text2)
        if not normalized1 or not normalized2:
            return 0.0

        grams1 = self._build_char_ngrams(normalized1, 2)
        grams2 = self._build_char_ngrams(normalized2, 2)

        if not grams1 or not grams2:
            return 0.0

        intersection = grams1.intersection(grams2)
        union = grams1.union(grams2)

        if not union:
            return 0.0

        return len(intersection) / len(union)

    def _collect_transition_issues(self, scenes: List[Dict]) -> List[Dict[str, Any]]:
        if len(scenes) < 2:
            return []

        issues: List[Dict[str, Any]] = []
        for index in range(1, len(scenes)):
            previous_scene = scenes[index - 1]
            current_scene = scenes[index]
            description = str(current_scene.get("description", "") or "").strip()
            if not description:
                issues.append({
                    "scene_a": previous_scene.get("scene_number", index),
                    "scene_b": current_scene.get("scene_number", index + 1),
                    "reason": "missing_description",
                })
                continue

            if not self._has_transition_cue(description):
                issues.append({
                    "scene_a": previous_scene.get("scene_number", index),
                    "scene_b": current_scene.get("scene_number", index + 1),
                    "reason": "missing_transition_cue",
                })

        return issues

    def _collect_intrusive_transition_issues(
        self,
        scenes: List[Dict],
    ) -> List[Dict[str, Any]]:
        """Detect transition language/effects that call attention to the edit itself."""
        issues: List[Dict[str, Any]] = []
        meta_markers = [
            "下一镜",
            "下一个镜头",
            "进入下个镜头",
            "进入下一场",
            "推进下一镜",
            "承接下一镜",
            "带到下一镜",
            "转场钩子",
            "悬念钩子",
            "留下悬念",
            "制造悬念",
            "埋下伏笔",
            "next scene",
            "next shot",
            "transition hook",
            "leave suspense",
            "次のシーン",
            "次のカット",
            "siguiente escena",
            "dejar suspense",
            "dejar suspenso",
        ]
        explicit_effects = [
            "黑屏",
            "闪白",
            "旋转转场",
            "粒子转场",
            "故障转场",
            "变焦爆炸",
            "甩镜",
            "淡入",
            "淡出",
            "叠化",
            "black screen",
            "white flash",
            "spin transition",
            "particle transition",
            "glitch transition",
            "whip pan",
            "fade in",
            "fade out",
            "dissolve",
            "ブラックアウト",
            "ホワイトフラッシュ",
            "回転トランジション",
            "フェードイン",
            "フェードアウト",
            "pantalla negra",
            "destello blanco",
            "transición giratoria",
            "fundido de entrada",
            "fundido de salida",
        ]
        effect_occurrences: Dict[str, List[Any]] = {
            effect: [] for effect in explicit_effects
        }

        for index, scene in enumerate(scenes, start=1):
            scene_number = scene.get("scene_number", index)
            description = str(scene.get("description") or "")
            normalized = description.lower()
            for marker in meta_markers:
                if marker in normalized:
                    issues.append({
                        "scene": scene_number,
                        "reason": "meta_transition_language",
                        "marker": marker,
                    })
                    break

            effects_in_scene = [
                effect for effect in explicit_effects if effect in normalized
            ]
            if len(effects_in_scene) > 1:
                issues.append({
                    "scene": scene_number,
                    "reason": "stacked_transition_effects",
                    "effects": effects_in_scene,
                })
            for effect in effects_in_scene:
                effect_occurrences[effect].append((index, scene_number))

        for effect, occurrences in effect_occurrences.items():
            indexes = [item[0] for item in occurrences]
            scene_numbers = [item[1] for item in occurrences]
            has_three_consecutive = any(
                indexes[offset + 2] - indexes[offset] == 2
                for offset in range(max(0, len(indexes) - 2))
            )
            overuse_threshold = max(4, (len(scenes) + 2) // 3)
            if has_three_consecutive or len(occurrences) >= overuse_threshold:
                issues.append({
                    "reason": "repeated_transition_effect",
                    "effect": effect,
                    "scenes": scene_numbers,
                })
        return issues

    def _collect_outgoing_transition_issues(self, scenes: List[Dict]) -> List[Dict[str, Any]]:
        if len(scenes) < 2:
            return []

        issues: List[Dict[str, Any]] = []
        for index in range(len(scenes) - 1):
            current_scene = scenes[index]
            description = str(current_scene.get("description", "") or "").strip()
            if not description:
                issues.append({
                    "scene_a": current_scene.get("scene_number", index + 1),
                    "reason": "missing_description",
                })
                continue

            if not self._has_outgoing_transition_cue(description):
                issues.append({
                    "scene_a": current_scene.get("scene_number", index + 1),
                    "reason": "missing_outgoing_transition_cue",
                })

        return issues

    def _has_transition_cue(self, text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False

        transition_keywords = [
            "仍在",
            "继续",
            "顺势",
            "余音",
            "脚步声",
            "铃声",
            "敲门声",
            "视线",
            "望向",
            "看向",
            "同一动作",
            "同一方向",
            "门后",
            "窗外",
            "倒影",
            "前景",
            "遮住",
            "掠过",
            "匹配",
            "声桥",
            "画面切换",
            "切换至",
            "场景过渡",
            "随着",
            "随后",
            "接着",
            "紧接着",
            "下一刻",
            "片刻后",
            "与此同时",
            "此时",
            "转场",
            "淡入",
            "淡出",
            "跟随",
            "拉远",
            "推进",
            "then",
            "meanwhile",
            "moments later",
            "the camera",
            "cut to",
            "transition",
        ]
        return any(keyword in normalized for keyword in transition_keywords)

    def _has_outgoing_transition_cue(self, text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False

        trailing_window = normalized[-120:] if len(normalized) > 120 else normalized
        outgoing_keywords = [
            "望向",
            "看向",
            "视线",
            "伸向",
            "握住",
            "推开",
            "走向",
            "冲向",
            "转身",
            "脚步",
            "铃声",
            "敲门",
            "风声",
            "余音",
            "回声",
            "倒影",
            "影子",
            "前景",
            "遮住",
            "掠过",
            "填满画面",
            "停在",
            "定格",
            "光线",
            "色彩",
            "looks toward",
            "gaze",
            "reaches",
            "opens",
            "footsteps",
            "ringing",
            "echo",
            "shadow",
            "foreground",
            "occludes",
            "fills the frame",
            "holds on",
            "随后",
            "接着",
            "紧接着",
            "下一刻",
            "片刻后",
            "与此同时",
            "镜头转向",
            "镜头跟随",
            "镜头缓缓",
            "画面切换",
            "切换至",
            "转场",
            "淡出",
            "淡入",
            "然后",
            "接下来",
            "随即",
            "the camera",
            "cut to",
            "then",
            "moments later",
            "meanwhile",
            "next",
        ]
        return any(keyword in trailing_window for keyword in outgoing_keywords)

    def _normalize_text_for_similarity(self, text: str) -> str:
        normalized = re.sub(r'\s+', '', str(text or '').strip().lower())
        normalized = re.sub(r'[，。、“”"！？!?,；：:（）()\[\]{}<>《》【】…\-—_~·`]', '', normalized)
        return normalized

    def _build_char_ngrams(self, text: str, n: int = 2) -> set:
        if not text:
            return set()
        if len(text) <= n:
            return {text}
        return {text[i:i + n] for i in range(len(text) - n + 1)}

    def _extract_script_data(self, content: str) -> Dict[str, Any]:
        """从非标准JSON内容中提取剧本数据"""
        import re

        data = {
            'title': '',
            'style': '',
            'background': '',
            'characters': [],
            'scene_definitions': [],
            'scenes': []
        }

        try:
            # 提取标题
            title_match = re.search(r'["\']?title["\']?\s*:\s*["\']([^"\']+)["\']', content)
            if title_match:
                data['title'] = title_match.group(1)

            # 提取风格
            style_match = re.search(r'["\']?style["\']?\s*:\s*["\']([^"\']+)["\']', content)
            if style_match:
                data['style'] = style_match.group(1)

            # 提取背景
            bg_match = re.search(r'["\']?background["\']?\s*:\s*["\']([^"\']+)["\']', content)
            if bg_match:
                data['background'] = bg_match.group(1)

            # 尝试提取characters数组
            char_match = re.search(r'["\']?characters["\']?\s*:\s*\[', content)
            if char_match:
                try:
                    chars_start = char_match.end() - 1
                    chars_str = self._extract_balanced_segment(content, chars_start, '[', ']')
                    # 尝试解析角色数组
                    if chars_str:
                        data['characters'] = self._parse_json_like(chars_str)
                except:
                    pass

            scene_defs_match = re.search(r'["\']?(scene_definitions|setting_definitions|backdrop_definitions)["\']?\s*:\s*\[', content)
            if scene_defs_match:
                try:
                    defs_start = scene_defs_match.end() - 1
                    defs_str = self._extract_balanced_segment(content, defs_start, '[', ']')
                    if defs_str:
                        data['scene_definitions'] = self._parse_json_like(defs_str)
                except Exception:
                    pass

            # 尝试提取scenes数组，避免描述文本中的 ] 干扰简单计数逻辑
            scenes_start = re.search(r'["\']?scenes["\']?\s*:\s*\[', content)
            if scenes_start:
                start_pos = scenes_start.end() - 1
                scenes_str = self._extract_balanced_segment(content, start_pos, '[', ']')
                if scenes_str:
                    try:
                        data['scenes'] = self._parse_json_like(scenes_str)
                    except json.JSONDecodeError as scenes_error:
                        logger.warning(
                            "Failed to parse scenes array: %s at line=%s column=%s pos=%s",
                            scenes_error.msg,
                            scenes_error.lineno,
                            scenes_error.colno,
                            scenes_error.pos,
                        )
                        # 尝试修复常见的JSON格式问题
                        try:
                            fixed_str = self._clean_json_candidate(scenes_str)
                            data['scenes'] = self._parse_json_like(fixed_str)
                        except Exception:
                            logger.warning(f"Failed to parse scenes array, content: {scenes_str[:200]}...")
                            salvaged_scenes = self._salvage_scenes_from_content(scenes_str)
                            if salvaged_scenes:
                                logger.info("Salvaged %s scenes from malformed scenes array", len(salvaged_scenes))
                                data['scenes'] = salvaged_scenes

            # 如果 scenes 为空，尝试从内容中提取分镜信息
            if not data['scenes']:
                logger.info("Attempting to extract scenes from content using alternative method...")
                data['scenes'] = self._salvage_scenes_from_content(content) or self._extract_scenes_from_content(content)

            return data if (data['title'] or data['scenes']) else None
        except Exception as e:
            logger.error(f"Failed to extract script data: {str(e)}")
            return None

    def _extract_scenes_from_content(self, content: str) -> List[Dict[str, Any]]:
        """从内容中提取分镜信息（备用方法）"""
        import re
        scenes = []

        # 尝试匹配分镜对象
        # 匹配模式：{ "scene_number": X, "description": "...", ... }
        scene_pattern = r'\{\s*["\']?scene_number["\']?\s*:\s*(\d+)\s*,[^}]*["\']?description["\']?\s*:\s*["\']([^"\']+)["\'][^}]*\}'
        matches = re.findall(scene_pattern, content, re.DOTALL)

        for match in matches:
            try:
                scene_num = int(match[0])
                description = match[1]
                scenes.append({
                    'scene_number': scene_num,
                    'scene_name': f'场景{scene_num}',
                    'description': description,
                    'dialogue': '',
                    'duration': self.scene_duration_min,
                    'character_description': '',
                    'voice_description': '',
                    'mood': '',
                    'time_of_day': self._infer_time_of_day_from_text(description),
                    'weather': self._infer_weather_from_text(description),
                    'camera_angle': '',
                    'characters_present': []
                })
            except:
                continue

        # 如果没有匹配到，尝试更宽松的匹配
        if not scenes:
            # 尝试匹配任何包含 scene_number 的对象
            scene_blocks = re.findall(r'["\']?scene_number["\']?\s*:\s*(\d+)', content)
            for num in scene_blocks:
                scenes.append({
                    'scene_number': int(num),
                    'scene_name': f'场景{num}',
                    'scene_state': '',
                    'description': f'分镜 {num}',
                    'dialogue': '',
                    'duration': self.scene_duration_min,
                    'character_description': '',
                    'voice_description': '',
                    'mood': '',
                    'time_of_day': '',
                    'weather': '',
                    'camera_angle': '',
                    'characters_present': []
                })

        return scenes[:self.max_storyboard_scenes]

    def _salvage_scenes_from_content(self, content: str) -> List[Dict[str, Any]]:
        """从损坏的 scenes 文本中尽量抢救出逐条分镜，优先保留对白和时长。"""
        text = str(content or "")
        if not text:
            return []

        scene_markers = list(re.finditer(r'["\']?scene_number["\']?\s*:\s*\d+', text))
        if not scene_markers:
            return []

        scenes: List[Dict[str, Any]] = []
        for index, marker in enumerate(scene_markers):
            block_start = text.rfind('{', 0, marker.start())
            if block_start == -1:
                block_start = marker.start()
            block_end = scene_markers[index + 1].start() if index + 1 < len(scene_markers) else len(text)
            block = text[block_start:block_end].strip().rstrip(',').strip()
            if not block:
                continue

            parsed_block = None
            candidate_object = self._extract_json_object(block)
            if candidate_object:
                try:
                    parsed_block = self._parse_json_like(candidate_object)
                except Exception:
                    parsed_block = None

            if isinstance(parsed_block, dict):
                scene = self._normalize_salvaged_scene_dict(parsed_block, len(scenes) + 1)
                if scene:
                    scenes.append(scene)
                continue

            scene = self._extract_scene_fields_from_text(block, len(scenes) + 1)
            if scene:
                scenes.append(scene)

        deduped: List[Dict[str, Any]] = []
        seen_numbers = set()
        for scene in scenes:
            scene_number = int(scene.get('scene_number') or len(deduped) + 1)
            if scene_number in seen_numbers:
                continue
            seen_numbers.add(scene_number)
            deduped.append(scene)
        return deduped[:self.max_storyboard_scenes]

    def _normalize_salvaged_scene_dict(self, scene: Dict[str, Any], fallback_index: int) -> Optional[Dict[str, Any]]:
        """把单条抢救出的分镜字典规范成下游可接受的结构。"""
        normalized = dict(scene)
        try:
            normalized['scene_number'] = int(
                normalized.get('scene_number')
                or normalized.get('sceneNo')
                or normalized.get('scene_no')
                or fallback_index
            )
        except Exception:
            normalized['scene_number'] = fallback_index

        scene_name_aliases = ['scene_name', 'sceneName', 'scene_ref', 'sceneRef', 'scene_title', 'sceneTitle', 'location_name']
        scene_name = next((normalized.get(alias) for alias in scene_name_aliases if normalized.get(alias)), None)
        normalized['scene_name'] = str(scene_name or f"场景{normalized['scene_number']}").strip()
        normalized['description'] = str(normalized.get('description') or '').strip()

        normalized['dialogue'] = self._normalize_dialogue_field(normalized.get('dialogue'))
        self._move_dialogue_out_of_description(normalized)

        duration_match = re.search(r'\d+', str(normalized.get('duration') or ''))
        duration = int(duration_match.group()) if duration_match else self.scene_duration_min
        normalized['duration'] = max(self.scene_duration_min, min(self.scene_duration_max, duration))
        normalized['character_description'] = str(normalized.get('character_description') or '').strip()
        normalized['voice_description'] = str(normalized.get('voice_description') or '').strip()
        normalized['mood'] = str(normalized.get('mood') or '').strip()
        normalized['time_of_day'] = self._normalize_single_line(
            normalized.get('time_of_day')
            or self._infer_time_of_day_from_text(
                normalized.get('scene_name'),
                normalized.get('description'),
                normalized.get('dialogue'),
            )
        )
        normalized['weather'] = self._normalize_single_line(
            normalized.get('weather')
            or self._infer_weather_from_text(
                normalized.get('scene_name'),
                normalized.get('description'),
                normalized.get('dialogue'),
            )
        )
        normalized['scene_state'] = self._normalize_scene_state(normalized.get('time_of_day'), normalized.get('weather'))
        normalized['camera_angle'] = str(normalized.get('camera_angle') or '').strip()
        characters_present = normalized.get('characters_present')
        normalized['characters_present'] = characters_present if isinstance(characters_present, list) else []

        if not normalized['description'] and not normalized['dialogue']:
            return None
        return normalized

    def _extract_scene_fields_from_text(self, block: str, fallback_index: int) -> Optional[Dict[str, Any]]:
        """在单条分镜对象无法整体解析时，按字段正则兜底提取。"""
        text = str(block or "")
        if not text:
            return None

        scene_number_match = re.search(r'["\']?scene_number["\']?\s*:\s*(\d+)', text)
        if not scene_number_match:
            return None

        scene_number = int(scene_number_match.group(1))

        def extract_string(field_names: List[str]) -> str:
            for field_name in field_names:
                pattern = (
                    rf'["\']?{field_name}["\']?\s*:\s*["\']([\s\S]*?)["\']'
                    rf'(?=\s*,\s*["\'](?:scene_number|scene_name|sceneName|character_outfits|scene_state|description|dialogue|duration|character_description|voice_description|mood|time_of_day|weather|camera_angle|characters_present)\b|\s*\}}|\s*\])'
                )
                match = re.search(pattern, text)
                if match:
                    return match.group(1).strip()
            return ''

        def extract_duration() -> int:
            match = re.search(r'["\']?duration["\']?\s*:\s*["\']?(\d+)', text)
            if match:
                return max(self.scene_duration_min, min(self.scene_duration_max, int(match.group(1))))
            return self.scene_duration_min

        scene = {
            'scene_number': scene_number or fallback_index,
            'scene_name': extract_string(['scene_name', 'sceneName', 'scene_ref', 'sceneRef', 'scene_title', 'sceneTitle', 'location_name']) or f"场景{scene_number}",
            'description': extract_string(['description']),
            'scene_state': extract_string(['scene_state']),
            'dialogue': extract_string(['dialogue']),
            'duration': extract_duration(),
            'character_description': extract_string(['character_description']),
            'voice_description': extract_string(['voice_description']),
            'mood': extract_string(['mood']),
            'time_of_day': extract_string(['time_of_day', 'timeOfDay']),
            'weather': extract_string(['weather']),
            'camera_angle': extract_string(['camera_angle']),
            'characters_present': [],
        }
        scene['time_of_day'] = self._normalize_single_line(
            scene['time_of_day'] or self._infer_time_of_day_from_text(scene['scene_name'], scene['description'], scene['dialogue'])
        )
        scene['weather'] = self._normalize_single_line(
            scene['weather'] or self._infer_weather_from_text(scene['scene_name'], scene['description'], scene['dialogue'])
        )

        if not scene['description'] and not scene['dialogue']:
            return None
        return scene

    def refine_script(
        self,
        current_script: Script,
        feedback: str
    ) -> Script:
        """
        根据反馈修改剧本

        Args:
            current_script: 当前剧本
            feedback: 修改意见

        Returns:
            修改后的剧本
        """
        logger.log_agent_call("ScriptAgent", "refine_script")

        messages = [
            {
                "role": "system",
                "content": self._get_system_prompt()
            },
            {
                "role": "user",
                "content": f"请修改以下剧本：\n\n当前剧本：{current_script.json()}\n\n修改意见：{feedback}\n\n请输出修改后的完整剧本JSON，确保剧情连贯不跳跃。"
            }
        ]

        # 调用 seed-sc-off 修改剧本，使用 300 秒超时
        logger.info(f"Calling seed-sc-off for script refinement with timeout {self.timeout}s")
        response = llm_service.chat_completion(
            model=self.model,
            messages=messages,
            temperature=0.8,
            max_tokens=self.max_tokens,
            timeout=self.timeout
        )

        content = response['choices'][0]['message']['content']
        self._log_raw_model_response("refine_script", 1, content)
        script_data = self._parse_script(content)
        characters = [Character(**c) for c in script_data['characters']]
        scene_definitions = [SceneDefinition(**item) for item in script_data.get('scene_definitions', [])]
        scenes = [Scene(**s) for s in script_data['scenes']]

        return Script(
            title=script_data['title'],
            style=script_data['style'],
            era=script_data.get('era'),
            background=script_data['background'],
            tone=script_data.get('tone'),
            characters=characters,
            scene_definitions=scene_definitions,
            scenes=scenes,
            total_duration=sum(s.duration for s in scenes)
        )

    def _log_raw_model_response(self, stage: str, attempt: int, content: str) -> None:
        logger.info("Script model raw response (%s, attempt %s) BEGIN", stage, attempt)
        logger.info("%s", str(content or ""))
        logger.info("Script model raw response (%s, attempt %s) END", stage, attempt)

    def _fallback_scene_name_from_description(self, description: str, index: int) -> str:
        cleaned = re.sub(r'\s+', ' ', str(description or '').strip())
        if not cleaned:
            return f"场景{index}"
        short_name = re.split(r"[，。；;,.!?！？\n]", cleaned)[0].strip()
        return short_name[:24] or f"场景{index}"

    def _normalize_scene_definitions(
        self,
        raw_scene_definitions: Any,
        scenes: List[Dict[str, Any]],
        background: str,
    ) -> List[Dict[str, Any]]:
        definitions: List[Dict[str, Any]] = []
        seen = set()

        def split_scene_names(value: str) -> List[str]:
            return [part.strip() for part in re.split(r"[、,，/|]+", str(value or "")) if part.strip()]

        for item in raw_scene_definitions or []:
            if not isinstance(item, dict):
                continue
            name = self._normalize_single_line(item.get('name') or item.get('scene_name'))
            description = self._normalize_single_line(item.get('description') or item.get('scene_description'))
            time_of_day = self._normalize_single_line(
                item.get('time_of_day')
                or item.get('default_time_of_day')
                or self._infer_time_of_day_from_text(description, name)
            )
            weather = self._normalize_single_line(
                item.get('weather')
                or item.get('default_weather')
                or self._infer_weather_from_text(description, name)
            )
            scene_features = self._normalize_scene_feature_list(
                item.get('scene_features')
                or item.get('features')
                or item.get('visual_features')
            )
            if not name:
                name = self._fallback_scene_name_from_description(description or background, len(definitions) + 1)
            if not description:
                description = background or name
            for split_name in split_scene_names(name) or [name]:
                key = re.sub(r'\s+', '', split_name).lower()
                if key in seen:
                    continue
                seen.add(key)
                definitions.append({
                    'name': split_name,
                    'description': description,
                    'time_of_day': time_of_day,
                    'weather': weather,
                    'scene_features': list(scene_features),
                })

        if not definitions:
            inferred_background_time = self._infer_time_of_day_from_text(background, *(scene.get('description') for scene in scenes or []))
            inferred_background_weather = self._infer_weather_from_text(background, *(scene.get('description') for scene in scenes or []))
            fallback_name = self._fallback_scene_name_from_description(background or '主场景', 1)
            fallback_description = self._normalize_single_line(background) or fallback_name
            definitions.append({
                'name': fallback_name,
                'description': fallback_description,
                'time_of_day': inferred_background_time,
                'weather': inferred_background_weather,
                'scene_features': self._normalize_scene_feature_list(background),
            })

        return definitions

    def _backfill_scene_conditions_from_definitions(
        self,
        scenes: List[Dict[str, Any]],
        scene_definitions: List[Dict[str, Any]],
    ) -> None:
        if not scenes:
            return

        definition_map = {
            self._normalize_scene_name_key(item.get('name')): item
            for item in scene_definitions or []
            if self._normalize_scene_name_key(item.get('name'))
        }
        for scene in scenes:
            matched_definition = definition_map.get(self._normalize_scene_name_key(scene.get('scene_name')))
            if matched_definition:
                if not self._normalize_single_line(scene.get('time_of_day')):
                    scene['time_of_day'] = self._normalize_single_line(
                        matched_definition.get('time_of_day')
                        or self._infer_time_of_day_from_text(
                            matched_definition.get('description'),
                            matched_definition.get('name'),
                        )
                    )
                if not self._normalize_single_line(scene.get('weather')):
                    scene['weather'] = self._normalize_single_line(
                        matched_definition.get('weather')
                        or self._infer_weather_from_text(
                            matched_definition.get('description'),
                            matched_definition.get('name'),
                        )
                    )

            if not self._normalize_single_line(scene.get('time_of_day')):
                scene['time_of_day'] = self._infer_time_of_day_from_text(
                    scene.get('scene_name'),
                    scene.get('description'),
                    scene.get('dialogue'),
                )
            if not self._normalize_single_line(scene.get('weather')):
                scene['weather'] = self._infer_weather_from_text(
                    scene.get('scene_name'),
                    scene.get('description'),
                    scene.get('dialogue'),
                )

    def _align_scene_names_to_definitions(
        self,
        scenes: List[Dict[str, Any]],
        scene_definitions: List[Dict[str, str]],
    ) -> None:
        if not scenes:
            return
        definitions_by_key = {
            re.sub(r'\s+', '', str(item.get('name') or '')).lower(): str(item.get('name') or '').strip()
            for item in scene_definitions or []
            if str(item.get('name') or '').strip()
        }
        default_scene_name = next(iter(definitions_by_key.values()), '主场景')
        for scene in scenes:
            scene_name = str(scene.get('scene_name') or '').strip()
            normalized_key = self._normalize_scene_name_key(scene_name)
            scene['scene_name'] = definitions_by_key.get(normalized_key, scene_name or default_scene_name)

    def _normalize_character_name_key(self, value: Optional[str]) -> str:
        normalized = re.sub(r'\s+', '', str(value or '').strip().lower())
        normalized = re.sub(r'[^0-9a-z\u4e00-\u9fff_-]+', '', normalized)
        return normalized

    def _normalize_scene_name_key(self, value: Optional[str]) -> str:
        normalized = re.sub(r'\s+', '', str(value or '').strip().lower())
        normalized = re.sub(r'[^0-9a-z\u4e00-\u9fff_-]+', '', normalized)
        return normalized

    def _split_character_names(self, value: Any) -> List[str]:
        if isinstance(value, list):
            raw_items = value
        else:
            raw_items = re.split(r"[、,，/|]+", str(value or ""))

        names: List[str] = []
        seen = set()
        for item in raw_items:
            name = self._sanitize_character_candidate(item)
            if not name:
                continue
            key = self._normalize_character_name_key(name)
            if not key or key in seen:
                continue
            seen.add(key)
            names.append(name)
        return names

    def _sanitize_character_candidate(self, value: Any) -> str:
        candidate = self._strip_character_name_markers(
            str(value or '').strip().strip('“”"\' ')
        )
        if not candidate:
            return ''

        candidate = re.sub(
            r'(低声应道|轻声应道|沉声应道|厉声喝道|低声说道|轻声说道|沉声说道|冷冷说道|'
            r'声音平静|声音低沉|声音沙哑|声音颤抖|声音发紧|语气平静|语气低沉|语气冰冷|'
            r'愤怒地咆哮|愤怒咆哮|怒吼|咆哮|嘶吼|喝道|喊道|问道|说道|应道|回应|开口|出声)$',
            '',
            candidate,
        ).strip()

        candidate = re.sub(
            r'(低声|轻声|沉声|厉声|冷冷|缓缓|平静地|低沉地|沙哑地|愤怒地|冷笑着|苦笑着|微笑着)$',
            '',
            candidate,
        ).strip()

        candidate = re.sub(r'[：:，,；;。.!！?？]+$', '', candidate).strip()
        return candidate

    def _extract_character_names_from_text_field(self, text: Any) -> List[str]:
        content = str(text or '')
        if not content:
            return []

        matches = []
        for pattern in [
            r'(?:^|[\n；;])\s*([^：:\n；;，,]{1,16})\s*[：:]',
            r'([^\s：:\n；;，,]{1,16})\s*[：:]',
        ]:
            matches.extend(re.findall(pattern, content))

        names: List[str] = []
        seen = set()
        blocked = {'镜头', '画面', '场景', '情绪', '氛围', '对白', '旁白', '说明'}
        for item in matches:
            name = self._sanitize_character_candidate(item)
            key = self._normalize_character_name_key(name)
            if not key or key in seen or name in blocked:
                continue
            seen.add(key)
            names.append(name)
        return names

    def _collect_scene_character_names(self, scenes: List[Dict[str, Any]], mutate_scenes: bool = True) -> List[str]:
        ordered_names: List[str] = []
        seen = set()

        for scene in scenes or []:
            # 出场角色数量只以模型返回的 characters_present 为准；
            # 先在单个分镜内去重，再在全局去重后统计。
            raw_names = self._split_character_names(scene.get('characters_present'))

            deduped_scene_names: List[str] = []
            scene_seen = set()
            for name in raw_names:
                key = self._normalize_character_name_key(name)
                if not key or key in scene_seen:
                    continue
                scene_seen.add(key)
                deduped_scene_names.append(name)

            if mutate_scenes:
                scene['characters_present'] = deduped_scene_names

            for name in deduped_scene_names:
                key = self._normalize_character_name_key(name)
                if key in seen:
                    continue
                seen.add(key)
                ordered_names.append(name)

        return ordered_names

    def _normalize_characters(self, raw_characters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized_characters: List[Dict[str, Any]] = []
        seen_character_keys = set()

        for item in raw_characters or []:
            if not isinstance(item, dict):
                continue
            name = self._strip_character_name_markers(item.get('name'))
            key = self._normalize_character_name_key(name)
            if not key or key in seen_character_keys:
                continue
            seen_character_keys.add(key)
            normalized_characters.append({
                'name': name,
                'age': str(item.get('age') or '未知'),
                'gender': str(item.get('gender') or '未知'),
                'nationality': self._coerce_scene_text_field(
                    item.get('nationality') or item.get('country') or item.get('ethnicity') or item.get('region_identity')
                ).strip() or None,
                'face_features': str(item.get('face_features') or item.get('appearance') or '剧本中的重要角色').strip(),
                'hairstyle': self._coerce_scene_text_field(
                    item.get('hairstyle') or item.get('hair_style') or item.get('hair') or item.get('hair_features')
                ).strip() or None,
                'body_features': self._coerce_scene_text_field(
                    item.get('body_features') or item.get('body_shape') or item.get('body_type') or item.get('figure') or item.get('build')
                ).strip() or None,
                'skin_tone': str(item.get('skin_tone') or '未知').strip(),
                'clothing': str(item.get('clothing') or item.get('usual_outfit') or item.get('default_outfit') or '').strip() or None,
                'voice_type': str(item.get('voice_type') or '未知').strip(),
                'voice_features': str(item.get('voice_features') or '待补充').strip(),
                'voice_style': str(item.get('voice_style') or '待补充').strip(),
                'personality': self._coerce_scene_text_field(item.get('personality')).strip() or None,
                'identity_background': self._coerce_scene_text_field(
                    item.get('identity_background') or item.get('identity') or item.get('occupation_background') or item.get('character_background') or item.get('background')
                ).strip() or None,
            })
            if len(normalized_characters) >= self.max_characters:
                break

        return normalized_characters[:self.max_characters]

    def _normalize_scene_character_presence(self, scenes: List[Dict[str, Any]]) -> None:
        self._collect_scene_character_names(scenes, mutate_scenes=True)

    def _collect_used_scene_names(self, scenes: List[Dict[str, Any]]) -> List[str]:
        ordered_names: List[str] = []
        seen = set()
        for scene in scenes or []:
            scene_name = str(scene.get('scene_name') or '').strip()
            if not scene_name:
                continue
            for name in self._split_character_names(scene_name):
                key = self._normalize_scene_name_key(name)
                if not key or key in seen:
                    continue
                seen.add(key)
                ordered_names.append(name)
        return ordered_names
