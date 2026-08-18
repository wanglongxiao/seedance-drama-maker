# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import random
import re
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.config import config
from app.prompt_skill import load_optional_nsfw_prompt, load_prompt, nsfw_content_requested
from app.services.llm_service import llm_service
from app.utils.logger import get_logger
from app.models.schemas import Script, GeneratedImage, Character

logger = get_logger("image_agent")


class ImageAgent:
    """角色/场景/图片生成Agent - 调用seedream-5.0-off"""

    def __init__(self):
        self.model = config.get('models.image.endpoint')
        self.size = config.get('models.image.default_params.size', '2K')
        self.default_aspect_ratio = "16:9"
        # 并发配置
        self.concurrency_enabled = config.get('generation.concurrency.enabled', True)
        self.max_workers = config.get('generation.concurrency.image_workers', 10)

    def _should_apply_japanese_manga_live_action_style(self, user_style_info: str, script: Script) -> bool:
        """未指定明确视觉风格时，参考图默认回落到写实漫画风格。"""
        combined = " ".join(filter(None, [user_style_info or "", getattr(script, "style", "") or ""])).lower()
        combined = combined.replace("：", ":")

        explicit_visual_style_keywords = [
            "风格", "樣式", "样式", "style", "真人", "写实", "寫實", "realistic",
            "photoreal", "photorealistic", "live action", "live-action", "cinematic",
            "电影感", "電影感", "电影风格", "電影風格", "日漫", "动漫", "動畫",
            "anime", "cartoon", "水墨", "油画", "油畫", "水彩", "像素",
        ]
        if any(keyword in combined for keyword in explicit_visual_style_keywords):
            return False
        return True

    def _append_reference_style_guidance(self, prompt_parts: List[str], user_style_info: str, script: Script) -> None:
        if self._should_apply_japanese_manga_live_action_style(user_style_info, script):
            prompt_parts.extend(load_prompt("image_reference_style_guidance.md").splitlines())

    def _is_visual_style_segment(self, text: str) -> bool:
        lowered = str(text or "").lower()
        if not lowered:
            return False
        if re.fullmatch(r"\d+\s*:\s*\d+", lowered):
            return True
        style_keywords = [
            "风格", "樣式", "样式", "style", "比例", "aspect ratio",
            "真人", "写实", "寫實", "日漫", "电影", "電影", "美漫", "动漫", "動畫", "anime",
            "live action", "live-action", "realistic", "photoreal", "photorealistic", "cinematic",
            "水墨", "油画", "油畫", "水彩", "像素", "cartoon",
        ]
        return any(keyword in lowered for keyword in style_keywords)

    def _is_non_visual_generation_parameter(self, text: str) -> bool:
        lowered = str(text or "").lower()
        if not lowered:
            return True
        non_visual_keywords = [
            "对白", "對白", "旁白", "台词", "臺詞", "语音", "声音", "音色",
            "时长", "時長", "秒", "分钟", "分鐘", "auto", "自动", "自動",
            "bgm", "music", "dialogue", "narration", "voice", "duration",
        ]
        return any(keyword in lowered for keyword in non_visual_keywords)

    def _extract_reference_prompt_style(self, user_style_info: str) -> str:
        """从用户输入中仅提炼参考图需要的风格/比例/样式，不保留原始长文本。"""
        if not user_style_info:
            return ""

        text = str(user_style_info).replace("\r", "\n")
        text = re.sub(r'[*#>`_-]{2,}', '\n', text)
        text = text.replace("：", ":")

        raw_segments = re.split(r'[\n，,、；;。！？]+|(?<=\])', text)
        style_segments: List[str] = []

        for segment in raw_segments:
            cleaned = segment.strip(" \t,，。；;:-")
            if not cleaned:
                continue

            if self._is_non_visual_generation_parameter(cleaned) and not re.fullmatch(r"\d+\s*:\s*\d+", cleaned):
                continue

            if self._is_visual_style_segment(cleaned):
                style_segments.append(cleaned)

        def _dedupe(items: List[str], limit: int) -> str:
            deduped: List[str] = []
            seen = set()
            for item in items:
                normalized = re.sub(r'\s+', ' ', item).strip().lower()
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    deduped.append(item)
            return "；".join(deduped[:limit])

        style_text = _dedupe(style_segments, 6)
        logger.info(
            f"Extracted reference prompt style from {len(user_style_info)} chars to {len(style_text)} chars"
        )
        return style_text

    def _sanitize_image_private_prompt(self, text: str) -> str:
        """Remove private prompt metadata and examples that can overpower the current scene."""
        if not text:
            return ""
        kept_lines: List[str] = []
        skipping_example = False
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if re.match(r"^#+\s*示例", line):
                skipping_example = True
                continue
            if skipping_example:
                continue
            if not line:
                kept_lines.append(raw_line)
                continue
            if re.search(r"本地私有扩展|NSFW\\?_?ENABLED|公开仓库|该文件|关闭.*跳过|仅当.*加载", line):
                continue
            if re.search(r"(\\?\[.+?\\?\]\s*\+\s*){2,}\\?\[.+?\\?\]", line):
                continue
            kept_lines.append(raw_line)
        return "\n".join(kept_lines).strip()

    def _append_image_private_extensions(
        self,
        prompt_parts: List[str],
        names: List[str],
        *trigger_texts: Any,
    ) -> None:
        if not nsfw_content_requested(*trigger_texts):
            return
        extensions: List[str] = []
        for name in names:
            text = self._sanitize_image_private_prompt(load_optional_nsfw_prompt(name))
            if text:
                extensions.append(text)
        if extensions:
            prompt_parts.extend([
                "",
                "[PRIVATE VISUAL EXTENSION - BOUNDED PRIORITY]",
                "Use the following local private guidance only as supplemental visual-detail guidance. Do not override aspect ratio, identity preservation, full-body framing, background rules, storyboard line-art rules, reference usage rules, or the current scene context.",
                "\n\n".join(extensions),
            ])

    def _normalize_asset_name(self, name: Optional[str], fallback: str) -> str:
        normalized = re.sub(r"\s+", " ", str(name or "").strip())
        return normalized or fallback

    def _build_scene_reference_name(self, scene_description: str, index: int) -> str:
        cleaned = re.sub(r"\s+", " ", str(scene_description or "").strip())
        if not cleaned:
            return f"Scene {index}"
        shortened = re.split(r"[，。；;,.!?！？\n]", cleaned)[0].strip()
        return shortened[:24] or f"Scene {index}"

    def _build_character_profile_lines(self, character: Character) -> List[str]:
        """Stable character profile fields shared by all character-related image prompts."""
        field_specs = [
            ("Name", getattr(character, "name", "")),
            ("Age", getattr(character, "age", "")),
            ("Gender", getattr(character, "gender", "")),
            ("Nationality", getattr(character, "nationality", None)),
            ("Face features", getattr(character, "face_features", "")),
            ("Hairstyle", getattr(character, "hairstyle", None)),
            ("Body features", getattr(character, "body_features", None)),
            ("Skin tone", getattr(character, "skin_tone", "")),
            ("Default clothing", getattr(character, "clothing", None)),
            ("Personality", getattr(character, "personality", None)),
            ("Identity background", getattr(character, "identity_background", None)),
        ]
        return [
            f"{label}: {str(value).strip()}"
            for label, value in field_specs
            if str(value or "").strip()
        ]

    def _outfit_requires_visible_genitals(self, outfit: str) -> bool:
        text = str(outfit or "").lower()
        upper_body_only_keywords = [
            "上身全裸", "上半身全裸", "上身赤裸", "上半身赤裸",
            "上身裸露", "上半身裸露", "赤裸上身", "裸露上身",
            "topless", "shirtless", "bare chest", "bare upper body",
        ]
        lower_body_or_genital_keywords = [
            "赤裸下身", "下身赤裸", "下体裸露", "裸露下体", "下半身赤裸", "下半身裸露", "下身全裸",
            "露出生殖器", "生殖器", "阴茎", "外阴", "私处裸露", "正面裸体", "正面全裸",
            "bare genitals", "visible genitals", "penis", "vulva",
        ]
        full_nudity_keywords = [
            "全裸", "一丝不挂", "赤身裸体", "全身赤裸", "全身裸体",
            "fully nude", "naked", "full nude", "bare genitals", "visible genitals",
            "penis", "vulva",
        ]
        if any(keyword in text for keyword in lower_body_or_genital_keywords):
            return True
        if any(keyword in text for keyword in upper_body_only_keywords):
            return False
        return any(keyword in text for keyword in full_nudity_keywords)

    def _infer_scene_time_guidance(self, scene_name: str, scene_description: str) -> List[str]:
        text = " ".join([
            str(scene_name or ""),
            str(scene_description or ""),
        ]).lower()

        night_keywords = ["深夜", "夜晚", "夜里", "晚上", "午夜", "月光", "霓虹夜景", "night", "midnight", "moonlight", "evening"]
        dawn_keywords = ["凌晨", "黎明", "清晨", "晨光", "破晓", "dawn", "sunrise", "morning"]
        sunset_keywords = ["黄昏", "傍晚", "日落", "晚霞", "sunset", "dusk", "twilight"]
        winter_keywords = ["冬", "寒冬", "雪", "冰", "winter", "snow", "icy"]
        autumn_keywords = ["秋", "落叶", "金黄", "autumn", "fall", "maple"]
        summer_keywords = ["夏", "盛夏", "炎热", "蝉鸣", "summer", "sunny season"]
        spring_keywords = ["春", "花开", "新芽", "spring", "blossom"]

        guidance: List[str] = []
        if any(keyword in text for keyword in night_keywords):
            guidance.append("Time requirement: night scene with lighting, sky, and ambience matching nighttime")
        elif any(keyword in text for keyword in dawn_keywords):
            guidance.append("Time requirement: dawn or morning scene with early light and fresh atmosphere")
        elif any(keyword in text for keyword in sunset_keywords):
            guidance.append("Time requirement: sunset or dusk scene with warm low-angle light")
        else:
            guidance.append("Time requirement: default to clear daytime lighting when no explicit time cue is provided")

        if any(keyword in text for keyword in winter_keywords):
            guidance.append("Season requirement: winter atmosphere")
        elif any(keyword in text for keyword in autumn_keywords):
            guidance.append("Season requirement: autumn atmosphere")
        elif any(keyword in text for keyword in summer_keywords):
            guidance.append("Season requirement: summer atmosphere")
        elif any(keyword in text for keyword in spring_keywords):
            guidance.append("Season requirement: spring atmosphere")

        return guidance

    def generate_character_reference_image(
        self,
        character: Character,
        script: Script,
        user_style_info: str = None,
        aspect_ratio: str = None,
        user_reference_images: List[str] = None,
        variation_requirements: str = None,
    ) -> GeneratedImage:
        if not aspect_ratio:
            aspect_ratio = self.default_aspect_ratio

        prompt_parts: List[str] = [f"Aspect ratio: {aspect_ratio}"]
        reference_style_info = self._extract_reference_prompt_style(user_style_info)
        self._append_reference_style_guidance(prompt_parts, reference_style_info, script)

        if reference_style_info:
            prompt_parts.append("[REFERENCE STYLE REQUIREMENTS]")
            prompt_parts.append(reference_style_info)

        if variation_requirements:
            prompt_parts.append("[VARIATION REQUIREMENTS]")
            prompt_parts.append(variation_requirements)

        prompt_parts.append(f"[CHARACTER REFERENCE] {character.name}")
        if user_reference_images:
            prompt_parts.append("[CRITICAL] Preserve the uploaded character identity and key appearance traits.")
            prompt_parts.append("[CRITICAL] Keep the face, hairstyle, clothing details, and silhouette recognizable.")
        else:
            prompt_parts.append("[CRITICAL] Generate the character from the character definition only.")

        prompt_parts.extend(self._build_character_profile_lines(character))

        prompt_parts.extend(load_prompt("character_reference_image.md").splitlines())

        prompt = "\n".join(prompt_parts)
        response = llm_service.generate_image(
            prompt=prompt,
            model=self.model,
            size=self.size,
            image_urls=user_reference_images or None,
            ratio=aspect_ratio,
        )
        return GeneratedImage(
            scene_number=0,
            url=response["data"][0]["url"],
            prompt=prompt,
            is_reference=True,
            name=self._normalize_asset_name(character.name, "Character"),
            reference_type="character",
        )

    def generate_scene_reference_image(
        self,
        scene_name: str,
        scene_description: str,
        script: Script,
        user_style_info: str = None,
        aspect_ratio: str = None,
        user_reference_images: List[str] = None,
        variation_requirements: str = None,
        scene_features: Optional[List[str]] = None,
        time_of_day: Optional[str] = None,
        weather: Optional[str] = None,
    ) -> GeneratedImage:
        if not aspect_ratio:
            aspect_ratio = self.default_aspect_ratio

        prompt_parts: List[str] = [f"Aspect ratio: {aspect_ratio}"]
        reference_style_info = self._extract_reference_prompt_style(user_style_info)
        if reference_style_info:
            prompt_parts.append("[REFERENCE STYLE REQUIREMENTS]")
            prompt_parts.append(reference_style_info)
        else:
            prompt_parts.append(f"Story style: {script.style}")

        if variation_requirements:
            prompt_parts.append("[VARIATION REQUIREMENTS]")
            prompt_parts.append(variation_requirements)

        prompt_parts.append(f"[SCENE REFERENCE] {scene_name}")
        prompt_parts.append(f"Backdrop definition: {scene_description}")
        if time_of_day:
            prompt_parts.append(f"Time of day: {time_of_day}")
        if weather:
            prompt_parts.append(f"Weather: {weather}")
        if scene_features:
            prompt_parts.append(f"Scene features: {', '.join([str(item).strip() for item in scene_features if str(item).strip()])}")
        if not time_of_day:
            prompt_parts.extend(self._infer_scene_time_guidance(scene_name, scene_description))
        if user_reference_images:
            prompt_parts.append("[CRITICAL] Preserve the major environment layout and landmark details from uploaded images.")
        prompt_parts.extend(load_prompt("scene_reference_image.md").splitlines())

        prompt = "\n".join(prompt_parts)
        response = llm_service.generate_image(
            prompt=prompt,
            model=self.model,
            size=self.size,
            image_urls=user_reference_images or None,
            ratio=aspect_ratio,
        )
        return GeneratedImage(
            scene_number=0,
            url=response["data"][0]["url"],
            prompt=prompt,
            is_reference=True,
            name=self._normalize_asset_name(scene_name, "Scene"),
            reference_type="scene",
        )

    def generate_character_outfit_image(
        self,
        character: Character,
        outfit: str,
        base_reference_image: GeneratedImage,
        script: Script,
        user_style_info: str = None,
        aspect_ratio: str = None,
    ) -> GeneratedImage:
        """基于角色主图 + 分镜中的特殊装扮信息，生成该角色的装扮参考图。"""
        if not aspect_ratio:
            aspect_ratio = self.default_aspect_ratio

        prompt_parts: List[str] = [f"Aspect ratio: {aspect_ratio}"]
        reference_style_info = self._extract_reference_prompt_style(user_style_info)
        self._append_reference_style_guidance(prompt_parts, reference_style_info, script)
        if reference_style_info:
            prompt_parts.append("[REFERENCE STYLE REQUIREMENTS]")
            prompt_parts.append(reference_style_info)

        prompt_parts.append(f"[CHARACTER OUTFIT VARIANT] {character.name}")
        prompt_parts.append("[CRITICAL] Keep the SAME person as the reference image: preserve face, hairstyle, facial features, skin tone and identity exactly.")
        prompt_parts.extend(self._build_character_profile_lines(character))
        prompt_parts.append(f"[OUTFIT REQUIREMENT] Change ONLY the clothing/outfit to: {outfit}")
        if self._outfit_requires_visible_genitals(outfit):
            prompt_parts.append("[CRITICAL] The outfit requirement explicitly indicates bare lower body or full nudity, not merely bare upper body. If the character is male, generate a clearly nude male image with visible penis. If the character is female, generate a front-facing fully nude female image with visible vulva; pubic hair may be present or absent. Do NOT cover, censor, blur, crop out, hide with hands, or obscure the genital area.")
        prompt_parts.extend(load_prompt("character_outfit_image.md").splitlines())
        self._append_image_private_extensions(
            prompt_parts,
            ["character_outfit_image.md"],
            user_style_info,
            getattr(script, "tone", ""),
            getattr(script, "background", ""),
            outfit,
        )

        prompt = "\n".join(prompt_parts)
        base_url = getattr(base_reference_image, "url", None)
        response = llm_service.generate_image(
            prompt=prompt,
            model=self.model,
            size=self.size,
            image_urls=[base_url] if base_url else None,
            ratio=aspect_ratio,
        )
        return GeneratedImage(
            scene_number=0,
            url=response["data"][0]["url"],
            prompt=prompt,
            is_reference=True,
            name=self._normalize_asset_name(f"{character.name} - {outfit}", "Outfit"),
            reference_type="character_outfit",
        )

    def generate_scene_state_image(
        self,
        scene_name: str,
        base_reference_image: GeneratedImage,
        script: Script,
        scene_state: str = None,
        time_of_day: str = None,
        weather: str = None,
        scene_description: str = None,
        user_style_info: str = None,
        aspect_ratio: str = None,
    ) -> GeneratedImage:
        """基于场景主图 + 分镜中的布景状态/时间/天气信息，生成该场景的状态参考图。"""
        if not aspect_ratio:
            aspect_ratio = self.default_aspect_ratio

        prompt_parts: List[str] = [f"Aspect ratio: {aspect_ratio}"]
        reference_style_info = self._extract_reference_prompt_style(user_style_info)
        if reference_style_info:
            prompt_parts.append("[REFERENCE STYLE REQUIREMENTS]")
            prompt_parts.append(reference_style_info)
        else:
            prompt_parts.append(f"Story style: {script.style}")

        prompt_parts.append(f"[BACKDROP STATE VARIANT] {scene_name}")
        prompt_parts.append("[CRITICAL] Keep the SAME location as the reference image: preserve environment layout, architecture, landmarks and spatial composition exactly.")
        if scene_description:
            prompt_parts.append(f"Backdrop definition: {scene_description}")
        state_parts: List[str] = []
        if time_of_day:
            state_parts.append(f"time of day = {time_of_day}")
        if weather:
            state_parts.append(f"weather = {weather}")
        if scene_state:
            prompt_parts.append(f"Backdrop state label for naming only: {scene_state}")
        if state_parts:
            prompt_parts.append(f"[STATE REQUIREMENT] Adjust ONLY the time of day and weather to reflect: {', '.join(state_parts)}")
        prompt_parts.extend(load_prompt("backdrop_state_image.md").splitlines())

        prompt = "\n".join(prompt_parts)
        base_url = getattr(base_reference_image, "url", None)
        response = llm_service.generate_image(
            prompt=prompt,
            model=self.model,
            size=self.size,
            image_urls=[base_url] if base_url else None,
            ratio=aspect_ratio,
        )
        state_suffix = " ".join(part for part in [scene_state or "", time_of_day or "", weather or ""] if part).strip() or "state"
        return GeneratedImage(
            scene_number=0,
            url=response["data"][0]["url"],
            prompt=prompt,
            is_reference=True,
            name=self._normalize_asset_name(f"{scene_name} - {state_suffix}", "SceneState"),
            reference_type="scene_state",
        )

    def generate_key_action_reference_image(
        self,
        scene,
        script: Script,
        reference_images: Optional[List[GeneratedImage]] = None,
        user_style_info: str = None,
        aspect_ratio: str = None,
    ) -> GeneratedImage:
        """Generate a scene-level key action reference image for private adult-content enhancement."""
        if not aspect_ratio:
            aspect_ratio = self.default_aspect_ratio

        reference_images = self._sort_key_action_reference_images(reference_images)
        prompt_parts: List[str] = [f"Aspect ratio: {aspect_ratio}"]
        reference_style_info = self._extract_reference_prompt_style(user_style_info)
        self._append_reference_style_guidance(prompt_parts, reference_style_info, script)
        if reference_style_info:
            prompt_parts.append("[USER VISUAL STYLE]")
            prompt_parts.append(reference_style_info)

        prompt_parts.extend(load_prompt("key_action_reference_image.md").splitlines())

        scene_context = self._resolve_scene_definition_context(getattr(scene, "scene_name", ""), script)
        reference_context = self._build_scene_reference_context(reference_images)
        has_character_reference = self._has_any_reference_type(reference_images, "character", "character_outfit")
        has_scene_reference = self._has_any_reference_type(reference_images, "scene", "scene_state")
        has_scene_state_reference = self._has_any_reference_type(reference_images, "scene_state")
        if reference_context:
            prompt_parts.extend(reference_context)
            prompt_parts.extend(self._build_key_action_reference_priority_context(reference_images))
        if not has_character_reference:
            prompt_parts.extend(self._build_scene_character_context(scene, script))
        if not has_scene_reference and getattr(scene, "scene_name", None):
            prompt_parts.append(f"Scene name: {getattr(scene, 'scene_name', '')}")
        if scene_context["descriptions"] and not has_scene_reference:
            prompt_parts.append(f"Scene backdrop definition: {'; '.join(scene_context['descriptions'])}")
        scene_state = str(getattr(scene, "scene_state", "") or "").strip()
        if not scene_state:
            scene_state = "，".join(
                part
                for part in [scene_context["time_of_day"], scene_context["weather"]]
                if str(part or "").strip()
            )
        if scene_state and not has_scene_state_reference:
            prompt_parts.append(f"Backdrop state: {scene_state}")
        scene_outfits = getattr(scene, "character_outfits", None) or {}
        if scene_outfits:
            outfit_lines = [
                f"{name}: {outfit}"
                for name, outfit in scene_outfits.items()
                if str(name or "").strip() and str(outfit or "").strip()
            ]
            if outfit_lines:
                prompt_parts.append(f"Current character outfit and hairstyle state: {'; '.join(outfit_lines)}")
        prompt_parts.append(f"Scene description: {getattr(scene, 'description', '')}")
        prompt_parts.append(f"Character action: {getattr(scene, 'character_description', '')}")
        prompt_parts.append(f"Mood: {getattr(scene, 'mood', '')}")
        if getattr(scene, "camera_angle", None):
            prompt_parts.append(f"Camera angle: {scene.camera_angle}")
        if getattr(scene, "characters_present", None):
            prompt_parts.append(f"Characters present: {', '.join(scene.characters_present)}")
        self._append_image_private_extensions(
            prompt_parts,
            ["key_action_reference_image.md"],
            user_style_info,
            getattr(script, "tone", ""),
            getattr(script, "background", ""),
            getattr(scene, "description", ""),
            getattr(scene, "character_description", ""),
            getattr(scene, "dialogue", ""),
            scene_outfits,
        )

        prompt = "\n".join(prompt_parts)
        response = llm_service.generate_image(
            prompt=prompt,
            model=self.model,
            size=self.size,
            image_urls=[image.url for image in (reference_images or [])] or None,
            ratio=aspect_ratio,
        )
        scene_number = max(1, int(getattr(scene, "scene_number", 1) or 1))
        return GeneratedImage(
            scene_number=scene_number,
            url=response["data"][0]["url"],
            prompt=prompt,
            name=self._build_scene_asset_name(scene, "Key Action"),
            reference_type="key_action",
            is_reference=True,
        )

    def _build_scene_asset_name(self, scene, suffix: str) -> str:
        scene_number = max(1, int(getattr(scene, "scene_number", 1) or 1))
        scene_name = self._normalize_asset_name(getattr(scene, "scene_name", ""), f"Scene {scene_number}")
        return f"Scene {scene_number:02d} {suffix} - {scene_name}"[:64]

    def _normalize_lookup_key(self, value: Any) -> str:
        normalized = re.sub(r"\s+", "", str(value or "").strip().lower())
        return re.sub(r"[^0-9a-z\u4e00-\u9fff_-]+", "", normalized)

    def _split_scene_names(self, value: Any) -> List[str]:
        return [
            part.strip()
            for part in re.split(r"[、,，/|]+", str(value or ""))
            if part.strip()
        ]

    def _resolve_scene_definition_context(self, scene_name: Any, script: Script) -> Dict[str, Any]:
        name_keys = {
            self._normalize_lookup_key(name)
            for name in self._split_scene_names(scene_name)
            if self._normalize_lookup_key(name)
        }
        matched_definitions = []
        for item in getattr(script, "scene_definitions", None) or []:
            if self._normalize_lookup_key(getattr(item, "name", "")) in name_keys:
                matched_definitions.append(item)

        descriptions: List[str] = []
        scene_features: List[str] = []
        seen_descriptions = set()
        seen_features = set()
        time_of_day = ""
        weather = ""
        for item in matched_definitions:
            description = str(getattr(item, "description", "") or "").strip()
            if description and description not in seen_descriptions:
                seen_descriptions.add(description)
                descriptions.append(description)
            for feature in getattr(item, "scene_features", None) or []:
                normalized_feature = str(feature or "").strip()
                if normalized_feature and normalized_feature not in seen_features:
                    seen_features.add(normalized_feature)
                    scene_features.append(normalized_feature)
            if not time_of_day:
                time_of_day = str(getattr(item, "time_of_day", "") or "").strip()
            if not weather:
                weather = str(getattr(item, "weather", "") or "").strip()

        return {
            "descriptions": descriptions,
            "scene_features": scene_features,
            "time_of_day": time_of_day,
            "weather": weather,
        }

    def _build_scene_character_context(self, scene, script: Script) -> List[str]:
        lines: List[str] = []
        character_names = [
            str(name or "").strip()
            for name in (getattr(scene, "characters_present", None) or [])
            if str(name or "").strip()
        ]
        if not character_names:
            return lines

        lines.append("[SCENE CHARACTER DEFINITIONS]")
        character_map = {
            self._normalize_lookup_key(getattr(character, "name", "")): character
            for character in getattr(script, "characters", None) or []
        }
        for name in character_names:
            character = character_map.get(self._normalize_lookup_key(name))
            if character is None:
                lines.append(f"- {name}")
                continue
            summary = [
                f"- {character.name}",
                f"age={character.age}",
                f"gender={character.gender}",
                f"face={character.face_features}",
                f"skin={character.skin_tone}",
            ]
            if getattr(character, "nationality", None):
                summary.append(f"nationality={character.nationality}")
            if getattr(character, "hairstyle", None):
                summary.append(f"hairstyle={character.hairstyle}")
            if getattr(character, "body_features", None):
                summary.append(f"body={character.body_features}")
            if getattr(character, "clothing", None):
                summary.append(f"clothing={character.clothing}")
            if getattr(character, "personality", None):
                summary.append(f"personality={character.personality}")
            if getattr(character, "identity_background", None):
                summary.append(f"identity_background={character.identity_background}")
            lines.append(", ".join(summary))
        return lines

    def _get_reference_type_set(self, reference_images: Optional[List[GeneratedImage]]) -> set:
        return {
            str(getattr(image, "reference_type", "") or "").strip().lower()
            for image in (reference_images or [])
            if str(getattr(image, "reference_type", "") or "").strip()
        }

    def _has_any_reference_type(self, reference_images: Optional[List[GeneratedImage]], *reference_types: str) -> bool:
        type_set = self._get_reference_type_set(reference_images)
        return any(str(reference_type).strip().lower() in type_set for reference_type in reference_types)

    def _build_storyboard_cast_constraints(self, scene, script: Script) -> List[str]:
        """构建故事版专用的角色数量/性别/防重复约束。

        故事版偶发同一角色在一个画面里重复出现、或性别画错，
        因此显式列出本分镜的角色清单（数量 + 性别），并强约束：
        每个角色在每个分格中最多出现一次、严格遵守性别。
        """
        character_names = [
            str(name or "").strip()
            for name in (getattr(scene, "characters_present", None) or [])
            if str(name or "").strip()
        ]
        if not character_names:
            return []

        character_map = {
            self._normalize_lookup_key(getattr(character, "name", "")): character
            for character in getattr(script, "characters", None) or []
        }
        # 去重，保持出场顺序
        seen_keys = set()
        roster: List[str] = []
        for name in character_names:
            key = self._normalize_lookup_key(name)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            character = character_map.get(key)
            gender = str(getattr(character, "gender", "") or "").strip() if character else ""
            if gender:
                roster.append(f"{name} (gender={gender})")
            else:
                roster.append(name)

        distinct_count = len(roster)
        lines: List[str] = ["[CAST CONSTRAINTS]"]
        lines.append(
            f"This scene has EXACTLY {distinct_count} distinct main character(s): "
            + "; ".join(roster)
        )
        lines.append(
            "[CRITICAL] Draw exactly these characters. Do NOT invent extra people and do NOT drop any of them."
        )
        lines.append(
            "[CRITICAL] Each character is ONE unique person. The SAME character must NEVER appear more than once within a single panel (no duplicated/cloned faces of the same person in one cell)."
        )
        lines.append(
            "[CRITICAL] Strictly respect each character's gender exactly as listed above; never swap or mistake a character's gender."
        )
        lines.append(
            "[CRITICAL] Keep the total number of distinct people consistent with the cast list across all panels."
        )
        return lines

    def _build_scene_reference_context(self, reference_images: Optional[List[GeneratedImage]]) -> List[str]:
        prompt_parts: List[str] = []
        if not reference_images:
            return prompt_parts

        prompt_parts.append("[REFERENCE ASSETS]")
        for index, image in enumerate(reference_images, start=1):
            reference_type = str(getattr(image, "reference_type", "") or "").strip().lower() or "reference"
            if reference_type == "character":
                label = "character reference"
            elif reference_type == "character_outfit":
                label = "character outfit reference"
            elif reference_type == "scene":
                label = "scene reference"
            elif reference_type == "scene_state":
                label = "backdrop state reference"
            elif reference_type == "key_action":
                label = "key action reference"
            elif reference_type == "storyboard":
                label = "9-panel storyboard"
            else:
                label = reference_type
            prompt_parts.append(f"- Image {index}: {getattr(image, 'name', f'Reference {index}')} ({label})")
        return prompt_parts

    def _sort_key_action_reference_images(
        self,
        reference_images: Optional[List[GeneratedImage]],
    ) -> List[GeneratedImage]:
        if not reference_images:
            return []
        priority = {
            "character_outfit": 0,
            "scene_state": 1,
            "character": 2,
            "scene": 3,
            "key_action": 4,
            "storyboard": 5,
        }
        return sorted(
            list(reference_images),
            key=lambda image: (
                priority.get(str(getattr(image, "reference_type", "") or "").strip().lower(), 99),
                str(getattr(image, "name", "") or ""),
            ),
        )

    def _build_key_action_reference_priority_context(
        self,
        reference_images: Optional[List[GeneratedImage]],
    ) -> List[str]:
        ordered_images = self._sort_key_action_reference_images(reference_images)
        if not ordered_images:
            return []

        indexed = list(enumerate(ordered_images, start=1))
        outfit_refs = [f"Image {idx}" for idx, image in indexed if getattr(image, "reference_type", None) == "character_outfit"]
        state_refs = [f"Image {idx}" for idx, image in indexed if getattr(image, "reference_type", None) == "scene_state"]
        character_refs = [f"Image {idx}" for idx, image in indexed if getattr(image, "reference_type", None) == "character"]
        scene_refs = [f"Image {idx}" for idx, image in indexed if getattr(image, "reference_type", None) == "scene"]

        parts: List[str] = ["[KEY ACTION REFERENCE PRIORITY]"]
        if outfit_refs:
            parts.append(
                f"Prefer {'/'.join(outfit_refs)} for the current character outfit, hairstyle, nudity level, damage, stains, and continuity."
            )
        if state_refs:
            parts.append(
                f"Prefer {'/'.join(state_refs)} for the current backdrop state, especially time of day, weather, and environment continuity."
            )
        if character_refs:
            parts.append(
                f"Use {'/'.join(character_refs)} only as fallback identity references when a matching character outfit reference is unavailable."
            )
        if scene_refs:
            parts.append(
                f"Use {'/'.join(scene_refs)} only as fallback environment references when a matching backdrop state reference is unavailable."
            )
        parts.append(
            "Combine the scene script, the current outfit/state references above, and the NSFW key-action prompt rules to stage one decisive action frame."
        )
        return parts

    def generate_scene_storyboard_image(
        self,
        scene,
        script: Script,
        reference_images: Optional[List[GeneratedImage]] = None,
        user_style_info: str = None,
        aspect_ratio: str = None,
    ) -> GeneratedImage:
        if not aspect_ratio:
            aspect_ratio = self.default_aspect_ratio

        prompt_parts: List[str] = [f"Aspect ratio: {aspect_ratio}"]

        # 故事版必须是白描线稿 9 宫格：把强制样式约束放在最前且最显著，
        # 且刻意不注入用户的彩色/写实/电影感风格要求（会与白描线稿冲突，
        # 曾导致模型偶发输出单张彩色写实图而非多宫格白描线稿）。
        prompt_parts.extend(load_prompt("storyboard_image.md").splitlines())

        scene_context = self._resolve_scene_definition_context(getattr(scene, "scene_name", ""), script)
        reference_context = self._build_scene_reference_context(reference_images)
        has_character_reference = self._has_any_reference_type(reference_images, "character", "character_outfit")
        if reference_context:
            prompt_parts.extend(reference_context)
            prompt_parts.append("[REFERENCE USAGE] The reference images above are content/identity references only. Do NOT copy their coloring or realistic finish — redraw everything as black-and-white line art.")
        if not has_character_reference:
            prompt_parts.extend(self._build_scene_character_context(scene, script))
        prompt_parts.extend(self._build_storyboard_cast_constraints(scene, script))
        prompt_parts.append("[STORYBOARD SHEET]")
        prompt_parts.append(f"Scene name: {getattr(scene, 'scene_name', '')}")
        if scene_context["descriptions"]:
            prompt_parts.append(f"Scene backdrop definition: {'; '.join(scene_context['descriptions'])}")
        resolved_time_of_day = str(getattr(scene, "time_of_day", "") or scene_context["time_of_day"]).strip()
        resolved_weather = str(getattr(scene, "weather", "") or scene_context["weather"]).strip()
        resolved_scene_state = str(getattr(scene, "scene_state", "") or "").strip()
        if resolved_scene_state:
            prompt_parts.append(f"Current backdrop state: {resolved_scene_state}")
        if resolved_time_of_day:
            prompt_parts.append(f"Time of day: {resolved_time_of_day}")
        if resolved_weather:
            prompt_parts.append(f"Weather: {resolved_weather}")
        resolved_scene_features = list(scene_context["scene_features"])
        if resolved_scene_features:
            prompt_parts.append(f"Scene features: {', '.join(resolved_scene_features)}")
        scene_outfits = getattr(scene, "character_outfits", None) or {}
        if scene_outfits:
            outfit_lines = [
                f"{name}: {outfit}"
                for name, outfit in scene_outfits.items()
                if str(name or "").strip() and str(outfit or "").strip()
            ]
            if outfit_lines:
                prompt_parts.append(f"Current character outfit and hairstyle state: {'; '.join(outfit_lines)}")
        prompt_parts.append(f"Scene description: {getattr(scene, 'description', '')}")
        prompt_parts.append(f"Character action: {getattr(scene, 'character_description', '')}")
        prompt_parts.append(f"Mood: {getattr(scene, 'mood', '')}")
        if getattr(scene, "camera_angle", None):
            prompt_parts.append(f"Camera angle: {scene.camera_angle}")
        if getattr(scene, "characters_present", None):
            prompt_parts.append(f"Characters present: {', '.join(scene.characters_present)}")
        prompt = "\n".join(prompt_parts)
        response = llm_service.generate_image(
            prompt=prompt,
            model=self.model,
            size=self.size,
            image_urls=[image.url for image in (reference_images or [])] or None,
            ratio=aspect_ratio,
        )
        return GeneratedImage(
            scene_number=max(1, int(getattr(scene, "scene_number", 1) or 1)),
            url=response["data"][0]["url"],
            prompt=prompt,
            name=self._build_scene_asset_name(scene, "Storyboard"),
            reference_type="storyboard",
            is_reference=True,
        )

    def generate_reference_image(
        self,
        script: Script,
        user_style_info: str = None,
        aspect_ratio: str = None,
        user_reference_images: List[str] = None,
        variation_requirements: str = None,
    ) -> GeneratedImage:
        """
        第一步：生成参考图库中的角色主参考图

        逻辑：
        1. 如果chatbot用户输入中包含风格信息，则仅使用用户输入的风格生成图片
        2. 如果用户上传了图片（支持多张），对用户上传的图片进行风格化后去掉背景，仅保留主要角色的全身正面像，作为参考图库中的角色主参考图
        3. 如果用户没有上传图片，根据chatbot用户补充的描述信息（尤其是风格、场景、其他描述信息）与初始输入，
           生成无背景的风格化主要角色全身正面像作为参考图库中的角色主参考图

        参考图库会展示于预览区域，可以被下载/重新生成。待用户确认参考图库可用后再进行下一步。

        Args:
            script: 剧本对象
            user_style_info: 用户补充的风格和场景描述信息（严格使用用户输入的风格）
            aspect_ratio: 图片比例（如用户未指定，使用默认16:9）
            user_reference_images: 用户上传的参考图片URL列表（支持1-4张）

        Returns:
            生成的参考图库
        """
        # 使用默认比例如果未指定
        if not aspect_ratio:
            aspect_ratio = self.default_aspect_ratio

        # 处理单张图片的兼容性（向后兼容）
        if isinstance(user_reference_images, str):
            user_reference_images = [user_reference_images]

        logger.log_agent_call("ImageAgent", "generate_reference_image", {
            "script_title": script.title,
            "aspect_ratio": aspect_ratio,
            "has_user_style_info": bool(user_style_info),
            "num_user_reference_images": len(user_reference_images) if user_reference_images else 0,
        })

        logger.info(f"Step 1: Generating reference library")
        logger.info(f"Using aspect_ratio: {aspect_ratio}")
        logger.info(f"User style info: {user_style_info[:200] if user_style_info else 'None'}...")
        reference_prompt_style = self._extract_reference_prompt_style(user_style_info)
        logger.info(
            f"Reference-image style metadata: {reference_prompt_style[:200] if reference_prompt_style else 'None'}..."
        )
        logger.info(f"User reference images count: {len(user_reference_images) if user_reference_images else 0}")
        if user_reference_images:
            for i, url in enumerate(user_reference_images):
                logger.info(f"  Image {i+1}: {url[:100]}...")

        # 生成参考图库中的角色主参考图
        if user_reference_images and len(user_reference_images) > 0:
            # 用户上传了图片（支持多张），进行风格化后去掉背景，仅保留主要角色的全身正面像作为参考图库主图
            logger.info(f"User uploaded {len(user_reference_images)} image(s). Stylizing as reference library image - full-body front view, no background")
            reference_image = self._stylize_user_images(
                user_image_urls=user_reference_images,
                script=script,
                user_style_info=user_style_info,
                reference_style_info=reference_prompt_style,
                aspect_ratio=aspect_ratio,
                variation_requirements=variation_requirements,
            )
        else:
            # 用户没有上传图片，根据描述生成无背景的风格化主要角色全身正面像作为参考图库主图
            logger.info("Generating stylized reference library image - full-body front view, no background based on user description")
            reference_image = self._generate_reference_image_from_description(
                script=script,
                user_style_info=reference_prompt_style,
                aspect_ratio=aspect_ratio,
                variation_requirements=variation_requirements,
            )

        logger.info(f"Reference library image generated successfully: {reference_image.url}")

        return reference_image

    def generate_scene_images(
        self,
        script: Script,
        reference_image_url: str,
        user_style_info: str = None,
        aspect_ratio: str = None,
    ) -> List[GeneratedImage]:
        """
        【已废弃】第二步：生成分镜首帧图片和结尾帧

        由于流程简化，此方法不再生成分镜首帧图和结尾帧。
        视频生成现在直接使用参考图库和分镜脚本。

        保留此方法以兼容旧代码，但返回空列表。

        Args:
            script: 剧本对象
            reference_image_url: 参考图库主图URL
            user_style_info: 用户补充的风格和场景描述信息
            aspect_ratio: 图片比例

        Returns:
            空列表（流程简化后不再生成分镜图）
        """
        logger.info("generate_scene_images: 流程已简化，不再生成分镜首帧图和结尾帧")
        logger.info("视频生成将直接使用参考图库和分镜脚本")
        return []

    def _parse_image_role_mappings(self, user_style_info: str, user_image_urls: List[str]) -> Dict[str, Any]:
        """
        解析用户输入中的 @图片1 @图片2 等角色定义

        支持格式：
        - @图片1 是角色 Jamoson
        - @图片2 是角色 Red
        - @图片1 中的 角色 Jamoson 在左侧 和 角色 Red 在右侧
        - @图片1 是主角，@图片2 是配角

        Args:
            user_style_info: 用户输入的风格和场景描述信息
            user_image_urls: 用户上传的图片URL列表

        Returns:
            角色到图片的映射字典，格式：
            {
                "image_to_roles": {
                    "image_1": [{"role_name": "Jamoson", "position": "left"}],
                    "image_2": [{"role_name": "Red", "position": "right"}]
                },
                "role_to_image": {
                    "Jamoson": "image_1",
                    "Red": "image_2"
                }
            }
        """
        import re

        mappings = {
            "image_to_roles": {},
            "role_to_image": {}
        }

        if not user_style_info:
            return mappings

        # 匹配 @图片1 @图片2 等格式
        image_pattern = r'@图片(\d+)'

        # 查找所有 @图片N 的引用
        image_refs = re.findall(image_pattern, user_style_info)

        if not image_refs:
            # 如果没有显式定义，使用默认映射（按顺序）
            for i, url in enumerate(user_image_urls, 1):
                mappings["image_to_roles"][f"image_{i}"] = [{"role_name": None, "position": None}]
            return mappings

        logger.info(f"Found {len(image_refs)} image references in user input")

        # 解析每个 @图片N 后的角色定义
        for img_num_str in image_refs:
            img_num = int(img_num_str)
            img_key = f"image_{img_num}"

            # 找到 @图片N 的位置
            pattern = rf'@图片{img_num}([^@]*)'
            match = re.search(pattern, user_style_info)

            if match:
                context = match.group(1).strip()
                roles_in_image = []

                # 尝试解析角色名和位置
                # 格式1: "是角色 XXX"
                role_match = re.search(r'是角色\s+(\w+)', context)
                if role_match:
                    role_name = role_match.group(1)
                    roles_in_image.append({"role_name": role_name, "position": None})
                    mappings["role_to_image"][role_name] = img_key

                # 格式2: "中的 角色 XXX 在左侧/右侧/中间"
                multi_role_pattern = r'角色\s+(\w+)\s+在(左侧|右侧|中间|左边|右边|左|右)'
                multi_matches = re.findall(multi_role_pattern, context)
                for role_name, position in multi_matches:
                    roles_in_image.append({"role_name": role_name, "position": position})
                    mappings["role_to_image"][role_name] = img_key

                # 格式3: "是主角/配角/角色名"（简化格式）
                if not roles_in_image:
                    simple_match = re.search(r'是(\w+)', context)
                    if simple_match:
                        role_desc = simple_match.group(1)
                        roles_in_image.append({"role_name": role_desc, "position": None})

                if roles_in_image:
                    mappings["image_to_roles"][img_key] = roles_in_image

        logger.info(f"Parsed image-role mappings: {mappings}")
        return mappings

    def _stylize_user_images(
        self,
        user_image_urls: List[str],
        script: Script,
        user_style_info: str,
        reference_style_info: str,
        aspect_ratio: str,
        variation_requirements: str = None,
    ) -> GeneratedImage:
        """
        对用户上传的多张图片进行处理，生成参考图库主图

        核心逻辑：
        1. 如果用户上传了角色图片，应该保留原角色的脸部/身材/其他重要特征
        2. 只进行简单的背景移除处理，转为白底
        3. 不要重新生成角色形象，而是保留上传图片中的角色特征
        4. 支持1-9张用户上传图片

        Args:
            user_image_urls: 用户上传的图片URL列表（1-9张）
            script: 剧本对象
            user_style_info: 用户补充的风格和场景描述信息
            aspect_ratio: 图片比例

        Returns:
            生成的参考图库
        """
        # 解析用户输入中的角色映射
        role_mappings = self._parse_image_role_mappings(user_style_info, user_image_urls)

        prompt_parts = []
        num_user_images = len(user_image_urls)

        # 添加画面比例
        prompt_parts.append(f"Aspect ratio: {aspect_ratio}")
        self._append_reference_style_guidance(prompt_parts, reference_style_info, script)

        if variation_requirements:
            prompt_parts.append("\n[VARIATION REQUIREMENTS]")
            prompt_parts.append(variation_requirements)

        if reference_style_info:
            prompt_parts.append("\n[REFERENCE STYLE REQUIREMENTS]")
            prompt_parts.append(reference_style_info)

        # 【最高优先级 - 保留上传图片中的角色特征】
        prompt_parts.append("[CRITICAL - PRESERVE ORIGINAL CHARACTERS]")
        prompt_parts.append("[CRITICAL] The uploaded images contain the EXACT characters to use")
        prompt_parts.append("[CRITICAL] DO NOT generate new characters or change the character designs")
        prompt_parts.append("[CRITICAL] Keep the EXACT same facial features, body shape, clothing, colors from uploaded images")
        prompt_parts.append("[CRITICAL] Preserve all unique characteristics: hairstyle, accessories, markings, logos, colors")
        prompt_parts.append("[CRITICAL] The character in the output must be IDENTICAL to the character in the input image")

        # 分析角色类型（人类 vs 非人类）
        human_chars = []
        non_human_chars = []

        if script.characters:
            for char in script.characters:
                # 判断是否为非人类角色（根据名称、描述中的关键词）
                char_desc = f"{char.name} {char.face_features} {char.clothing or ''}".lower()
                non_human_keywords = ['熊', 'cat', 'dog', 'animal', 'pet', 'creature', 'monster', '波波', 'panda', 'rabbit', 'fox', 'wolf', 'lion', 'tiger', 'elephant', 'bird', 'fish']

                is_non_human = any(keyword in char_desc for keyword in non_human_keywords)

                if is_non_human:
                    non_human_chars.append(char)
                else:
                    human_chars.append(char)

        logger.info(f"Character analysis: {len(human_chars)} human characters, {len(non_human_chars)} non-human characters")
        logger.info(f"User uploaded {num_user_images} image(s) for character reference")
        logger.info(f"Role mappings: {role_mappings}")

        # 【关键 - 角色保留说明】
        prompt_parts.append("\n[CHARACTER PRESERVATION RULES]")
        prompt_parts.append("[CRITICAL] Process ALL characters from the uploaded images")
        prompt_parts.append("[CRITICAL] Keep characters in their original form - only remove background")

        # 处理人类角色（保留上传图片中的角色）
        if human_chars:
            for i, char in enumerate(human_chars):
                # 检查角色是否有对应的图片映射
                char_img_key = role_mappings["role_to_image"].get(char.name)

                if char_img_key:
                    # 从映射中提取图片编号
                    img_num = int(char_img_key.replace("image_", ""))
                    img_index = img_num - 1

                    if img_index < num_user_images:
                        # 有对应的用户图片
                        prompt_parts.append(f"\n[CHARACTER {i+1}: {char.name} - FROM REFERENCE IMAGE {img_num}]")
                        prompt_parts.append(f"[CRITICAL] Use the EXACT character from reference image {img_num}:")
                        prompt_parts.append("  - IDENTICAL facial features, expression, and identity")
                        prompt_parts.append("  - IDENTICAL body shape, proportions, and posture")
                        prompt_parts.append("  - IDENTICAL clothing, colors, and accessories")
                        prompt_parts.append("  - IDENTICAL hairstyle and unique markings")
                        prompt_parts.append("[CRITICAL] DO NOT change or stylize the character - preserve it exactly as shown")

                        # 检查是否有位置信息
                        roles_in_img = role_mappings["image_to_roles"].get(char_img_key, [])
                        for role_info in roles_in_img:
                            if role_info.get("role_name") == char.name and role_info.get("position"):
                                prompt_parts.append(f"[INFO] This character is located at the {role_info['position']} of image {img_num}")
                    else:
                        char_img_key = None

                if not char_img_key:
                    # 没有对应的用户图片映射，尝试按顺序匹配
                    if i < num_user_images:
                        img_num = i + 1
                        prompt_parts.append(f"\n[CHARACTER {i+1}: {char.name} - FROM REFERENCE IMAGE {img_num}]")
                        prompt_parts.append(f"[CRITICAL] Use the EXACT character from reference image {img_num}")
                        prompt_parts.append("[CRITICAL] Preserve all original features exactly - do not modify")
                    else:
                        # 没有对应的用户图片，根据文本生成
                        prompt_parts.append(f"\n[CHARACTER {i+1}: {char.name} - GENERATE FROM CHARACTER DEFINITION]")
                        prompt_parts.append("[NOTE] No reference image for this character")
                        prompt_parts.append("[RULE] Generate based on the character definition only")

                prompt_parts.append(f"\nCharacter Details (for reference):")
                prompt_parts.append(f"  Name: {char.name}")
                prompt_parts.append(f"  Role: {char.age}{char.gender}")
                if char.clothing:
                    prompt_parts.append(f"  Costume: {char.clothing}")
                if char.face_features:
                    prompt_parts.append(f"  Features: {char.face_features}")

        # 处理非人类角色（同样保留上传图片中的特征）
        if non_human_chars:
            prompt_parts.append("\n[NON-HUMAN CHARACTERS - PRESERVE FROM IMAGES]")
            prompt_parts.append("[CRITICAL] For non-human characters, also preserve from reference images if available")

            for i, char in enumerate(non_human_chars, 1):
                prompt_parts.append(f"\nNon-Human Character {i}: {char.name}")
                prompt_parts.append(f"  Description: {char.face_features}")
                if char.clothing:
                    prompt_parts.append(f"  Features: {char.clothing}")
                prompt_parts.append(f"  [RULE] Preserve exact appearance from reference image if available")

        # 【关键 - 只进行背景移除，不改变角色】
        prompt_parts.append("\n[CRITICAL - BACKGROUND REMOVAL ONLY]")
        prompt_parts.append("[CRITICAL] ONLY task: Remove the background and replace with pure white")
        prompt_parts.append("[CRITICAL] DO NOT change the character design, style, or appearance")
        prompt_parts.append("[CRITICAL] DO NOT re-interpret or re-imagine the character")
        prompt_parts.append("[CRITICAL] Keep the character EXACTLY as it appears in the uploaded image")
        prompt_parts.append("[CRITICAL] Remove ONLY the background, keep ALL character details intact")
        prompt_parts.append("[CRITICAL] If the image already has white background, preserve the character as-is")

        # 【关键 - 正面形象要求】
        prompt_parts.append("\n[POSE REQUIREMENT]")
        prompt_parts.append("[CRITICAL] Convert all characters to front-facing pose if they are not already")
        prompt_parts.append("[CRITICAL] Maintain the EXACT same character appearance, just change the viewing angle to front")
        prompt_parts.append("[CRITICAL] Full-body front view, facing forward, showing every character from head to feet")
        prompt_parts.append("[CRITICAL] Do NOT crop the knees, waist, shoulders, head, hands or feet")
        prompt_parts.append("[CRITICAL] Keep facial features clear and recognizable even in the full-body composition")

        # 总角色数
        total_chars = len(human_chars) + len(non_human_chars)
        if total_chars > 0:
            prompt_parts.append(f"\n[CRITICAL] Show exactly {total_chars} different main characters")
            prompt_parts.append("[CRITICAL] Each character must match its reference image exactly")

        prompt_parts.append("Pure white background, high quality, detailed")
        prompt_parts.append("Full-body front view, face clearly visible, detailed facial features")
        prompt_parts.append("This is a reference image for video generation - characters must be recognizable")

        prompt = "\n".join(prompt_parts)

        logger.info(f"Processing {num_user_images} user image(s) with prompt: {prompt[:300]}...")
        logger.info(f"Using aspect ratio: {aspect_ratio}")

        # 使用所有用户图片作为参考进行处理（最多9张）
        image_urls_to_use = user_image_urls[:9]
        logger.info(f"Using {len(image_urls_to_use)} reference image(s) for processing")

        response = llm_service.generate_image(
            prompt=prompt,
            model=self.model,
            size=self.size,
            image_urls=image_urls_to_use,
            ratio=aspect_ratio
        )

        image_url = response['data'][0]['url']
        logger.info(f"User images processed successfully as reference library image - {total_chars} characters preserved using {len(image_urls_to_use)} reference image(s)")

        return GeneratedImage(
            scene_number=0,
            url=image_url,
            prompt=prompt,
            is_end_frame=False,
            is_reference=True
        )

    def _generate_reference_image_from_description(
        self,
        script: Script,
        user_style_info: str,
        aspect_ratio: str,
        variation_requirements: str = None,
    ) -> GeneratedImage:
        """根据描述生成参考图库主图 - 无背景的风格化所有主要角色全身正面像参考图"""
        prompt_parts = []

        # 添加画面比例
        prompt_parts.append(f"Aspect ratio: {aspect_ratio}")
        self._append_reference_style_guidance(prompt_parts, user_style_info, script)

        if variation_requirements:
            prompt_parts.append("[VARIATION REQUIREMENTS]")
            prompt_parts.append(variation_requirements)

        # 仅保留提炼后的参考图风格要点，不再直接拼接原始长文本
        if user_style_info:
            prompt_parts.append("[REFERENCE STYLE REQUIREMENTS]")
            prompt_parts.append(user_style_info)
        else:
            prompt_parts.append(f"Story style: {script.style}")

        # 【关键 - 无背景要求】
        prompt_parts.append("[CRITICAL - NO BACKGROUND] Pure white/transparent background ONLY")
        prompt_parts.append("[CRITICAL] No environment, no scenery, no background elements whatsoever")
        prompt_parts.append("[CRITICAL] Characters isolated on plain white background")

        # 添加角色描述（根据角色数量决定）
        num_characters = len(script.characters) if script.characters else 0
        if num_characters > 0:
            if num_characters == 1:
                # 只有一个角色
                char = script.characters[0]
                prompt_parts.append("Main character reference image (full-body front view, head-to-feet visible):")
                prompt_parts.append("[CRITICAL] Show exactly ONE character only")
                prompt_parts.append("[CRITICAL] Do NOT duplicate the character, show only ONE instance")
                char_desc = f"{char.name}: {char.age} {char.gender}, {char.face_features}, {char.skin_tone}"
                prompt_parts.append(char_desc)
                if char.clothing:
                    prompt_parts.append(f"Wearing: {char.clothing}")
            else:
                # 多个角色
                prompt_parts.append("Main characters reference image (full-body front view, head-to-feet visible):")
                prompt_parts.append(f"[CRITICAL] Show exactly {num_characters} different main characters")
                prompt_parts.append("[CRITICAL] Each character should be distinct and unique, NO duplicates")
                for i, char in enumerate(script.characters, 1):
                    char_desc = f"Character {i} - {char.name}: {char.age} {char.gender}, {char.face_features}, {char.skin_tone}"
                    prompt_parts.append(char_desc)
                    if char.clothing:
                        prompt_parts.append(f"  Wearing: {char.clothing}")

        prompt_parts.append("Full-body front view, facing camera, clear facial features")
        prompt_parts.append("Show every character from head to feet, with hands and feet visible")
        prompt_parts.append("Do NOT crop the knees, waist, shoulders, head, hands or feet")
        prompt_parts.append("Keep facial features clear and recognizable in the full-body composition")
        prompt_parts.append("Consistent character design, standalone figures on white background")
        prompt_parts.append("This is a stylized reference image for subsequent scene generation")
        prompt_parts.append("High quality, detailed, 8k resolution, white background")

        prompt = ", ".join(prompt_parts)

        logger.info(f"Generating reference library image with prompt: {prompt[:200]}...")
        logger.info(f"Using aspect ratio: {aspect_ratio}")

        response = llm_service.generate_image(
            prompt=prompt,
            model=self.model,
            size=self.size,
            ratio=aspect_ratio
        )

        image_url = response['data'][0]['url']

        logger.info("Reference library image generated successfully - all main characters full-body front view, no background")

        return GeneratedImage(
            scene_number=0,
            url=image_url,
            prompt=prompt,
            is_end_frame=False,
            is_reference=True
        )

    def _generate_images_concurrent(self, tasks: List[Dict]) -> List[GeneratedImage]:
        """并发生成图片"""
        images = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_task = {
                executor.submit(self._generate_single_image_with_reference, task): task
                for task in tasks
            }

            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    image = future.result()
                    images.append(image)
                    logger.info(f"Image generated: {task['type']} {task['scene_number']}")
                except Exception as e:
                    logger.error(f"Failed to generate image for {task['type']} {task['scene_number']}: {str(e)}")
                    raise

        return images

    def _generate_images_sequential(self, tasks: List[Dict]) -> List[GeneratedImage]:
        """串行生成图片"""
        images = []

        for task in tasks:
            try:
                image = self._generate_single_image_with_reference(task)
                images.append(image)
                logger.info(f"Image generated: {task['type']} {task['scene_number']}")
            except Exception as e:
                logger.error(f"Failed to generate image for {task['type']} {task['scene_number']}: {str(e)}")
                raise

        return images

    def _generate_single_image_with_reference(self, task: Dict) -> GeneratedImage:
        """生成单张图片（使用参考图）"""
        scene_number = task['scene_number']
        prompt = task['prompt']
        is_end_frame = task['is_end_frame']
        reference_image = task.get('reference_image')
        aspect_ratio = task.get('aspect_ratio', '16:9')

        label = f"结尾帧" if is_end_frame else f"分镜 {scene_number}"
        logger.info(f"Generating {label} image with reference image")
        logger.info(f"Using aspect ratio: {aspect_ratio}")

        # 使用参考图生成（如果提供了参考图）
        response = llm_service.generate_image(
            prompt=prompt,
            model=self.model,
            size=self.size,
            image_urls=[reference_image] if reference_image else None,
            ratio=aspect_ratio
        )

        image_url = response['data'][0]['url']

        logger.info(f"{label} image generated successfully")

        return GeneratedImage(
            scene_number=scene_number,
            url=image_url,
            prompt=prompt,
            is_end_frame=is_end_frame
        )

    def _build_reference_prompt(
        self,
        script: Script,
        user_style_info: str,
        aspect_ratio: str,
    ) -> str:
        """构建参考图库提示词 - 无背景角色全身正面像"""
        prompt_parts = []

        # 添加画面比例
        prompt_parts.append(f"Aspect ratio: {aspect_ratio}")
        self._append_reference_style_guidance(prompt_parts, user_style_info, script)

        # 仅保留提炼后的参考图风格要点，不再直接拼接原始长文本
        if user_style_info:
            prompt_parts.append("[REFERENCE STYLE REQUIREMENTS]")
            prompt_parts.append(user_style_info)
        else:
            prompt_parts.append(f"Story style: {script.style}")

        # 【关键 - 无背景要求】
        prompt_parts.append("[CRITICAL - NO BACKGROUND] Pure white/transparent background ONLY")
        prompt_parts.append("[CRITICAL] No environment, no scenery, no background elements whatsoever")
        prompt_parts.append("[CRITICAL] Character isolated on plain white background")

        # 添加角色描述（根据角色数量决定）
        num_characters = len(script.characters) if script.characters else 0
        if num_characters > 0:
            if num_characters == 1:
                # 只有一个角色
                char = script.characters[0]
                prompt_parts.append("Main character reference image (full-body front view, head-to-feet visible):")
                prompt_parts.append("[CRITICAL] Show exactly ONE character only")
                prompt_parts.append("[CRITICAL] Do NOT duplicate the character, show only ONE instance")
                char_desc = f"{char.name}: {char.age} {char.gender}, {char.face_features}, {char.skin_tone}"
                prompt_parts.append(char_desc)
                if char.clothing:
                    prompt_parts.append(f"Wearing: {char.clothing}")
            else:
                # 多个角色
                prompt_parts.append("Main characters reference image (full-body front view, head-to-feet visible):")
                prompt_parts.append(f"[CRITICAL] Show exactly {num_characters} different main characters")
                prompt_parts.append("[CRITICAL] Each character should be distinct and unique, NO duplicates")
                for i, char in enumerate(script.characters, 1):
                    char_desc = f"Character {i} - {char.name}: {char.age} {char.gender}, {char.face_features}, {char.skin_tone}"
                    prompt_parts.append(char_desc)
                    if char.clothing:
                        prompt_parts.append(f"  Wearing: {char.clothing}")

        prompt_parts.append("Full-body front view, facing camera, clear facial features")
        prompt_parts.append("Show every character from head to feet, with hands and feet visible")
        prompt_parts.append("Do NOT crop the knees, waist, shoulders, head, hands or feet")
        prompt_parts.append("Keep facial features clear and recognizable in the full-body composition")
        prompt_parts.append("Consistent character design, standalone figures on white background")
        prompt_parts.append("This is a stylized reference image for subsequent scene generation")
        prompt_parts.append("High quality, detailed, 8k resolution, white background")

        return ", ".join(prompt_parts)

    def _build_end_frame_prompt(
        self,
        script: Script,
        user_style_info: str,
        aspect_ratio: str,
        reference_image_url: str = None,
    ) -> str:
        """构建结尾帧提示词"""
        prompt_parts = []

        # 添加画面比例
        prompt_parts.append(f"Aspect ratio: {aspect_ratio}")

        # 【最高优先级 - 严格使用用户风格】
        if user_style_info:
            prompt_parts.append(f"[CRITICAL - STRICT] Style: {user_style_info}")
            prompt_parts.append("[RULE] Use ONLY the above style for the ending frame")
            prompt_parts.append("[RULE] DO NOT mix with other styles")
        else:
            prompt_parts.append(f"Story style: {script.style}")

        # 【关键 - 使用参考图角色】
        if reference_image_url:
            prompt_parts.append("\n[CRITICAL - USE REFERENCE IMAGE CHARACTERS]")
            prompt_parts.append("[CRITICAL] The reference image shows the MAIN CHARACTERS - USE THESE EXACTLY")
            prompt_parts.append("[CRITICAL] Include the characters from the reference image in the ending scene")
            prompt_parts.append("[CRITICAL] Use the EXACT facial features, clothing, and appearance from the reference image")
            prompt_parts.append("[CRITICAL] Characters must match the reference image perfectly")

        # 根据剧本风格生成结尾画面
        prompt_parts.append(f"Ending scene for: {script.title}")
        if getattr(script, "era", None):
            prompt_parts.append(f"Era: {script.era}")
        prompt_parts.append(f"Background: {script.background}")

        # 生成带有"The End"的结尾画面
        prompt_parts.append("A cinematic closing title card with 'The End' text")
        prompt_parts.append("Elegant typography, centered text, movie poster style")
        prompt_parts.append("The text 'The End' is clearly visible and prominent")
        prompt_parts.append("Beautiful background matching the story theme and specified style")
        prompt_parts.append("Professional film ending credits style")

        prompt_parts.append("High quality, 8k resolution, cinematic lighting")

        return ", ".join(prompt_parts)

    def _build_reference_regeneration_requirements(
        self,
        has_user_reference_images: bool,
        feedback: str = None,
    ) -> str:
        """构建参考图重新生成时的多样化约束。"""
        variation_groups = [
            [
                "change the pose significantly while keeping the character identity",
                "use a clearly different body gesture and hand movement",
            ],
            [
                "change the facial expression noticeably",
                "use a different emotional state and eye expression",
            ],
            [
                "change the camera framing and composition",
                "use a different crop, distance, or character placement",
            ],
            [
                "change the outfit styling details without changing the core identity",
                "use different clothing combinations or accessory emphasis while preserving identity",
            ],
            [
                "change the lighting and color mood",
                "use a different light direction and highlight treatment",
            ],
        ]
        chosen_variations = [random.choice(group) for group in random.sample(variation_groups, k=3)]

        prompt_parts = [
            "Create a clearly fresh alternative reference image instead of repeating the last attempt.",
            "Make the new result noticeably different in visual presentation.",
            "Apply these diversity directions:",
        ]
        prompt_parts.extend(f"- {item}" for item in chosen_variations)

        if has_user_reference_images:
            prompt_parts.extend([
                "- Base the character ONLY on the user-uploaded source images.",
                "- Preserve the identity, face, body shape, hairstyle, and signature features from the uploaded images.",
                "- You may change pose, expression, composition, lighting, and styling details, but do not replace the character.",
            ])
        else:
            prompt_parts.extend([
                "- Generate from text description ONLY.",
                "- Do NOT use any previous generated image as reference.",
                "- Do NOT try to match the previous generated image.",
                "- Build a fresh character presentation purely from the written description and style requirements.",
            ])

        if feedback:
            prompt_parts.extend([
                "- Additional user feedback to follow:",
                feedback,
            ])

        return "\n".join(prompt_parts)

    def regenerate_reference_image(
        self,
        script: Script,
        feedback: str,
        user_style_info: str = None,
        aspect_ratio: str = None,
        user_reference_images: List[str] = None,
    ) -> GeneratedImage:
        """重新生成参考图库，并显式提高多样性。"""
        if not aspect_ratio:
            aspect_ratio = self.default_aspect_ratio

        if isinstance(user_reference_images, str):
            user_reference_images = [user_reference_images]

        variation_requirements = self._build_reference_regeneration_requirements(
            has_user_reference_images=bool(user_reference_images),
            feedback=feedback,
        )

        logger.log_agent_call("ImageAgent", "regenerate_reference_image", {
            "aspect_ratio": aspect_ratio,
            "has_user_reference_images": bool(user_reference_images),
            "reference_image_count": len(user_reference_images) if user_reference_images else 0,
        })

        return self.generate_reference_image(
            script=script,
            user_style_info=user_style_info,
            aspect_ratio=aspect_ratio,
            user_reference_images=user_reference_images,
            variation_requirements=variation_requirements,
        )

    def regenerate_character_reference_image(
        self,
        character: Character,
        script: Script,
        feedback: str,
        user_style_info: str = None,
        aspect_ratio: str = None,
        user_reference_images: List[str] = None,
    ) -> GeneratedImage:
        variation_requirements = self._build_reference_regeneration_requirements(
            has_user_reference_images=bool(user_reference_images),
            feedback=feedback,
        )
        return self.generate_character_reference_image(
            character=character,
            script=script,
            user_style_info=user_style_info,
            aspect_ratio=aspect_ratio,
            user_reference_images=user_reference_images,
            variation_requirements=variation_requirements,
        )

    def regenerate_scene_reference_image(
        self,
        scene_name: str,
        scene_description: str,
        script: Script,
        feedback: str,
        user_style_info: str = None,
        aspect_ratio: str = None,
        user_reference_images: List[str] = None,
    ) -> GeneratedImage:
        variation_requirements = self._build_reference_regeneration_requirements(
            has_user_reference_images=bool(user_reference_images),
            feedback=feedback,
        )
        return self.generate_scene_reference_image(
            scene_name=scene_name,
            scene_description=scene_description,
            script=script,
            user_style_info=user_style_info,
            aspect_ratio=aspect_ratio,
            user_reference_images=user_reference_images,
            variation_requirements=variation_requirements,
        )

    def regenerate_image(
        self,
        scene_number: int,
        script: Script,
        feedback: str,
        user_style_info: str = None,
        aspect_ratio: str = None,
        reference_image_url: str = None,
    ) -> GeneratedImage:
        """
        根据反馈重新生成某一场景的图片
        """
        if not aspect_ratio:
            aspect_ratio = self.default_aspect_ratio

        logger.log_agent_call("ImageAgent", "regenerate_image", {
            "scene_number": scene_number,
            "feedback": feedback,
            "aspect_ratio": aspect_ratio,
            "has_reference": bool(reference_image_url)
        })

        if scene_number == 0:
            logger.info("Scene 0 regeneration delegated to regenerate_reference_image")
            return self.regenerate_reference_image(
                script=script,
                feedback=feedback,
                user_style_info=user_style_info,
                aspect_ratio=aspect_ratio,
                user_reference_images=[reference_image_url] if reference_image_url else None,
            )

        else:
            # 其他场景编号（包括999结尾帧）不再支持重新生成
            # 因为分镜图和结尾帧生成功能已废弃
            logger.warning(f"Scene {scene_number} regeneration not supported - feature deprecated")
            raise ValueError(f"Scene {scene_number} regeneration not supported. Only reference image (scene 0) can be regenerated.")
