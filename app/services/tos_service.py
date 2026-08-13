# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import os
import mimetypes
import io
import requests
from datetime import datetime
from typing import Optional, List
from urllib.parse import urlparse
from pathlib import Path
from app.config import config
from app.utils.logger import get_logger

logger = get_logger("tos_service")


class TOSService:
    """TOS文件服务 - 使用 tos-python-sdk 上传"""

    # .mov / .qt 默认会被识别为 video/quicktime，部分浏览器（Chrome/Firefox）对该 MIME
    # 兼容性较差；seedance-2.5 输出的 mov 实为 H.264/AAC 编码，统一以 video/mp4 提供可获得
    # 最佳的 Web 前端边下边播兼容性。
    _VIDEO_CONTENT_TYPE_OVERRIDES = {
        ".mov": "video/mp4",
        ".qt": "video/mp4",
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
    }

    @classmethod
    def _resolve_content_type(cls, filename: str, fallback: Optional[str] = None) -> str:
        """根据文件扩展名解析 Content-Type，并对视频类型做浏览器兼容性归一化。"""
        suffix = Path(str(filename)).suffix.lower()
        if suffix in cls._VIDEO_CONTENT_TYPE_OVERRIDES:
            return cls._VIDEO_CONTENT_TYPE_OVERRIDES[suffix]
        guessed = mimetypes.guess_type(str(filename))[0]
        return guessed or fallback or "application/octet-stream"

    def __init__(self):
        self.bucket = config.tos_bucket
        self.region = config.tos_region
        self.endpoint = config.tos_endpoint.replace("https://", "").replace("http://", "")
        self.uploads_dir = config.tos_uploads_dir
        self.request_timeout = config.get('tos.request_timeout', 180)
        self.socket_timeout = config.get('tos.socket_timeout', 180)
        self.connection_timeout = config.get('tos.connection_timeout', 30)
        self.max_retry_count = config.get('tos.max_retry_count', 5)
        # BytePlus 认证信息
        self.ak = config.get('byteplus.ak')
        self.sk = config.get('byteplus.sk')
        # 创建 TOS 客户端
        self.client = self._create_client()

    def _create_client(self):
        """创建 TOS 客户端"""
        try:
            import tos
            # 使用 TosClientV2 创建客户端
            client = tos.TosClientV2(
                self.ak,
                self.sk,
                self.endpoint,
                self.region,
                max_retry_count=self.max_retry_count,
                request_timeout=self.request_timeout,
                connection_time=self.connection_timeout,
                socket_timeout=self.socket_timeout,
            )
            logger.info("TOS client created successfully")
            return client
        except ImportError:
            logger.error("tos SDK not installed. Please run: pip install tos")
            return None
        except Exception as e:
            logger.error(f"Failed to create TOS client: {str(e)}")
            return None

    def generate_filename(self, original_name: str) -> str:
        """
        生成带时间戳的文件名

        Args:
            original_name: 原始文件名

        Returns:
            带时间戳的新文件名
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = Path(original_name).suffix
        base_name = Path(original_name).stem
        # 清理文件名中的特殊字符
        safe_base = base_name.replace(" ", "_").replace("/", "_").replace("\\", "_")
        safe_base = "".join(c for c in safe_base if c.isalnum() or c in "_-.")
        return f"{timestamp}_{safe_base}{ext}"

    def build_project_prefix(self, project_id: str) -> str:
        return f"{self.uploads_dir}/{project_id}"

    def _normalize_relative_key(self, filename: str) -> str:
        normalized = str(filename or "").strip().lstrip("/")
        if normalized.startswith(f"{self.uploads_dir}/"):
            normalized = normalized[len(self.uploads_dir) + 1:]
        return normalized

    def build_url(self, filename: str, project_id: Optional[str] = None, category: Optional[str] = None) -> str:
        """
        构建文件公共访问URL

        Args:
            filename: 文件名

        Returns:
            完整的公共访问 URL
        """
        object_key = self.build_object_key(filename, project_id=project_id, category=category)
        return f"https://{self.bucket}.{self.endpoint}/{object_key}"

    def build_object_key(self, filename: str, project_id: Optional[str] = None, category: Optional[str] = None) -> str:
        """
        构建对象键（路径）

        Args:
            filename: 文件名

        Returns:
            对象键
        """
        relative_key = self._normalize_relative_key(filename)
        if project_id:
            category_part = str(category or "").strip().strip("/")
            base_prefix = self.build_project_prefix(project_id)
            if category_part:
                return f"{base_prefix}/{category_part}/{relative_key}"
            return f"{base_prefix}/{relative_key}"
        return f"{self.uploads_dir}/{relative_key}"

    def upload_file(
        self,
        local_path: str,
        custom_filename: Optional[str] = None,
        project_id: Optional[str] = None,
        category: Optional[str] = None,
    ) -> str:
        """
        上传文件到TOS，并设置为公共读权限

        Args:
            local_path: 本地文件路径
            custom_filename: 自定义文件名（可选）

        Returns:
            文件的公共访问URL
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"文件不存在: {local_path}")

        # 确定文件名
        if custom_filename:
            filename = custom_filename
        else:
            original_name = os.path.basename(local_path)
            filename = self.generate_filename(original_name)

        # 构建对象键
        object_key = self.build_object_key(filename, project_id=project_id, category=category)

        logger.info(f"Uploading file to TOS: {filename}")

        try:
            if self.client:
                import tos
                content_type = self._resolve_content_type(
                    object_key,
                    fallback=mimetypes.guess_type(local_path)[0],
                )

                # 使用 SDK 直接按文件路径上传，避免大文件流式写入时更容易触发写超时
                self.client.put_object_from_file(
                    self.bucket,
                    object_key,
                    local_path,
                    content_type=content_type,
                )
                
                # 设置对象 ACL 为公共读
                self.client.put_object_acl(self.bucket, object_key, acl=tos.ACLType.ACL_Public_Read)
                logger.info(f"Object ACL set to Public Read: {object_key}")
                
                public_url = self.build_url(filename, project_id=project_id, category=category)
                logger.info(f"File uploaded successfully via SDK: {public_url}")
                return public_url
            else:
                # SDK 不可用，返回本地文件路径
                logger.warning("TOS SDK not available, returning local file path")
                return f"file://{local_path}"

        except Exception as e:
            logger.error(f"Upload failed: {str(e)}")
            # 上传失败，返回本地文件路径作为备选
            return f"file://{local_path}"

    def upload_bytes(
        self,
        data: bytes,
        filename: str,
        content_type: Optional[str] = None,
        project_id: Optional[str] = None,
        category: Optional[str] = None,
    ) -> str:
        """
        上传字节数据到TOS，并设置为公共读权限

        Args:
            data: 文件字节数据
            filename: 文件名
            content_type: 内容类型（可选）

        Returns:
            文件的公共访问URL
        """
        # 构建对象键
        object_key = self.build_object_key(filename, project_id=project_id, category=category)

        logger.info(f"Uploading bytes to TOS: {filename}")

        try:
            if self.client:
                import tos
                # 使用 SDK 上传
                from io import BytesIO
                self.client.put_object(self.bucket, object_key, content=BytesIO(data))
                
                # 设置对象 ACL 为公共读
                self.client.put_object_acl(self.bucket, object_key, acl=tos.ACLType.ACL_Public_Read)
                logger.info(f"Object ACL set to Public Read: {object_key}")
                
                public_url = self.build_url(filename, project_id=project_id, category=category)
                logger.info(f"Bytes uploaded successfully via SDK: {public_url}")
                return public_url
            else:
                # SDK 不可用
                logger.error("TOS SDK not available, cannot upload bytes")
                raise Exception("TOS SDK not available")

        except Exception as e:
            logger.error(f"Upload failed: {str(e)}")
            raise

    def stitch_images_and_upload(
        self,
        image_urls: List[str],
        filename_prefix: str = "stitched_reference",
        project_id: Optional[str] = None,
        category: Optional[str] = None,
    ) -> str:
        """按给定顺序横向拼接多张图片并上传到 TOS。"""
        if not image_urls:
            raise ValueError("No image urls provided for stitching")
        if len(image_urls) == 1:
            return image_urls[0]

        try:
            from PIL import Image
        except ImportError as e:
            raise RuntimeError("Pillow is required for stitching uploaded reference images") from e

        images = []
        for index, url in enumerate(image_urls, start=1):
            logger.info(f"Downloading reference image {index} for stitching: {url}")
            response = requests.get(url, timeout=self.request_timeout)
            response.raise_for_status()
            image = Image.open(io.BytesIO(response.content)).convert("RGB")
            images.append(image)

        target_height = max(image.height for image in images)
        resized_images = []
        total_width = 0
        for image in images:
            if image.height != target_height:
                target_width = max(1, int(image.width * (target_height / image.height)))
                image = image.resize((target_width, target_height))
            resized_images.append(image)
            total_width += image.width

        stitched = Image.new("RGB", (total_width, target_height), color=(255, 255, 255))
        current_x = 0
        for image in resized_images:
            stitched.paste(image, (current_x, 0))
            current_x += image.width

        buffer = io.BytesIO()
        stitched.save(buffer, format="PNG")
        buffer.seek(0)
        filename = self.generate_filename(f"{filename_prefix}.png")
        logger.info(f"Uploading stitched reference image: {filename}")
        return self.upload_bytes(
            buffer.getvalue(),
            filename,
            content_type="image/png",
            project_id=project_id,
            category=category,
        )

    def copy_url_to_tos(
        self,
        source_url: str,
        target_filename: str,
        project_id: Optional[str] = None,
        category: Optional[str] = None,
    ) -> str:
        """将远程文件复制到任务目录下，统一纳入任务文件管理。"""
        response = requests.get(source_url, timeout=self.request_timeout)
        response.raise_for_status()
        return self.upload_bytes(
            response.content,
            target_filename,
            content_type=self._resolve_content_type(
                target_filename,
                fallback=response.headers.get("Content-Type"),
            ),
            project_id=project_id,
            category=category,
        )

    def get_file_url(self, filename: str) -> str:
        """
        获取已上传文件的URL

        Args:
            filename: 文件名

        Returns:
            文件的公共访问URL
        """
        return self.build_url(filename)

    def extract_object_key_from_url(self, file_url: str) -> Optional[str]:
        parsed = urlparse(str(file_url or ""))
        if parsed.scheme not in {"http", "https"}:
            return None
        object_key = parsed.path.lstrip("/")
        return object_key or None

    def delete_file(self, filename: str, project_id: Optional[str] = None, category: Optional[str] = None) -> bool:
        """
        删除TOS上的文件

        Args:
            filename: 文件名

        Returns:
            是否删除成功
        """
        if str(filename).startswith("http://") or str(filename).startswith("https://"):
            object_key = self.extract_object_key_from_url(filename)
        else:
            object_key = self.build_object_key(filename, project_id=project_id, category=category)

        if not object_key:
            logger.warning(f"Cannot resolve TOS object key for deletion: {filename}")
            return False

        try:
            if self.client:
                self.client.delete_object(self.bucket, object_key)
                logger.info(f"File deleted: {filename}")
                return True
            else:
                logger.warning("TOS SDK not available, cannot delete file")
                return False
        except Exception as e:
            logger.error(f"Failed to delete file: {str(e)}")
            return False

    def list_object_keys(self, prefix: str) -> List[str]:
        if not self.client:
            return []
        try:
            object_keys: List[str] = []
            continuation_token = None
            while True:
                result = self.client.list_objects_type2(
                    self.bucket,
                    prefix=prefix,
                    continuation_token=continuation_token,
                )
                for item in getattr(result, "contents", []) or []:
                    key = getattr(item, "key", None)
                    if key:
                        object_keys.append(key)
                if not getattr(result, "is_truncated", False):
                    break
                continuation_token = getattr(result, "next_continuation_token", None)
                if not continuation_token:
                    break
            return object_keys
        except Exception as e:
            logger.error(f"Failed to list objects under prefix {prefix}: {str(e)}")
            return []

    # ==================== 项目状态持久化（跨实例恢复） ====================
    # 云端 veFaaS 为弹性多实例，进程内内存态会随实例回收/路由到其它实例而丢失。
    # 这里将项目状态以 JSON 快照写入 TOS（私有对象，仅后端按 key 读写），
    # 用于在 get_project 未命中内存时回源恢复，避免 "未找到项目"。
    def build_state_object_key(self, project_id: str) -> str:
        return f"{self.build_project_prefix(project_id)}/state/project.json"

    def put_project_state_json(self, project_id: str, state_json: str) -> bool:
        """将项目状态 JSON 快照写入 TOS（私有对象，不设置公共读）。"""
        if not self.client:
            logger.warning("TOS SDK not available, skip project state persistence")
            return False
        object_key = self.build_state_object_key(project_id)
        try:
            self.client.put_object(
                self.bucket,
                object_key,
                content=io.BytesIO(state_json.encode("utf-8")),
                content_type="application/json",
            )
            return True
        except Exception as e:
            logger.error(f"Failed to persist project state {project_id}: {str(e)}")
            return False

    def get_project_state_json(self, project_id: str) -> Optional[str]:
        """从 TOS 读取项目状态 JSON 快照；不存在或失败返回 None。"""
        if not self.client:
            return None
        object_key = self.build_state_object_key(project_id)
        try:
            result = self.client.get_object(self.bucket, object_key)
            data = result.read()
            if isinstance(data, bytes):
                return data.decode("utf-8")
            return str(data)
        except Exception as e:
            # 对象不存在属正常情况（新项目/未持久化），仅在 debug 记录。
            logger.debug(f"Project state not found in TOS for {project_id}: {str(e)}")
            return None

    def cleanup_project_directory(self, project_id: str, keep_prefixes: Optional[List[str]] = None) -> None:
        project_prefix = self.build_project_prefix(project_id).rstrip("/")
        keep_full_prefixes = [
            f"{project_prefix}/{str(prefix).strip().strip('/')}/"
            for prefix in (keep_prefixes or [])
            if str(prefix).strip()
        ]

        for object_key in self.list_object_keys(project_prefix):
            if any(object_key.startswith(prefix) for prefix in keep_full_prefixes):
                continue
            self.delete_file(f"https://{self.bucket}.{self.endpoint}/{object_key}")


# 全局服务实例
tos_service = TOSService()
