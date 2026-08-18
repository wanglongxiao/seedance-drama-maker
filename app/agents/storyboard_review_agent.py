# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

"""
分镜故事版审核Agent
使用LLM的图像理解能力审核生成的白描多宫格故事版是否符合要求。

审核 3 个硬性条件（任一不满足即不通过，需重新生成）：
1. 是白描线稿风格，包含 6-12 幅宫格图，不一定要求是 3 行 x 3 列的排列
2. 同一个角色在同一个画面（分格）中没有重复出现；且画面中任一角色不出现多于 2 只手臂或多于 2 条腿
3. 角色的性别没有画错
"""
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from app.config import config
from app.services.llm_service import llm_service
from app.utils.i18n import language_name, translate
from app.utils.logger import get_logger

logger = get_logger("storyboard_review_agent")


class StoryboardReviewAgent:
    """分镜故事版审核Agent - 使用图像理解审核白描线稿多宫格是否合规。"""

    def __init__(self):
        # 复用视频审核模型端点（同为多模态视觉理解模型），可通过独立配置覆盖
        self.model = (
            config.get('models.storyboard_review.endpoint')
            or config.get('models.video_review.endpoint')
        )
        self.temperature = config.get('models.storyboard_review.temperature',
                                      config.get('models.video_review.temperature', 0.3))
        self.max_tokens = config.get('models.storyboard_review.max_tokens',
                                     config.get('models.video_review.max_tokens', 1000))
        self.request_retry_count = max(0, int(config.get('storyboard_review.request_retry_count', 2)))
        self.request_retry_delay_seconds = max(
            0.0, float(config.get('storyboard_review.request_retry_delay_seconds', 2))
        )
        logger.info(f"[CONFIG] Storyboard review model: {self.model}")

    def _build_cast_summary(self, characters_present: Optional[List[str]],
                            character_map: Optional[Dict[str, str]]) -> str:
        """构建角色清单（含性别）文本，供审核判定数量/性别使用。"""
        names = [str(n or "").strip() for n in (characters_present or []) if str(n or "").strip()]
        if not names:
            return "（该分镜无明确角色清单）"
        character_map = character_map or {}
        seen = set()
        parts: List[str] = []
        for name in names:
            key = name.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            gender = str(character_map.get(name, "") or "").strip()
            parts.append(f"{name}（性别：{gender}）" if gender else name)
        return f"共 {len(parts)} 个不同角色：" + "；".join(parts)

    def review_storyboard(
        self,
        image_url: str,
        scene_description: str = "",
        characters_present: Optional[List[str]] = None,
        character_gender_map: Optional[Dict[str, str]] = None,
        output_language: str = "zh-CN",
    ) -> Tuple[bool, str]:
        """
        审核故事版图片是否满足 3 个硬性条件。

        Returns:
            (is_approved: bool, feedback: str)
            - is_approved: True=通过；False=需要重新生成
            - feedback: 反馈信息，不通过时说明是哪一条不满足
        """
        logger.info(f"[SB_REVIEW] Starting storyboard review for: {str(image_url)[:100]}...")

        cast_summary = self._build_cast_summary(characters_present, character_gender_map)

        prompt_parts: List[str] = []
        prompt_parts.append("【分镜故事版图片审核任务】")
        prompt_parts.append("请使用图像理解能力观看这张分镜故事版图片，并严格审核它是否满足以下 3 个硬性条件。")
        prompt_parts.append("")
        prompt_parts.append("【本分镜角色清单（用于判断数量与性别）】")
        prompt_parts.append(cast_summary)
        if scene_description:
            prompt_parts.append("")
            prompt_parts.append("【分镜剧情参考】")
            prompt_parts.append(scene_description[:600])
        prompt_parts.append("")
        prompt_parts.append("【3 个硬性审核条件 - 每条独立判断是否满足】")
        prompt_parts.append("1. 白描线稿多宫格：整张图必须是白描线稿风格（黑白线稿/铅笔草图/分镜线稿），且包含 6 到 12 幅宫格图；不一定要求是 3 行 x 3 列的排列，横排、竖排或网格排列均可。若为彩色、写实照片风、或宫格数量少于 6 / 多于 12，则不满足。")
        prompt_parts.append("2. 角色不重复且肢体正常：同一个角色在同一个分格画面中不能重复出现（不能出现同一人的多张脸/克隆分身）；并且画面中任何一个角色都不能出现多于 2 只手臂或多于 2 条腿（不能出现多手多脚等肢体错误）。若任一分格里同一角色出现多次，或任一角色出现超过 2 只手臂 / 超过 2 条腿，则不满足。")
        prompt_parts.append("3. 性别正确：画面中角色的性别必须与上面角色清单一致，不能把男画成女或把女画成男。若有性别画错，则不满足。")
        prompt_parts.append("")
        prompt_parts.append("【判定规则 - 重要】")
        prompt_parts.append("- 3 个条件必须全部满足才算通过（approved=true）。只要有任意一条不满足，就必须判为不通过（approved=false）。")
        prompt_parts.append("- 色情、暴力、恐怖等题材本身不是扣分/不通过的理由，只按上述 3 个条件判断。")
        prompt_parts.append("")
        prompt_parts.append("【输出要求 - 必须严格按以下JSON格式输出】")
        prompt_parts.append(translate(output_language, "review.output_language_rule",
                                       language=language_name(output_language)))
        prompt_parts.append("{")
        prompt_parts.append('  "approved": <true/false - 三个条件是否全部满足>,')
        prompt_parts.append('  "feedback": "<详细反馈，说明哪些条件满足/不满足，不通过时指出具体问题>",')
        prompt_parts.append('  "checks": {')
        prompt_parts.append('    "is_line_art_storyboard": <true/false - 是否为白描线稿风格且宫格数量在6-12之间>,')
        prompt_parts.append('    "no_duplicate_character": <true/false - 同一角色是否未重复出现，且无角色出现多于2只手臂或多于2条腿>,')
        prompt_parts.append('    "gender_correct": <true/false>')
        prompt_parts.append('  }')
        prompt_parts.append("}")

        prompt = "\n".join(prompt_parts)
        logger.info(f"[SB_REVIEW] Review prompt built, length: {len(prompt)} chars")

        content: List[Dict[str, Any]] = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
        messages = [{"role": "user", "content": content}]

        try:
            result = None
            for attempt in range(self.request_retry_count + 1):
                try:
                    logger.info(
                        f"[SB_REVIEW] Calling LLM for storyboard review "
                        f"(attempt {attempt + 1}/{self.request_retry_count + 1})"
                    )
                    result = llm_service.chat_completion(
                        model=self.model,
                        messages=messages,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                    )
                    break
                except requests.exceptions.HTTPError as e:
                    status_code = e.response.status_code if e.response is not None else None
                    should_retry = attempt < self.request_retry_count and status_code in {400, 408, 429, 500, 502, 503, 504}
                    logger.warning(
                        f"[SB_REVIEW] HTTP error on attempt {attempt + 1}/{self.request_retry_count + 1}: "
                        f"status={status_code}, retry={should_retry}"
                    )
                    if not should_retry:
                        raise
                    time.sleep(self.request_retry_delay_seconds)

            if result is None:
                raise RuntimeError("storyboard review returned no result")

            if 'choices' not in result or len(result['choices']) == 0:
                logger.error("[SB_REVIEW] Storyboard review failed: no choices in response")
                return False, translate(output_language, "error.generation_failed", error="review returned no choices")

            output = result['choices'][0]['message']['content']
            logger.info(f"[SB_REVIEW] Raw LLM output: {output[:500]}...")

            try:
                review_result = json.loads(self._strip_json(output))
            except json.JSONDecodeError as e:
                logger.error(f"[SB_REVIEW] Failed to parse review result: {e}, output: {output}")
                # 解析失败时保守判为不通过，触发重新生成
                return False, translate(output_language, "error.generation_failed", error="review result parse failed")

            checks = review_result.get('checks', {}) if isinstance(review_result.get('checks'), dict) else {}
            is_approved = review_result.get('approved', None)
            feedback = review_result.get('feedback', '')

            # 以三项 check 为准重算 approved，避免模型 approved 字段与 checks 不一致
            check_values = [
                checks.get('is_line_art_storyboard'),
                checks.get('no_duplicate_character'),
                checks.get('gender_correct'),
            ]
            if all(isinstance(v, bool) for v in check_values):
                computed = all(check_values)
                if is_approved is not computed:
                    logger.info(
                        f"[SB_REVIEW] Recomputed approved from checks: {computed} (raw approved={is_approved})"
                    )
                is_approved = computed
            elif not isinstance(is_approved, bool):
                # checks 与 approved 都不可靠时，保守判为不通过
                is_approved = False

            logger.info(f"[SB_REVIEW] Parsed result - approved: {is_approved}, checks: {checks}")

            if is_approved:
                logger.info("[SB_REVIEW] Storyboard review PASSED")
            else:
                logger.warning(f"[SB_REVIEW] Storyboard review FAILED: {feedback}")
            return bool(is_approved), feedback

        except Exception as e:
            logger.error(f"[SB_REVIEW] Storyboard review failed: {str(e)}")
            return False, translate(output_language, "error.generation_failed", error=str(e))

    @staticmethod
    def _strip_json(text: str) -> str:
        """去掉可能的 markdown code fence，尽量提取 JSON 主体。"""
        s = str(text or "").strip()
        if s.startswith("```"):
            # 去掉 ```json ... ``` 包裹
            s = s.strip("`")
            if s.lower().startswith("json"):
                s = s[4:]
            s = s.strip()
        # 截取第一个 { 到最后一个 }
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return s[start:end + 1]
        return s
