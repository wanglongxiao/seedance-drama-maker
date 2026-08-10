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

        # 构建审核提示词 - 使用大模型视觉理解能力进行三维审核
        prompt_parts = []
        prompt_parts.append("【分镜视频视觉理解审核任务】")
        prompt_parts.append("请使用大模型视觉理解能力观看视频，并对当前分镜视频进行审核评分。")
        prompt_parts.append("")

        prompt_parts.append("【剧本要求】")
        prompt_parts.append(script_scene_description)
        prompt_parts.append("")

        if reference_image_url:
            prompt_parts.append("【角色参考】")
            prompt_parts.append("参考图片中的角色形象应该在视频中保持一致")
            prompt_parts.append("")

        if previous_video_url:
            prompt_parts.append("【上下文参考】")
            prompt_parts.append("可参考前一个分镜视频理解角色状态和剧情背景，但重点仍是判断当前视频本身是否合理自然")
            prompt_parts.append("")

        prompt_parts.append("【审核标准 - 请逐项评分（0-100分）】")
        prompt_parts.append("1. 人物/角色一致性（权重见下）：角色形象、身份、特征是否与参考图和上下文保持一致")
        prompt_parts.append("2. 物理世界规律（权重见下）：画面和动作是否符合基本物理原理与视觉逻辑")
        prompt_parts.append("   - 重点检查：人物是否出现多余肢体、转身前后脸部特征突变、动作诡异、物体大小比例不合逻辑、结构突然错位等")
        prompt_parts.append("3. 与分镜脚本意义上一致（权重见下）：视频内容在语义和剧情推进上是否与该分镜脚本一致")
        prompt_parts.append("")

        prompt_parts.append("【评分规则 - 重要】")
        prompt_parts.append("- 评分基于视觉理解与语义一致性，不是文字逐字匹配")
        prompt_parts.append("- 只要视频内容在意义上与分镜脚本相符，即使表述方式不同，也应给高分")
        prompt_parts.append("- 重点关注人物/角色一致性、物理世界规律、与分镜脚本意义上一致这 3 个方面")
        prompt_parts.append("- 色情、性爱、限制级、恐怖、凶杀、血腥等成人或强烈题材本身不是扣分项，不能因为题材属性降低任何维度分数")
        prompt_parts.append("- 即使视频包含18+、恐怖或暴力内容，也只能按角色一致性、物理规律、分镜语义一致性来评分，不得因内容题材本身判为不通过")
        prompt_parts.append("- 只有当画面执行质量差、角色不一致、物理规律异常、或与当前分镜脚本语义不一致时，才允许扣分")
        prompt_parts.append(f"- {self.pass_threshold}分及以上视为通过")
        prompt_parts.append("")

        prompt_parts.append("【输出要求 - 必须严格按以下JSON格式输出】")
        prompt_parts.append(translate(output_language, "review.output_language_rule", language=language_name(output_language)))
        prompt_parts.append("{")
        prompt_parts.append('  "score": <综合评分0-100的整数>,')
        prompt_parts.append(f'  "approved": <true/false - 综合评分>={self.pass_threshold}为true否则false>,')
        prompt_parts.append('  "feedback": "<详细反馈，说明视频内容与剧本的语义匹配情况。如果不通过请说明具体问题和修改建议>",')
        prompt_parts.append('  "details": {')
        prompt_parts.append('    "character_consistency": <人物/角色一致性评分0-100>,')
        prompt_parts.append('    "physical-laws": <物理世界规律评分0-100>,')
        prompt_parts.append('    "script_semantic_consistency": <与分镜脚本意义上一致评分0-100>')
        prompt_parts.append('  }')
        prompt_parts.append("}")
        prompt_parts.append("")
        prompt_parts.append("注意：")
        prompt_parts.append(
            f"- 综合评分 = 人物/角色一致性*{self.weights['character_consistency']:.2f} + "
            f"物理世界规律*{self.weights['physical-laws']:.2f} + "
            f"与分镜脚本意义上一致*{self.weights['script_semantic_consistency']:.2f}"
        )
        prompt_parts.append("- feedback 必须明确指出未通过时是哪个维度拉低了分数")

        prompt = "\n".join(prompt_parts)
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
