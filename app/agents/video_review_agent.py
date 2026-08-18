# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

"""
视频审核Agent
使用LLM的视频理解能力审核生成的视频是否符合剧本要求
通过语义分析对比计算分数，只要文字表达的意思相似即可
"""
import time
from typing import Dict, Any, Tuple
import requests
from app.config import config
from app.prompt_skill import render_prompt
from app.services.llm_service import llm_service
from app.utils.i18n import language_name, translate
from app.utils.logger import get_logger

logger = get_logger("video_review_agent")


class VideoReviewAgent:
    """视频审核Agent - 使用视频理解审核生成结果"""

    def __init__(self):
        # 使用 YAML 中当前启用的视频审核模型
        self.model = config.get('models.video_review.endpoint')
        # 审核通过阈值（0-100分）
        self.pass_threshold = config.get('video_review.pass_threshold', 70)
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
        previous_video_url: str = None,
        reference_image_url: str = None,
        output_language: str = "zh-CN",
    ) -> Tuple[bool, str, int]:
        """
        审核视频是否符合剧本要求

        Args:
            script_scene_description: 剧本中该分镜的描述
            video_url: 生成的视频URL
            previous_video_url: 前一个分镜视频URL（可作为上下文参考）
            reference_image_url: 参考图URL（用于角色一致性检查）

        Returns:
            (is_approved: bool, feedback: str, score: int)
            - is_approved: True=通过，False=需要重新生成
            - feedback: 反馈信息，如果不通过，说明原因
            - score: 综合评分（0-100分）
        """
        logger.info(f"[REVIEW] Starting video review for: {video_url[:100]}...")
        logger.info(f"[REVIEW] Script description: {script_scene_description[:200]}...")
        logger.info(f"[REVIEW] Has previous video: {previous_video_url is not None}")
        logger.info(f"[REVIEW] Has reference image: {reference_image_url is not None}")

        reference_image_section = ""
        if reference_image_url:
            reference_image_section = "【角色参考】\n参考图片中的角色形象应该在视频中保持一致\n"
        previous_video_section = ""
        if previous_video_url:
            previous_video_section = "【上下文参考】\n可参考前一个分镜视频理解角色状态和剧情背景，但重点仍是判断当前视频本身是否合理自然\n"

        prompt = render_prompt(
            "video_review.md",
            script_scene_description=script_scene_description,
            reference_image_section=reference_image_section,
            previous_video_section=previous_video_section,
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

        # 构建消息，包含视频URL
        content = []

        # 添加文本提示
        content.append({
            "type": "text",
            "text": prompt
        })

        # 添加视频
        content.append({
            "type": "video_url",
            "video_url": {
                "url": video_url
            }
        })

        # 如果有参考图，添加参考图
        if reference_image_url:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": reference_image_url
                }
            })

        # 如果有前一个视频，添加前一个视频
        if previous_video_url:
            content.append({
                "type": "video_url",
                "video_url": {
                    "url": previous_video_url
                },
                "role": "previous_video"
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
                        max_tokens=self.max_tokens
                    )
                    break
                except requests.exceptions.HTTPError as e:
                    status_code = e.response.status_code if e.response is not None else None
                    should_retry = attempt < self.request_retry_count and status_code in {400, 408, 429, 500, 502, 503, 504}
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
                logger.error(f"[REVIEW] Video review failed: no choices in response")
                return False, translate(output_language, "error.generation_failed", error="review returned no choices"), 0

            output = result['choices'][0]['message']['content']
            logger.info(f"[REVIEW] Raw LLM output: {output[:500]}...")

            # 解析JSON输出
            import json
            try:
                review_result = json.loads(output)
                is_approved = review_result.get('approved', False)
                feedback = review_result.get('feedback', '')
                details = review_result.get('details', {})

                # 统一以 details + weights 计算 score，避免模型返回的 score 不一致
                score = self._compute_score_from_details(details if isinstance(details, dict) else {})

                logger.info(f"[REVIEW] Parsed result - score: {score}, approved: {is_approved}")
                logger.info(f"[REVIEW] Details: {details}")

                # 如果LLM没有正确设置approved，根据分数判断
                if not isinstance(is_approved, bool):
                    is_approved = score >= self.pass_threshold
                    logger.info(f"[REVIEW] Recalculated approved based on score: {is_approved}")

                if is_approved:
                    logger.info(f"[REVIEW] Video review PASSED with score {score}/{self.pass_threshold}")
                    return True, feedback, score
                else:
                    logger.warning(f"[REVIEW] Video review FAILED with score {score}/{self.pass_threshold}: {feedback}")
                    return False, feedback, score

            except json.JSONDecodeError as e:
                logger.error(f"[REVIEW] Failed to parse review result: {e}, output: {output}")
                # 如果解析失败，保守起见认为不通过
                return False, translate(output_language, "error.generation_failed", error="review result parse failed"), 0

        except Exception as e:
            logger.error(f"[REVIEW] Video review failed: {str(e)}")
            return False, translate(output_language, "error.generation_failed", error=str(e)), 0
