# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import requests
import json
from typing import Dict, Any, List, Optional
from app.config import config
from app.utils.logger import get_logger

logger = get_logger("llm_service")

class LLMService:
    """大模型服务封装"""

    def __init__(self):
        self.api_key = config.modelark_api_key
        self.base_url = config.modelark_base_url
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

    def _get_model_api_key(self, model_endpoint: str) -> str:
        """
        获取模型特定的API密钥
        优先使用模型配置中的api_key，如果没有则使用全局api_key
        """
        # 检查 script 模型 (剧本分镜脚本Agent)
        script_endpoint = config.get('models.script.endpoint')
        script_api_key = config.get('models.script.api_key')
        if model_endpoint == script_endpoint and script_api_key:
            logger.info(f"[API_KEY] Using script model api_key for endpoint: {model_endpoint}")
            return script_api_key

        # 检查 main_agent 模型
        main_agent_endpoint = config.get('models.main_agent.endpoint')
        main_agent_api_key = config.get('models.main_agent.api_key')
        if model_endpoint == main_agent_endpoint and main_agent_api_key:
            logger.info(f"[API_KEY] Using main_agent model api_key for endpoint: {model_endpoint}")
            return main_agent_api_key

        # 检查 video 模型
        video_endpoint = config.get('models.video.endpoint')
        video_api_key = config.get('models.video.api_key')
        if model_endpoint == video_endpoint and video_api_key:
            logger.info(f"[API_KEY] Using video model api_key for endpoint: {model_endpoint}")
            return video_api_key

        # 检查 image 模型
        image_endpoint = config.get('models.image.endpoint')
        image_api_key = config.get('models.image.api_key')
        if model_endpoint == image_endpoint and image_api_key:
            logger.info(f"[API_KEY] Using image model api_key for endpoint: {model_endpoint}")
            return image_api_key

        # 检查 video_review 模型 (视频审核Agent)
        video_review_endpoint = config.get('models.video_review.endpoint')
        video_review_api_key = config.get('models.video_review.api_key')
        if model_endpoint == video_review_endpoint and video_review_api_key:
            logger.info(f"[API_KEY] Using video_review model api_key for endpoint: {model_endpoint}")
            return video_review_api_key

        # 默认使用全局api_key
        logger.info(f"[API_KEY] Using global api_key for endpoint: {model_endpoint}")
        return self.api_key

    def _get_headers(self, model_endpoint: str) -> Dict[str, str]:
        """获取请求头，使用模型特定的API密钥"""
        api_key = self._get_model_api_key(model_endpoint)
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

    def _normalize_image_size(self, size: str, ratio: Optional[str]) -> str:
        """将抽象分辨率 + 比例转换为图片接口稳定支持的像素尺寸。"""
        if not size:
            size = "2K"

        normalized_size = str(size).upper()
        if "X" in normalized_size:
            return size

        if not ratio:
            return size

        ratio_map = {
            "2K": {
                "1:1": "2048x2048",
                "4:3": "2304x1728",
                "3:4": "1728x2304",
                "16:9": "2848x1600",
                "9:16": "1600x2848",
                "3:2": "2496x1664",
                "2:3": "1664x2496",
                "21:9": "3136x1344",
            },
            "4K": {
                "1:1": "4096x4096",
                "4:3": "4704x3520",
                "3:4": "3520x4704",
                "16:9": "5504x3040",
                "9:16": "3040x5504",
                "3:2": "4992x3328",
                "2:3": "3328x4992",
                "21:9": "6240x2656",
            }
        }

        mapped_size = ratio_map.get(normalized_size, {}).get(ratio)
        return mapped_size or size

    def _normalize_video_duration(self, duration: int) -> int:
        """按项目配置约束视频时长，同时保持在模型支持范围内。"""
        configured_min = int(config.get('video_generation.scene_duration.min', 10))
        configured_max = int(config.get('video_generation.scene_duration.max', 30))
        effective_min = max(6, configured_min)
        effective_max = min(30, configured_max)
        if effective_min > effective_max:
            effective_min, effective_max = 6, 30
        return max(effective_min, min(effective_max, int(duration)))

    def chat_completion(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> Dict[str, Any]:
        """
        调用聊天补全API（剧本生成）

        Args:
            model: 模型端点
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数

        Returns:
            API响应结果
        """
        url = f"{self.base_url}/chat/completions"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs
        }

        logger.log_llm_input(model, json.dumps(messages, ensure_ascii=False))
        logger.info(f"[LLM_REQUEST][CHAT] url={url}")
        logger.info(f"[LLM_REQUEST][CHAT] payload={json.dumps(payload, ensure_ascii=False)}")

        try:
            # 剧本生成 timeout: 600秒
            response = requests.post(
                url,
                headers=self._get_headers(model),
                json=payload,
                timeout=600
            )
            response.raise_for_status()
            result = response.json()

            if 'choices' in result and len(result['choices']) > 0:
                output = result['choices'][0]['message']['content']
                logger.log_llm_output(model, output)

            return result

        except requests.exceptions.HTTPError as e:
            logger.error(f"LLM call failed: {str(e)}")
            logger.error(f"LLM request payload: {json.dumps(payload, ensure_ascii=False)}")
            if e.response is not None and e.response.text:
                logger.error(f"LLM response text: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"LLM call failed: {str(e)}")
            raise
    
    def generate_image(
        self,
        prompt: str,
        model: str = None,
        size: str = "2K",
        image_urls: List[str] = None,
        ratio: str = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        调用生图API
        
        Args:
            prompt: 图片生成提示词
            model: 模型端点
            size: 图片尺寸
            image_urls: 参考图片URL列表（图生图）
            ratio: 图片比例（如 16:9, 9:16, 4:3, 1:1, 3:4, 2:3 等）
        
        Returns:
            API响应结果
        """
        if model is None:
            model = config.get('models.image.endpoint')

        url = f"{self.base_url}/images/generations"
        normalized_size = self._normalize_image_size(size, ratio)

        payload = {
            "model": model,
            "prompt": prompt,
            "size": normalized_size,
            "response_format": "url",
            "stream": False,
            "watermark": False,
            **kwargs
        }

        if ratio:
            logger.info(f"Image generation requested with ratio: {ratio}, normalized size: {normalized_size}")

        if image_urls:
            payload["image"] = image_urls

        logger.log_llm_input(model, prompt)
        logger.info(f"[LLM_REQUEST][IMAGE] url={url}")
        logger.info(f"[LLM_REQUEST][IMAGE] payload={json.dumps(payload, ensure_ascii=False)}")
        
        try:
            # 图片生成 timeout: 900秒
            response = requests.post(
                url,
                headers=self._get_headers(model),
                json=payload,
                timeout=900
            )
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"Image generation successful: {model}")
            return result
            
        except requests.exceptions.HTTPError as e:
            logger.error(f"Image generation failed: {str(e)}")
            logger.error(f"Image request payload: {json.dumps(payload, ensure_ascii=False)}")
            if e.response is not None and e.response.text:
                logger.error(f"Image response text: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Image generation failed: {str(e)}")
            raise
    
    def create_video_task(
        self,
        prompt: str,
        reference_image_url: str = None,
        first_frame_url: str = None,
        last_frame_url: str = None,
        model: str = None,
        duration: int = 10,
        resolution: str = "720p",
        aspect_ratio: str = None,
        camera_fixed: bool = False,
        **kwargs
    ) -> str:
        """
        创建视频生成任务 - seedance-2.0 版本

        Args:
            prompt: 视频生成提示词
            reference_image_url: 角色参考图URL（seedance-2.0 使用）
            first_frame_url: 首帧图片URL（旧版本使用，可选）
            last_frame_url: 尾帧图片URL（可选）
            model: 模型端点
            duration: 视频时长（秒）
            resolution: 视频分辨率 (480p, 720p, 1080p)
            camera_fixed: 是否固定相机位置

        Returns:
            任务ID
        """
        if model is None:
            model = config.get('models.video.endpoint')

        url = f"{self.base_url}/contents/generations/tasks"

        # 模型支持 6-30 秒，这里进一步收口到项目配置的分镜时长范围。
        duration = self._normalize_video_duration(duration)

        ratio = aspect_ratio or "9:16"
        logger.info(f"Video generation: duration={duration}s, resolution={resolution}, ratio={ratio}")

        # 使用视频模型特定的 API key（如果配置了）
        video_api_key = config.get('models.video.api_key')
        if video_api_key:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {video_api_key}"
            }
        else:
            headers = self.headers

        # 构建 content 数组
        content = [
            {
                "type": "text",
                "text": prompt
            }
        ]

        # 优先使用 reference_image_url (seedance-2.0 格式)
        if reference_image_url:
            content.append({
                "type": "image_url",
                "image_url": {"url": reference_image_url},
                "role": "reference_image"
            })
            logger.info(f"Using reference_image for character consistency")
        elif first_frame_url:
            # 兼容旧版本：使用 first_frame
            content.append({
                "type": "image_url",
                "image_url": {"url": first_frame_url}
            })
            logger.info(f"Using first_frame (legacy mode)")

        # 构建 payload - seedance-2.5 格式
        payload = {
            "model": model,
            "content": content,
            "generate_audio": True,
            "ratio": ratio,
            "duration": duration,
            "watermark": False,
            # 生成 mov 格式视频（seedance-2.5 支持 output_format）
            "output_format": config.get('video_generation.output_format', 'mov'),
            **kwargs
        }

        # 添加可选参数
        if resolution:
            payload["resolution"] = resolution
        if camera_fixed:
            payload["camera_fixed"] = camera_fixed

        logger.log_llm_input(model, prompt)
        logger.info(f"Video task payload: model={model}, duration={duration}, ratio={ratio}")
        logger.info(f"[LLM_REQUEST][VIDEO_CREATE] url={url}")
        logger.info(f"[LLM_REQUEST][VIDEO_CREATE] payload={json.dumps(payload, ensure_ascii=False)}")

        try:
            # 视频生成 timeout: 1000秒
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=1000
            )
            response.raise_for_status()
            result = response.json()

            task_id = result.get('id')
            logger.info(f"Video task created: {task_id}")
            return task_id

        except requests.exceptions.HTTPError as e:
            logger.error(f"Video task creation failed: {str(e)}")
            logger.error(f"Request payload: {json.dumps(payload, ensure_ascii=False)}")
            if response.text:
                logger.error(f"Response text: {response.text}")
            raise
        except Exception as e:
            logger.error(f"Video task creation failed: {str(e)}")
            raise

    def create_video_task_with_content(
        self,
        model: str,
        content: List[Dict[str, Any]],
        duration: int = 10,
        resolution: str = "720p",
        aspect_ratio: str = None,
        camera_fixed: bool = False,
        **kwargs
    ) -> str:
        """
        创建视频生成任务 - 使用自定义content数组格式（支持reference_image + reference_video）

        用于：基于前一个视频生成延伸视频
        格式：
        [
            {"type": "text", "text": "prompt"},
            {"type": "image_url", "role": "reference_image", "image_url": {"url": "..."}},
            {"type": "video_url", "video_url": {"url": "..."}, "role": "reference_video"}
        ]

        Args:
            model: 模型端点
            content: content数组（已经构建好）
            duration: 视频时长（秒）
            resolution: 视频分辨率 (480p, 720p, 1080p)
            camera_fixed: 是否固定相机位置

        Returns:
            任务ID
        """
        url = f"{self.base_url}/contents/generations/tasks"

        # 模型支持 6-30 秒，这里进一步收口到项目配置的分镜时长范围。
        duration = self._normalize_video_duration(duration)

        normalized_resolution = str(resolution).strip().lower() if resolution else ""
        logger.info(
            f"Video generation: duration={duration}s, resolution={normalized_resolution or 'default'}, "
            f"content has {len(content)} items"
        )

        # 检测content中是否包含视频引用
        has_video_ref = any(item.get("type") == "video_url" for item in content)
        if has_video_ref:
            logger.info("[CONTENT] Contains reference_video for extension generation")

        # 使用视频模型特定的 API key（如果配置了）
        video_api_key = config.get('models.video.api_key')
        if video_api_key:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {video_api_key}"
            }
        else:
            headers = self._get_headers(model)

        ratio = aspect_ratio or ("16:9" if normalized_resolution in {"720p", "1080p"} else "9:16")

        # 构建 payload
        payload = {
            "model": model,
            "content": content,
            "generate_audio": True,
            "ratio": ratio,
            "duration": duration,
            "watermark": False,
            # 生成 mov 格式视频（seedance-2.5 支持 output_format）
            "output_format": config.get('video_generation.output_format', 'mov'),
            **kwargs
        }

        # 添加可选参数
        if normalized_resolution:
            payload["resolution"] = normalized_resolution
        if camera_fixed:
            payload["camera_fixed"] = camera_fixed

        logger.info(
            f"Video task created: model={model}, duration={duration}, ratio={ratio}, "
            f"resolution={normalized_resolution or 'default'}, content={len(content)} items"
        )
        logger.info(f"[LLM_REQUEST][VIDEO_CREATE_WITH_CONTENT] url={url}")
        logger.info(f"[LLM_REQUEST][VIDEO_CREATE_WITH_CONTENT] payload={json.dumps(payload, ensure_ascii=False)}")

        try:
            # 视频生成 timeout: 1000秒
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=1000
            )
            response.raise_for_status()
            result = response.json()

            task_id = result.get('id')
            logger.info(f"Video task created: {task_id}")
            return task_id

        except requests.exceptions.HTTPError as e:
            logger.error(f"Video task creation failed: {str(e)}")
            logger.error(f"Request payload: {json.dumps(payload, ensure_ascii=False)}")
            if response.text:
                logger.error(f"Response text: {response.text}")
            raise
        except Exception as e:
            logger.error(f"Video task creation failed: {str(e)}")
            raise

    def query_video_task(self, task_id: str) -> Dict[str, Any]:
        """
        查询视频生成任务状态
        
        Args:
            task_id: 任务ID
        
        Returns:
            任务状态和结果
        """
        url = f"{self.base_url}/contents/generations/tasks/{task_id}"
        
        # 使用视频模型特定的 API key（如果配置了）
        video_api_key = config.get('models.video.api_key')
        if video_api_key:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {video_api_key}"
            }
        else:
            headers = self.headers
        """
        查询视频生成任务状态
        
        Args:
            task_id: 任务ID
        
        Returns:
            任务状态和结果
        """
        url = f"{self.base_url}/contents/generations/tasks/{task_id}"
        
        # 使用视频模型特定的 API key（如果配置了）
        video_api_key = config.get('models.video.api_key')
        if video_api_key:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {video_api_key}"
            }
        else:
            headers = self.headers
        
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=config.get('limits.request_timeout', 120)
            )
            response.raise_for_status()
            result = response.json()
            
            logger.log_heartbeat("video_task", result.get('status', 'unknown'))
            return result
            
        except Exception as e:
            logger.error(f"Video task query failed: {str(e)}")
            raise

    def extract_video_result(self, result: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """从视频任务查询结果中尽可能提取 video_url 和 seed。"""
        content = result.get('content') if isinstance(result, dict) else None
        if not isinstance(content, dict):
            content = {}

        video_url = (
            content.get('video_url')
            or content.get('url')
            or result.get('video_url')
            or result.get('url')
        )

        seed_candidates = [
            content.get('seed'),
            content.get('video_seed'),
            content.get('random_seed'),
            result.get('seed'),
            result.get('video_seed'),
            result.get('random_seed'),
        ]
        seed = next((str(candidate) for candidate in seed_candidates if candidate not in (None, "")), None)

        return {
            "video_url": video_url,
            "seed": seed,
        }

# 全局服务实例
llm_service = LLMService()
