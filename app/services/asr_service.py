# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import json
import os
import time
import uuid
import requests
from typing import Optional, Dict, Any
from urllib.parse import urlparse
from app.config import config
from app.utils.logger import get_logger

logger = get_logger("asr_service")

class ASRService:
    """Seed Speech ASR 语音识别服务"""

    def __init__(self):
        self.appid = config.get('speech.appid')
        self.api_key = config.get('speech.api_key')
        self.base_url = config.get('speech.base_url', 'https://voice.ap-southeast-1.bytepluses.com/api/v3')
        self.submit_path = config.get('speech.submit_path', '/auc/bigmodel/submit')
        self.query_path = config.get('speech.query_path', '/auc/bigmodel/query')
        self.resource_id = config.get('models.asr.resource_id', 'volc.bigasr.auc')

        if not self.appid:
            raise ValueError("Missing required config: speech.appid")
        if not self.api_key:
            raise ValueError("Missing required config: speech.api_key")

    @staticmethod
    def _get_audio_format_from_url(audio_url: str) -> str:
        parsed_url = urlparse(audio_url)
        extension = os.path.splitext(parsed_url.path)[1].lower().lstrip(".")
        extension_aliases = {
            "weba": "webm",
            "m4a": "mp4",
            "oga": "ogg",
        }
        return extension_aliases.get(extension, extension or "wav")

    @staticmethod
    def _get_default_codec(audio_format: str) -> str:
        if audio_format in {"webm", "ogg", "opus"}:
            return "opus"
        return "raw"

    def submit_task(self, audio_url: str, language: Optional[str] = None, **kwargs) -> tuple:
        """
        提交ASR任务

        Args:
            audio_url: 音频文件URL
            language: 语言代码（可选）
            **kwargs: 其他可选参数

        Returns:
            (task_id, log_id) 元组
        """
        submit_url = f"{self.base_url}{self.submit_path}"
        task_id = str(uuid.uuid4())
        audio_format = kwargs.get("format") or self._get_audio_format_from_url(audio_url)
        codec = kwargs.get("codec") or self._get_default_codec(audio_format)

        # 构建音频参数
        audio_params = {
            "url": audio_url,
            "format": audio_format,
            "codec": codec,
            "rate": kwargs.get("rate", 16000),
            "bits": kwargs.get("bits", 16),
            "channel": kwargs.get("channel", 1)
        }

        if language:
            audio_params["language"] = language

        # 构建请求体
        request = {
            "user": {
                "uid": "豆包语音"
            },
            "audio": audio_params,
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": False,
                "enable_ddc": False,
                "enable_speaker_info": False,
                "enable_channel_split": False,
                **kwargs.get("request_params", {})
            }
        }

        logger.info(f"Submitting ASR task: {task_id}, format={audio_format}, codec={codec}")

        # 构建请求头（使用 x-api-key 认证）
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": task_id,
            "X-Api-Sequence": "-1"
        }

        try:
            response = requests.post(
                submit_url,
                data=json.dumps(request),
                headers=headers,
                timeout=config.get('limits.request_timeout', 120)
            )

            status_code = response.headers.get("X-Api-Status-Code", "")

            if status_code == "20000000":
                x_tt_logid = response.headers.get("X-Tt-Logid", "")
                logger.info(f"ASR task submitted successfully: {task_id}")
                return task_id, x_tt_logid
            else:
                error_msg = f"ASR task submission failed: {response.headers}"
                logger.error(error_msg)
                raise Exception(error_msg)

        except Exception as e:
            logger.error(f"ASR task submission error: {str(e)}")
            raise

    def query_task(self, task_id: str, log_id: str) -> Dict[str, Any]:
        """
        查询ASR任务结果

        Args:
            task_id: 任务ID
            log_id: 日志ID

        Returns:
            API响应结果
        """
        query_url = f"{self.base_url}{self.query_path}"

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": task_id,
            "X-Tt-Logid": log_id
        }

        try:
            response = requests.post(
                query_url,
                json.dumps({}),
                headers=headers,
                timeout=config.get('limits.request_timeout', 120)
            )

            logger.log_heartbeat("asr_task", response.headers.get("X-Api-Status-Code", "unknown"))
            return response

        except Exception as e:
            logger.error(f"ASR task query error: {str(e)}")
            raise

    def recognize(
        self,
        audio_url: str,
        language: Optional[str] = None,
        poll_interval: Optional[int] = None,
        max_wait_time: Optional[int] = None,
        **kwargs
    ) -> str:
        """
        识别音频文件并返回文本结果

        Args:
            audio_url: 音频文件URL
            language: 语言代码（可选）
            poll_interval: 轮询间隔（秒）
            max_wait_time: 最大等待时间（秒）
            **kwargs: 其他参数（format, codec, rate, bits, channel等）

        Returns:
            识别出的文本
        """
        if poll_interval is None:
            poll_interval = config.get('limits.asr_poll_interval', 1)
        if max_wait_time is None:
            max_wait_time = config.get('limits.asr_max_wait_time', 300)

        # 提交任务
        task_id, log_id = self.submit_task(audio_url, language=language, **kwargs)

        # 轮询等待结果
        elapsed = 0
        while elapsed < max_wait_time:
            query_response = self.query_task(task_id, log_id)
            code = query_response.headers.get("X-Api-Status-Code", "")

            if code == "20000000":  # 任务完成
                result = query_response.json()

                # 提取文本
                text = ""
                if "result" in result:
                    if "text" in result["result"]:
                        text = result["result"]["text"]
                    elif "utterances" in result["result"]:
                        texts = [u.get("text", "") for u in result["result"]["utterances"]]
                        text = " ".join(texts)

                logger.info(f"ASR recognition successful: {text[:100]}...")
                return text

            elif code not in ["20000001", "20000002"]:  # 任务失败
                error_msg = f"ASR task failed with code: {code}"
                logger.error(error_msg)
                raise Exception(error_msg)

            # 任务进行中，继续等待
            logger.debug(f"ASR task in progress... elapsed: {elapsed}s")
            time.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError("ASR recognition timeout")

# 全局服务实例
asr_service = ASRService()
