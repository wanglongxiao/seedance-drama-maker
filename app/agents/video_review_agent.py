# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

"""
视频审核Agent
使用LLM的视频理解能力审核生成的视频是否符合剧本要求
通过语义分析对比计算分数，只要文字表达的意思相似即可
"""
import json
import re
import time
from typing import Dict, Any, List, Optional, Tuple
import requests
from app.config import config
from app.prompt_skill import render_prompt
from app.services.llm_service import llm_service
from app.services.video_skill_service import video_skill_service, review_video_input
from app.utils.i18n import language_name, translate
from app.utils.logger import get_logger
from app.utils.visual_review import DIGIT_EVIDENCE, concerns_digits, anatomy_review_options

logger = get_logger("video_review_agent")


class VideoReviewUnavailableError(RuntimeError):
    """Raised when the review service cannot evaluate a generated video."""


class VideoReviewAgent:
    """视频审核Agent - 使用视频理解审核生成结果"""

    def __init__(self):
        # 使用 YAML 中当前启用的视频审核模型
        self.model = config.get('models.video_review.endpoint')
        # 审核通过阈值（0-100分）
        self.pass_threshold = config.get('video_review.pass_threshold', 80)
        self.temperature = config.get('models.video_review.temperature', 0.3)
        self.max_tokens = config.get('models.video_review.max_tokens', 1000)
        self.request_retry_count = max(0, int(config.get('video_review.request_retry_count', 2)))
        self.request_retry_delay_seconds = max(0.0, float(config.get('video_review.request_retry_delay_seconds', 2)))
        # 审核维度权重（仅使用 3 项）
        raw_weights = config.get('video_review.weights', {
            'character_consistency': 0.40,
            'physical-laws': 0.30,
            'script_semantic_consistency': 0.30
        })
        self.weights = self._normalize_weights(raw_weights)
        logger.info(f"[CONFIG] Review pass threshold: {self.pass_threshold}")
        logger.info(f"[CONFIG] Review weights: {self.weights}")

    def _normalize_weights(self, weights: Dict[str, Any]) -> Dict[str, float]:
        """仅保留 3 个维度，并归一化权重（避免配置不一致导致分数异常）"""
        keys = ["character_consistency", "physical-laws", "script_semantic_consistency"]
        filtered: Dict[str, float] = {}
        for k in keys:
            try:
                v = float(weights.get(k, 0))
            except Exception:
                v = 0.0
            filtered[k] = max(v, 0.0)

        s = sum(filtered.values())
        if s <= 0:
            # fallback
            return {
                "character_consistency": 0.40,
                "physical-laws": 0.30,
                "script_semantic_consistency": 0.30,
            }
        return {k: filtered[k] / s for k in keys}

    def _compute_score_from_details(self, details: Dict[str, Any]) -> int:
        """使用 3 项评分 + 权重计算综合分（0-100）"""
        total = 0.0
        for k, w in self.weights.items():
            try:
                v = float(details.get(k, 0))
            except Exception:
                v = 0.0
            v = max(0.0, min(100.0, v))
            total += v * w
        return int(round(total))

    def review_video(
        self,
        script_scene_description: str,
        video_url: str,
        reference_image_url: str = None,
        output_language: str = "zh-CN",
        reference_image_urls: Optional[List[str]] = None,
        repair_requirement: Optional[str] = None,
        boundary_image_urls: Optional[List[str]] = None,
    ) -> Tuple[bool, str, int]:
        """
        审核视频是否符合剧本要求

        Args:
            script_scene_description: 剧本中该分镜的描述
            video_url: 生成的视频URL
            reference_image_url: 兼容旧调用的单张角色参考图URL
            reference_image_urls: 当前分镜各角色参考图URL；装扮图应替代同角色主图

        Returns:
            (is_approved: bool, feedback: str, score: int)
            - is_approved: True=通过，False=需要重新生成
            - feedback: 反馈信息，如果不通过，说明原因
            - score: 综合评分（0-100分）
        """
        normalized_reference_urls: List[str] = []
        for url in list(reference_image_urls or []) + ([reference_image_url] if reference_image_url else []):
            normalized_url = str(url or "").strip()
            if normalized_url and normalized_url not in normalized_reference_urls:
                normalized_reference_urls.append(normalized_url)

        logger.info("[REVIEW] Starting video review: description_chars=%s",
                    len(script_scene_description or ""))
        logger.info(f"[REVIEW] Character reference image count: {len(normalized_reference_urls)}")

        reference_image_section = ""
        if normalized_reference_urls:
            reference_image_section = (
                "【角色参考】\n"
                "以下图片是当前分镜各角色的有效参考图。若提供的是角色装扮图，"
                "人物一致性必须以该装扮图中的服装、发型、裸露层级和外观为准，"
                "不得改用角色主图中的默认服装。\n"
            )

        prompt = render_prompt(
            "video_review.md",
            script_scene_description=script_scene_description,
            reference_image_section=reference_image_section,
            pass_threshold=self.pass_threshold,
            output_language_rule=translate(
                output_language,
                "review.output_language_rule",
                language=language_name(output_language),
            ),
            character_consistency_weight=f"{self.weights['character_consistency']:.2f}",
            physical_laws_weight=f"{self.weights['physical-laws']:.2f}",
            script_semantic_consistency_weight=f"{self.weights['script_semantic_consistency']:.2f}",
        )
        logger.info(f"[REVIEW] Review prompt built, length: {len(prompt)} chars")
        prompt += video_skill_service.guidance("review")
        prompt += (
            "\nInclude an optional JSON field hard_failures: an array of concrete timecoded evidence "
            "for clearly visible extra limbs/fingers, fused hands, impossible object contact or core causal "
            "contradictions. Use [] when none is confirmed. Each entry must be an object with evidence "
            "and confirmed=true. A confirmed hard failure requires approved=false regardless of average score."
            "\nMODERATE STORY SEMANTICS: judge the character's actual identity, motivation, "
            "central action and outcome. Harmless camera moves, redundant gestures and alternative "
            "prop positions are advisory when the same causal story remains understandable. "
            "Do not fail a coherent scene merely because paper was picked up from a desk instead "
            "of a printer, or hidden differently. A prop change that contradicts an indispensable "
            "next action is a real continuity defect and must still be reported with timed evidence. "
            "Do not infer missing fingers from occlusion or duplicate characters from plausible "
            "glass reflections. Require observable contradictory contours or events."
        )
        if repair_requirement:
            prompt += (
                "\nLOCAL PATCH SCOPE: the attached video contains ONLY the short repair interval. "
                "All video timestamps start at 0 within this patch. Do not demand the complete "
                "scene's setup, subsequent action or dialogue inside it. Boundary stills show "
                "adjacent master footage OUTSIDE the patch, not additional frames in its video. "
                "Do not describe a boundary still as a cut inside this patch. They constrain "
                "incoming/outgoing continuity; character references constrain identity. "
                "Any timecodes in the reported fault are source-scene/master coordinates, "
                "not patch timestamps and not evidence that the fault remains. Judge only "
                "the actual visible patch and its immediate boundary continuity.\n"
                "\nMANDATORY LOCAL REPAIR VERIFICATION: " + repair_requirement
                + "\nAlso return repair_verified:boolean and repair_evidence:string. "
                "repair_verified may be true ONLY when the specified defect is visibly corrected "
                "inside this short patch and the boundary constraints remain coherent. Describe "
                "the actual corrected object/action with local timestamps in repair_evidence. "
                "Matching characters, style or a related reaction is insufficient. A patch which "
                "omits the required prop close-up or merely replays a setup must not pass."
                "\nMODERATE LOCAL FINISHING: distinguish the actual defect from background "
                "choreography. A harmless camera-path, grip, pose, spark or light-sweep variation "
                "must not block a coherent repair or lower its score below the threshold. "
                "A limb behind another limb is normal occlusion, not object penetration. "
                "A hidden finger is not proof of a missing finger. Require visible abnormal "
                "attachment, contours or contact across timed frames before asserting anatomy "
                "or penetration failure; speculative 'risk' is not a confirmed defect. Still "
                "require affirmative evidence of the requested correction and coherent incoming "
                "and outgoing state. Never pass an obscured repair site or an unrelated reaction."
            )
            if concerns_digits(repair_requirement):
                prompt += DIGIT_EVIDENCE

        # 构建消息，包含视频URL
        content = []

        # 添加文本提示
        content.append({
            "type": "text",
            "text": prompt
        })

        # 添加视频
        content.append(review_video_input(video_url))

        if boundary_image_urls:
            content.append({"type": "text", "text":
                "ADJACENT MASTER BOUNDARY STILLS, before then after. Outside the patch video; "
                "not character reference portraits and not extra shots within the patch."})
            content.extend({"type": "image_url", "image_url": {"url": url}}
                           for url in boundary_image_urls)

        # 添加当前分镜的全部角色参考图
        if normalized_reference_urls:
            content.append({"type": "text", "text": "CHARACTER IDENTITY / OUTFIT REFERENCES:"})
        for reference_url in normalized_reference_urls:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": reference_url
                }
            })

        messages = [
            {
                "role": "user",
                "content": content
            }
        ]

        try:
            result = None
            for attempt in range(self.request_retry_count + 1):
                try:
                    # 调用LLM进行视频理解
                    logger.info(f"[REVIEW] Calling LLM for video review (attempt {attempt + 1}/{self.request_retry_count + 1})")
                    result = llm_service.chat_completion(
                        model=self.model,
                        messages=messages,
                        temperature=self.temperature,
                        **(anatomy_review_options() if concerns_digits(repair_requirement)
                           else {"max_tokens": self.max_tokens})
                    )
                    break
                except requests.exceptions.HTTPError as e:
                    status_code = e.response.status_code if e.response is not None else None
                    should_retry = attempt < self.request_retry_count and status_code in {408, 429, 500, 502, 503, 504}
                    logger.warning(
                        f"[REVIEW] Video review HTTP error on attempt {attempt + 1}/{self.request_retry_count + 1}: "
                        f"status={status_code}, retry={should_retry}"
                    )
                    if not should_retry:
                        raise
                    time.sleep(self.request_retry_delay_seconds)

            if result is None:
                raise RuntimeError("video review returned no result")

            if 'choices' not in result or len(result['choices']) == 0:
                raise VideoReviewUnavailableError("review returned no choices")

            output = result['choices'][0]['message']['content']
            logger.info("[REVIEW] Response received: chars=%s", len(output or ""))

            # 解析JSON输出
            try:
                text = str(output or "").strip()
                if text.startswith("```"):
                    text = re.sub(r"^```(?:json)?\s*", "", text, count=1, flags=re.I)
                    text = re.sub(r"\s*```\s*$", "", text, count=1)
                review_result = json.loads(text)
                if not isinstance(review_result, dict) or not isinstance(review_result.get("details"), dict):
                    raise VideoReviewUnavailableError("review returned invalid scoring schema")
                is_approved = review_result.get('approved', False)
                feedback = review_result.get('feedback', '')
                details = review_result.get('details', {})

                # 统一以 details + weights 计算 score，避免模型返回的 score 不一致
                score = self._compute_score_from_details(details if isinstance(details, dict) else {})

                logger.info(f"[REVIEW] Parsed result - score: {score}, approved: {is_approved}")
                logger.info(f"[REVIEW] Details: {details}")

                # Apply the configured weighted gate in both directions; explicit,
                # evidenced hard failures and repair verification remain vetoes.
                is_approved = score >= self.pass_threshold
                hard_failures = review_result.get("hard_failures", [])
                if isinstance(hard_failures, list) and any(
                       isinstance(issue, dict) and issue.get("confirmed") is True and issue.get("evidence")
                       for issue in hard_failures):
                    is_approved = False
                if repair_requirement and not (
                        review_result.get("repair_verified") is True
                        and str(review_result.get("repair_evidence", "")).strip()):
                    is_approved = False
                    feedback = "指定修复点未得到可见证据验证。 " + feedback

                if is_approved:
                    logger.info(f"[REVIEW] Video review PASSED with score {score}/{self.pass_threshold}")
                    return True, feedback, score
                else:
                    logger.warning(
                        "[REVIEW] Video review FAILED with score %s/%s feedback_chars=%s",
                        score, self.pass_threshold, len(feedback or ""),
                    )
                    return False, feedback, score

            except json.JSONDecodeError as e:
                logger.warning("[REVIEW] Invalid review JSON: %s", e)
                raise VideoReviewUnavailableError("review result parse failed") from e

        except VideoReviewUnavailableError:
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"[REVIEW] Video review failed: {str(e)}")
            raise VideoReviewUnavailableError(str(e)) from e
        except Exception as e:
            logger.error(f"[REVIEW] Video review failed: {str(e)}")
            return False, translate(output_language, "error.generation_failed", error=str(e)), 0
