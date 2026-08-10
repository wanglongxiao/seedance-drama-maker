# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.
import uuid
import asyncio
import re
import time
from typing import Dict, List, Optional, Any, Callable, Awaitable
from fastapi.encoders import jsonable_encoder
from app.config import config
from app.services.asr_service import asr_service
from app.utils.i18n import normalize_locale, translate
from app.utils.logger import get_logger
from app.utils.task_paths import ensure_project_temp_dir
from app.models.schemas import VideoProject, Script, GeneratedImage, GeneratedVideo, VideoSceneState, UploadedReferenceImage
from app.agents.script_agent import ScriptAgent
from app.agents.image_agent import ImageAgent
from app.agents.video_agent import VideoAgent
from app.agents.video_review_agent import VideoReviewAgent
from app.agents.merge_agent import MergeAgent
from app.services.asset_library_service import asset_library_service, AssetLibraryError
from app.services.tos_service import tos_service

logger = get_logger("main_agent")



CONTROL_COMMAND_KEYWORDS = [
    '开始生成', '开始', '生成', '启动', '创建', '开始制作', '开始创作', '执行',
    '開始生成', '開始', '生成', '啟動', '建立', '開始製作', '開始創作', '執行',
    '继续', '下一步', '确认', '确认继续', '进行下一步', '好', '好的', '行', '可以', '没问题', '确认并继续',
    '繼續', '下一步', '確認', '確認繼續', '進行下一步', '好', '好的', '行', '可以', '沒問題', '確認並繼續',
    '重新生成', '重做', '重新制作', '再来一次', '重来',
    '重新生成', '重做', '重新製作', '再來一次', '重來',
    '全自动', '一键生成', 'autorun', 'auto', '自动', '全自动生成', '一键制作', '自动运行',
    '全自動', '一鍵生成', '自動', '全自動生成', '一鍵製作', '自動運行',
    'start', 'generate', 'begin', 'create', 'launch', 'run',
    'continue', 'next', 'go', 'ok', 'okay', 'yes',
    'regenerate', 'redo', 'regen', 'restart',
    'automatic', 'automatic mode',
    '退回', '回退', '退回到', '回退到',
    'rollback', 'roll back', 'go back', 'back to',
    'ロールバック', '戻る', '前に戻る',
    'retroceder', 'volver', 'volver a',
    '開始する', '始める', 'スタート', '生成開始', '実行', '続ける', '次へ', '次に進む', '続行', 'はい', 'いいよ', 'オーケー',
    '再生成', 'やり直し', 'リジェン', '自動', '自動生成', '自動モード', 'オート',
    'iniciar', 'inicia', 'empezar', 'empieza', 'comenzar', 'comienza', 'generar', 'crear', 'ejecutar',
    'continuar', 'continua', 'siguiente', 'seguir', 'vale', 'bueno', 'sí', 'si',
    'regenerar', 'rehacer', 'regen', 'otra vez', 'automático', 'automatico', 'modo automático', 'modo automatico',
]


def normalize_command_text(message: str) -> str:
    normalized = (message or "").strip().lower()
    normalized = re.sub(r'[“”"\'`]', '', normalized)
    normalized = re.sub(r'[，。！？、；：,.!?;:()\[\]{}]+', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized.strip()


def is_control_command_text(message: str) -> bool:
    normalized = normalize_command_text(message)
    if not normalized:
        return False

    for keyword in CONTROL_COMMAND_KEYWORDS:
        normalized_keyword = normalize_command_text(keyword)
        if normalized == normalized_keyword or normalized.startswith(f"{normalized_keyword} "):
            return True
    return False


class SceneSkippedError(RuntimeError):
    """Used internally to continue the flow after removing a failed scene."""

    def __init__(self, skip_info: dict):
        super().__init__(skip_info.get("message", "scene skipped"))
        self.skip_info = skip_info


class ProjectEndedError(RuntimeError):
    """Raised when an ended project receives more work."""


def extract_aspect_ratio(user_input: str) -> Optional[str]:
    """
    从用户输入中提取图片/视频比例

    支持格式：16:9, 4:3, 9:16, 1:1, 21:9, 9:21, 2:3, 3:4 等
    也支持中文冒号：9：16

    Args:
        user_input: 用户输入文本

    Returns:
        提取到的比例字符串，如果没有则返回 None
    """
    if not user_input:
        return None

    # 先将中文冒号替换为英文冒号，方便统一处理
    normalized_input = user_input.replace('：', ':')

    # 匹配常见的比例格式
    # 支持：16:9, 4/3, 9×16, 1:1, 9:21 等
    patterns = [
        r'(\d+:\d+)',  # 16:9, 4:3, 1:1, 9:21
        r'(\d+/\d+)',  # 16/9, 4/3
        r'(\d+)\s*[xX×]\s*(\d+)',  # 16x9, 1920×1080
    ]

    for pattern in patterns:
        match = re.search(pattern, normalized_input)
        if match:
            ratio = match.group(0)
            # 统一转换为 宽:高 格式
            if 'x' in ratio.lower() or '×' in ratio or 'X' in ratio:
                # 1920x1080 格式，需要简化
                w, h = re.findall(r'\d+', ratio)
                w, h = int(w), int(h)
                # 简化比例
                from math import gcd
                g = gcd(w, h)
                ratio = f"{w//g}:{h//g}"
            elif '/' in ratio:
                ratio = ratio.replace('/', ':')

            # 验证是否是常见比例（支持更多比例）
            valid_ratios = ['16:9', '4:3', '1:1', '9:16', '21:9', '2:3', '3:2', '3:4', '9:21', '21:9']
            if ratio in valid_ratios:
                logger.info(f"Extracted aspect ratio: {ratio}")
                return ratio
            else:
                # 如果不是常见比例，但仍然匹配了格式，也返回
                logger.info(f"Extracted custom aspect ratio: {ratio}")
                return ratio

    return None


def extract_video_resolution(user_input: str) -> str:
    """
    从用户输入中提取视频分辨率。

    仅当分辨率在 yaml 的 video_generation.resolution_options 中配置时才采用，
    否则回落到默认分辨率。
    """
    default_resolution = str(config.get('video_generation.default_resolution', '480p')).strip().lower()
    configured_options = config.get('video_generation.resolution_options', ['480p', '720p']) or []
    valid_resolutions = [str(item).strip().lower() for item in configured_options if str(item).strip()]
    if default_resolution not in valid_resolutions:
        valid_resolutions.append(default_resolution)

    if not user_input:
        return default_resolution

    normalized_input = str(user_input).lower().replace('：', ':')
    for resolution in valid_resolutions:
        escaped = re.escape(resolution)
        patterns = [
            rf'(?<!\w){escaped}(?!\w)',
            rf'(?:分辨率|解析度|resolution)\s*[:：]?\s*{escaped}',
        ]
        if any(re.search(pattern, normalized_input, flags=re.IGNORECASE) for pattern in patterns):
            return resolution

    return default_resolution


class MainAgent:
    """主Agent - 协调所有子Agent完成视频生成"""

    def __init__(self):
        self.model = config.get('models.main_agent.endpoint')
        self.temperature = config.get('models.main_agent.temperature', 0.8)
        self.max_tokens = config.get('models.main_agent.max_tokens', 4096)
        self.script_agent = ScriptAgent()
        self.image_agent = ImageAgent()
        self.video_agent = VideoAgent()
        self.video_review_agent = VideoReviewAgent()
        self.merge_agent = MergeAgent()
        self.projects: Dict[str, VideoProject] = {}
        self._reference_generation_slots: Dict[str, Dict[str, List[Optional[GeneratedImage]]]] = {}
        self._reference_asset_cache: Dict[str, Dict[str, str]] = {}
        self._reference_generation_tasks: Dict[str, asyncio.Task] = {}
        self._reference_asset_regeneration_tasks: Dict[str, asyncio.Task] = {}

    def _build_asset_group_name(self, project_id: str) -> str:
        return f"seedance-project-{project_id}"

    def _build_asset_group_description(self, project: VideoProject) -> str:
        # Avoid sending raw user input to asset-group metadata because
        # the upstream API may reject sensitive text in descriptive fields.
        return str(project.project_id or "").strip()

    def _raise_if_project_ended(self, project: Optional[VideoProject]) -> None:
        if project and getattr(project, "is_ended", False):
            raise ProjectEndedError(f"Project {project.project_id} has already been ended")

    def mark_project_ended(self, project_id: str, reason: Optional[str] = None) -> Optional[VideoProject]:
        project = self.projects.get(project_id)
        if not project:
            return None

        project.is_ended = True
        project.end_reason = reason or "ended"
        project.status = "ended"
        project.current_step = "ended"

        generation_task = self._reference_generation_tasks.pop(project_id, None)
        if generation_task and not generation_task.done():
            generation_task.cancel()

        for task_key, task in list(self._reference_asset_regeneration_tasks.items()):
            if not task_key.startswith(f"{project_id}:"):
                continue
            self._reference_asset_regeneration_tasks.pop(task_key, None)
            if task and not task.done():
                task.cancel()

        self._reference_generation_slots.pop(project_id, None)
        return project

    def remove_project(self, project_id: str) -> None:
        self.projects.pop(project_id, None)
        self._reference_generation_slots.pop(project_id, None)
        self._reference_asset_cache.pop(project_id, None)
        self._reference_generation_tasks.pop(project_id, None)
        for task_key in list(self._reference_asset_regeneration_tasks.keys()):
            if task_key.startswith(f"{project_id}:"):
                self._reference_asset_regeneration_tasks.pop(task_key, None)

    def _ensure_project_asset_group(self, project: VideoProject) -> Dict[str, Any]:
        self._raise_if_project_ended(project)
        if getattr(project, "asset_group_id", None):
            return {
                "Id": project.asset_group_id,
                "Name": project.asset_group_name,
                "ProjectName": project.asset_project_name or config.asset_library_project_name,
            }

        group_name = self._build_asset_group_name(project.project_id)
        group = asset_library_service.ensure_project_asset_group(
            project_id=project.project_id,
            group_name=group_name,
            description=self._build_asset_group_description(project),
            project_name=config.asset_library_project_name,
        )
        project.asset_group_id = str(group.get("Id") or "").strip() or None
        project.asset_group_name = str(group.get("Name") or group_name).strip() or group_name
        project.asset_project_name = str(group.get("ProjectName") or config.asset_library_project_name).strip()
        if not project.asset_group_id:
            raise AssetLibraryError(f"Asset group id is missing for project {project.project_id}")
        return group

    def _register_uploaded_portrait_assets(self, project: VideoProject) -> None:
        self._ensure_project_asset_group(project)
        for asset in getattr(project, "uploaded_reference_images", []) or []:
            if getattr(asset, "reference_type", None) != "character":
                continue
            if getattr(asset, "asset_id", None):
                continue
            asset_info = asset_library_service.register_image_asset(
                group_id=project.asset_group_id,
                url=asset.url,
                name=asset.name or f"character-{project.project_id}",
                project_name=project.asset_project_name,
            )
            asset.asset_id = str(asset_info.get("Id") or "").strip() or None
            asset.asset_status = str(asset_info.get("Status") or "").strip() or None

    def _find_uploaded_character_asset(
        self,
        project: VideoProject,
        asset_name: str,
        source_url: str,
    ) -> Optional[UploadedReferenceImage]:
        normalized_name = self._normalize_name_key(asset_name)
        for item in getattr(project, "uploaded_reference_images", []) or []:
            if getattr(item, "reference_type", None) != "character":
                continue
            if source_url and str(getattr(item, "url", "") or "").strip() == source_url:
                return item
            if normalized_name and self._normalize_name_key(getattr(item, "name", "")) == normalized_name:
                return item
        return None

    def _register_generated_portrait_asset(
        self,
        project: VideoProject,
        image: GeneratedImage,
        asset_name: str,
    ) -> GeneratedImage:
        self._ensure_project_asset_group(project)
        asset_info = asset_library_service.register_image_asset(
            group_id=project.asset_group_id,
            url=image.url,
            name=asset_name or f"character-{project.project_id}",
            project_name=project.asset_project_name,
        )
        image.asset_id = str(asset_info.get("Id") or "").strip() or None
        image.asset_status = str(asset_info.get("Status") or "").strip() or None
        return image

    async def create_project(
        self,
        user_input: str,
        reference_images: List[str] = None,
        uploaded_reference_images: List[Dict[str, Any]] = None,
        audio_url: str = None,
        output_language: str = "zh-CN",
        use_original_reference: bool = False,
        project_id: Optional[str] = None,
    ) -> VideoProject:
        """
        创建新的视频项目

        Args:
            user_input: 用户输入文本
            reference_images: 参考图片URL列表
            audio_url: 音频文件URL

        Returns:
            视频项目对象
        """
        project_id = project_id or str(uuid.uuid4())[:8]
        uploaded_assets = self._normalize_uploaded_reference_images(
            uploaded_reference_images=uploaded_reference_images,
            reference_images=reference_images,
            output_language=output_language,
        )
        task_temp_dir = ensure_project_temp_dir(project_id)

        logger.log_agent_call("MainAgent", "create_project", {
            "project_id": project_id,
            "user_input": user_input,
            "has_images": bool(reference_images),
            "uploaded_reference_images": len(uploaded_assets),
            "has_audio": bool(audio_url),
            "use_original_reference": bool(use_original_reference),
        })

        # 如果有音频，先进行ASR识别
        audio_text = None
        if audio_url:
            logger.info(f"Processing audio for project {project_id}")
            try:
                audio_text = asr_service.recognize(audio_url)
                logger.info(f"Audio recognized: {audio_text[:100]}...")
            except Exception as e:
                logger.error(f"ASR failed: {str(e)}")

        # 从用户输入中提取图片/视频比例
        aspect_ratio = extract_aspect_ratio(user_input)
        if aspect_ratio:
            logger.info(f"Project {project_id} aspect ratio: {aspect_ratio}")
        video_resolution = extract_video_resolution(user_input)
        logger.info(f"Project {project_id} video resolution: {video_resolution}")

        # 初始化 combined_input，包含用户初始输入
        combined_input = f"原始需求：{user_input}"
        if audio_text:
            combined_input += f"\n语音内容：{audio_text}"

        project = VideoProject(
            project_id=project_id,
            user_input=user_input,
            combined_input=combined_input,
            reference_images=[item.url for item in uploaded_assets],
            audio_url=audio_url,
            status="created",
            current_step="initialized",
            aspect_ratio=aspect_ratio,
            video_resolution=video_resolution,
            output_language=normalize_locale(output_language),
            use_original_reference=bool(use_original_reference),
            uploaded_reference_images=uploaded_assets,
            task_tos_prefix=tos_service.build_project_prefix(project_id),
            task_temp_dir=str(task_temp_dir),
            asset_group_name=self._build_asset_group_name(project_id),
            asset_project_name=config.asset_library_project_name,
        )

        self.projects[project_id] = project
        await asyncio.to_thread(self._ensure_project_asset_group, project)
        await asyncio.to_thread(self._register_uploaded_portrait_assets, project)

        logger.info(f"Project created with combined_input: {combined_input[:200]}...")

        return project

    def _normalize_uploaded_reference_images(
        self,
        uploaded_reference_images: Optional[List[Dict[str, Any]]] = None,
        reference_images: Optional[List[str]] = None,
        output_language: Optional[str] = None,
    ) -> List[UploadedReferenceImage]:
        normalized: List[UploadedReferenceImage] = []
        locale = normalize_locale(output_language)
        upload_total_limit = max(1, int(config.get("video_generation.reference_images.upload_max_count", 30)))
        upload_character_limit = max(1, int(config.get("video_generation.reference_images.upload_character_max_count", 10)))
        upload_scene_limit = max(1, int(config.get("video_generation.reference_images.upload_scene_max_count", 20)))
        for index, item in enumerate(uploaded_reference_images or [], start=1):
            url = str((item or {}).get("url") or "").strip()
            if not url:
                continue
            reference_type = str((item or {}).get("reference_type") or "character").strip().lower()
            if reference_type not in {"character", "scene"}:
                reference_type = "character"
            name = str((item or {}).get("name") or "").strip()
            if not name:
                raise ValueError(
                    translate(
                        locale,
                        "error.reference_name_required",
                        index=index,
                        type=translate(
                            locale,
                            "label.reference_type_character" if reference_type == "character" else "label.reference_type_scene"
                        ),
                    )
                )
            normalized.append(UploadedReferenceImage(url=url, name=name, reference_type=reference_type))

        if normalized:
            if len(normalized) > upload_total_limit:
                raise ValueError(translate(locale, "error.reference_upload_limit_total", count=upload_total_limit))
            character_count = sum(1 for item in normalized if item.reference_type == "character")
            scene_count = sum(1 for item in normalized if item.reference_type == "scene")
            if character_count > upload_character_limit:
                raise ValueError(translate(locale, "error.reference_upload_limit_character", count=upload_character_limit))
            if scene_count > upload_scene_limit:
                raise ValueError(translate(locale, "error.reference_upload_limit_scene", count=upload_scene_limit))
            return normalized

        for index, url in enumerate(reference_images or [], start=1):
            normalized.append(UploadedReferenceImage(
                url=url,
                name=f"character_{index}",
                reference_type="character",
            ))
        if len(normalized) > upload_total_limit:
            raise ValueError(translate(locale, "error.reference_upload_limit_total", count=upload_total_limit))
        return normalized

    def _slugify_storage_name(self, value: str, fallback: str) -> str:
        cleaned = re.sub(r"\s+", "_", str(value or "").strip().lower())
        cleaned = re.sub(r"[^a-z0-9_-]+", "", cleaned)
        return cleaned or fallback

    def _normalize_name_key(self, value: Optional[str]) -> str:
        normalized = re.sub(r"\s+", "", str(value or "").strip().lower())
        normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff_-]+", "", normalized)
        return normalized

    def _extract_scene_reference_definitions(self, script: Script, limit: int) -> List[Dict[str, str]]:
        definitions: List[Dict[str, str]] = []
        seen = set()

        for index, item in enumerate(getattr(script, "scene_definitions", []) or [], start=1):
            name = str(getattr(item, "name", "") or "").strip()[:24] or f"Scene {index}"
            description = str(getattr(item, "description", "") or "").strip() or name
            key = self._normalize_name_key(name)
            if not key or key in seen:
                continue
            seen.add(key)
            definitions.append({"name": name, "description": description})
            if len(definitions) >= limit:
                break

        return definitions[:limit]
    def _resolve_uploaded_assets_for_targets(
        self,
        assets: List[UploadedReferenceImage],
        target_names: List[str],
    ) -> Dict[str, List[UploadedReferenceImage]]:
        assignments: Dict[str, List[UploadedReferenceImage]] = {}

        for target_name in target_names:
            target_key = self._normalize_name_key(target_name)
            matched_assets: List[UploadedReferenceImage] = []

            if target_key:
                matched_assets = [
                    asset for asset in assets
                    if self._normalize_name_key(asset.name) == target_key
                ]

            assignments[target_name] = matched_assets

        return assignments

    def _store_reference_asset(
        self,
        project: VideoProject,
        image: GeneratedImage,
        category: str,
        asset_name: str,
        index: int,
        used_original: bool = False,
    ) -> GeneratedImage:
        source_url = str(getattr(image, "url", "") or "").strip()
        project_prefix = str(getattr(project, "task_tos_prefix", "") or "").rstrip("/")
        target_prefix = f"{project_prefix}/references/{category}s/" if project_prefix else ""
        object_key = tos_service.extract_object_key_from_url(source_url)
        cache = self._reference_asset_cache.setdefault(project.project_id, {})

        revision_tag = uuid.uuid4().hex[:8]
        filename = (
            f"{index + 1:02d}_{self._slugify_storage_name(asset_name, f'{category}_{index + 1}')}"
            f"_{revision_tag}.png"
        )
        started_at = time.perf_counter()
        if object_key and target_prefix and object_key.startswith(target_prefix):
            stored_url = source_url
        elif source_url and source_url in cache:
            stored_url = cache[source_url]
            logger.info(
                "Reusing cached reference asset for project %s: %s -> %s",
                project.project_id,
                source_url,
                stored_url,
            )
        else:
            stored_url = tos_service.copy_url_to_tos(
                image.url,
                target_filename=filename,
                project_id=project.project_id,
                category=f"references/{category}s",
            )
            if source_url:
                cache[source_url] = stored_url
        logger.debug(
            "Reference asset stored in %.2f ms",
            round((time.perf_counter() - started_at) * 1000, 2),
        )
        image.url = stored_url
        image.name = asset_name
        image.reference_type = category
        image.used_original = used_original
        image.regenerate_locked = used_original
        image.is_reference = True
        image.source = "uploaded_original" if used_original else "generated"

        if category == "character":
            uploaded_asset = self._find_uploaded_character_asset(project, asset_name, source_url)
            if uploaded_asset and getattr(uploaded_asset, "asset_id", None):
                image.asset_id = uploaded_asset.asset_id
                image.asset_status = uploaded_asset.asset_status or "Active"
            elif getattr(image, "asset_id", None):
                image.asset_status = image.asset_status or "Active"
            else:
                self._register_generated_portrait_asset(project, image, asset_name)
        return image

    async def _store_reference_asset_async(
        self,
        project: VideoProject,
        image: GeneratedImage,
        category: str,
        asset_name: str,
        index: int,
        used_original: bool = False,
    ) -> GeneratedImage:
        return await asyncio.to_thread(
            self._store_reference_asset,
            project,
            image,
            category,
            asset_name,
            index,
            used_original,
        )

    def _expected_reference_counts(self, project: VideoProject) -> Dict[str, int]:
        character_limit = max(1, int(config.get("video_generation.reference_images.character_max_count", 20)))
        scene_limit = max(1, int(config.get("video_generation.reference_images.scene_max_count", 40)))
        character_count = len(list((getattr(getattr(project, "script", None), "characters", None) or [])[:character_limit]))
        scene_count = len(self._extract_scene_reference_definitions(getattr(project, "script", None), scene_limit))
        return {
            "characters": character_count,
            "scenes": scene_count,
            "total": character_count + scene_count,
        }

    def _is_reference_library_ready_for_confirmation(self, project: VideoProject) -> bool:
        session_slots = self._get_reference_generation_session(project)
        if session_slots is not None:
            return all(image is not None for image in session_slots.get("characters", [])) and all(
                image is not None for image in session_slots.get("scenes", [])
            )

        expected = self._expected_reference_counts(project)
        actual_character_count = len(getattr(project, "character_reference_images", []) or [])
        actual_scene_count = len(getattr(project, "scene_reference_images", []) or [])
        return (
            actual_character_count >= expected["characters"]
            and actual_scene_count >= expected["scenes"]
        )

    def _serialize_reference_images(
        self,
        project: VideoProject,
        images: List[GeneratedImage],
        reference_type: str,
    ) -> List[Dict[str, Any]]:
        serialized: List[Dict[str, Any]] = []
        for fallback_index, image in enumerate(images):
            item = image.dict()
            slot_index = self._get_reference_slot_index(
                project,
                reference_type,
                getattr(image, "name", ""),
            )
            item["slot_index"] = slot_index if slot_index >= 0 else fallback_index
            serialized.append(item)
        return serialized

    def _build_reference_output(
        self,
        project: VideoProject,
        message: Optional[str] = None,
        include_default_message: bool = False,
    ) -> Dict[str, Any]:
        character_images = list(getattr(project, "character_reference_images", []) or [])
        scene_images = list(getattr(project, "scene_reference_images", []) or [])
        images = character_images + scene_images
        serialized_character_images = self._serialize_reference_images(project, character_images, "character")
        serialized_scene_images = self._serialize_reference_images(project, scene_images, "scene")
        expected_counts = self._expected_reference_counts(project)
        resolved_message = message
        if resolved_message is None and include_default_message:
            resolved_message = self._t(project, "message.reference.confirm_prompt")
        return {
            "step": "reference_image",
            "count": len(images),
            "urls": [image.url for image in images],
            "images": serialized_character_images + serialized_scene_images,
            "character_images": serialized_character_images,
            "scene_images": serialized_scene_images,
            "library": {
                "characters": serialized_character_images,
                "scenes": serialized_scene_images,
            },
            "ready_for_confirmation": self._is_reference_library_ready_for_confirmation(project),
            "expected_count": expected_counts["total"],
            "expected_character_count": expected_counts["characters"],
            "expected_scene_count": expected_counts["scenes"],
            "message": resolved_message,
        }

    def _sync_reference_library_state(
        self,
        project: VideoProject,
        character_images: List[Optional[GeneratedImage]],
        scene_images: List[Optional[GeneratedImage]],
    ) -> None:
        completed_character_images = [image for image in character_images if image is not None]
        completed_scene_images = [image for image in scene_images if image is not None]
        project.character_reference_images = completed_character_images
        project.scene_reference_images = completed_scene_images
        project.reference_image_library = {
            "characters": completed_character_images,
            "scenes": completed_scene_images,
        }
        project.images = completed_character_images + completed_scene_images
        project.reference_image = (
            project.character_reference_images[0]
            if project.character_reference_images
            else (project.scene_reference_images[0] if project.scene_reference_images else None)
        )

    def _start_reference_generation_session(
        self,
        project: VideoProject,
        character_count: int,
        scene_count: int,
    ) -> Dict[str, List[Optional[GeneratedImage]]]:
        slots = {
            "characters": [None] * max(0, character_count),
            "scenes": [None] * max(0, scene_count),
        }
        self._reference_generation_slots[project.project_id] = slots
        return slots

    def _get_reference_generation_session(
        self,
        project: VideoProject,
    ) -> Optional[Dict[str, List[Optional[GeneratedImage]]]]:
        return self._reference_generation_slots.get(project.project_id)

    def _finish_reference_generation_session(self, project: VideoProject) -> None:
        self._reference_generation_slots.pop(project.project_id, None)

    def _get_reference_slot_index(
        self,
        project: VideoProject,
        reference_type: str,
        normalized_name: str,
    ) -> int:
        normalized_name = self._normalize_name_key(normalized_name)
        if reference_type == "character":
            targets = [getattr(character, "name", "") for character in (getattr(project.script, "characters", None) or [])]
        else:
            scene_limit = max(1, int(config.get("video_generation.reference_images.scene_max_count", 40)))
            targets = [item["name"] for item in self._extract_scene_reference_definitions(project.script, scene_limit)]

        for index, name in enumerate(targets):
            if self._normalize_name_key(name) == normalized_name:
                return index
        return -1

    def _build_reference_asset_task_key(self, project_id: str, reference_type: str, asset_name: str) -> str:
        return f"{project_id}:{reference_type}:{self._normalize_name_key(asset_name)}"

    def _select_reference_assets_for_scene(self, project: VideoProject, scene) -> List[GeneratedImage]:
        selected: List[GeneratedImage] = []
        present_characters = [
            self._normalize_name_key(name)
            for name in (getattr(scene, "characters_present", None) or [])
            if str(name).strip()
        ]
        for image in getattr(project, "character_reference_images", []) or []:
            image_name = self._normalize_name_key(getattr(image, "name", ""))
            if present_characters and image_name in present_characters:
                selected.append(image)

        raw_scene_name = str(getattr(scene, "scene_name", "") or "")
        scene_name_keys = [
            self._normalize_name_key(part)
            for part in re.split(r"[、,，/|]+", raw_scene_name)
            if str(part).strip()
        ]
        matched_scene_images = []
        for image in getattr(project, "scene_reference_images", []) or []:
            image_name = self._normalize_name_key(getattr(image, "name", ""))
            if scene_name_keys and image_name in scene_name_keys:
                matched_scene_images.append(image)
        if matched_scene_images:
            selected.extend(matched_scene_images[: max(1, len(scene_name_keys))])

        if not selected and getattr(project, "scene_reference_images", None):
            selected.extend(project.scene_reference_images[:2])
        if not selected and getattr(project, "character_reference_images", None):
            selected.extend(project.character_reference_images[:2])

        deduped: List[GeneratedImage] = []
        seen_urls = set()
        for image in selected:
            if image.url in seen_urls:
                continue
            seen_urls.add(image.url)
            deduped.append(image)
        return deduped

    def archive_scene_video(
        self,
        project: VideoProject,
        video: GeneratedVideo,
        generation_count: Optional[int] = None,
    ) -> GeneratedVideo:
        """Copy a generated scene video into the task-scoped TOS directory."""
        if not video or not getattr(video, "url", None):
            raise ValueError("Video url is required for archiving")

        object_key = tos_service.extract_object_key_from_url(video.url)
        project_prefix = str(getattr(project, "task_tos_prefix", "") or "").rstrip("/")
        scene_prefix = f"{project_prefix}/videos/scenes/" if project_prefix else ""
        if object_key and scene_prefix and object_key.startswith(scene_prefix):
            return video

        scene_number = max(1, int(getattr(video, "scene_number", 1) or 1))
        attempt = max(1, int(generation_count or 1))
        video_ext = str(config.get('video_generation.output_format', 'mov')).strip().lstrip('.').lower() or "mov"
        filename = f"scene_{scene_number:02d}_attempt_{attempt:02d}.{video_ext}"
        started_at = time.perf_counter()
        video.url = tos_service.copy_url_to_tos(
            source_url=video.url,
            target_filename=filename,
            project_id=project.project_id,
            category="videos/scenes",
        )
        logger.debug(
            "Archived scene video in %.2f ms",
            round((time.perf_counter() - started_at) * 1000, 2),
        )
        return video

    async def archive_scene_video_async(
        self,
        project: VideoProject,
        video: GeneratedVideo,
        generation_count: Optional[int] = None,
    ) -> GeneratedVideo:
        return await asyncio.to_thread(
            self.archive_scene_video,
            project,
            video,
            generation_count,
        )

    def set_project_output_language(self, project_id: str, output_language: Optional[str]) -> None:
        project = self.projects.get(project_id)
        if project:
            project.output_language = normalize_locale(output_language)

    def set_project_video_review_mode(self, project_id: str, review_mode: Optional[str]) -> None:
        project = self.projects.get(project_id)
        if project:
            project.video_review_mode = self._normalize_review_mode(review_mode)

    def _project_language(self, project: Optional[VideoProject], fallback: str = "zh-CN") -> str:
        return normalize_locale(getattr(project, "output_language", fallback))

    def _t(self, project: Optional[VideoProject], key: str, **kwargs) -> str:
        return translate(self._project_language(project), key, **kwargs)

    def _normalize_review_mode(self, review_mode: Optional[str]) -> str:
        return "auto" if str(review_mode or "").strip().lower() == "auto" else "manual"

    def _ensure_video_flow_state(self, project: VideoProject, review_mode: str, reset: bool = False) -> None:
        if reset:
            project.videos = []
            project.video_scene_states = {}
            project.next_scene_index = 0
            project.generated_video_seeds = []

        project.video_review_mode = self._normalize_review_mode(review_mode)
        project.video_scene_states = dict(project.video_scene_states or {})
        project.generated_video_seeds = list(getattr(project, "generated_video_seeds", []) or [])
        if project.next_scene_index < 0:
            project.next_scene_index = 0

    def _get_scene_state(self, project: VideoProject, scene_number: int) -> VideoSceneState:
        state = project.video_scene_states.get(scene_number)
        if not state:
            state = VideoSceneState(scene_number=scene_number)
            project.video_scene_states[scene_number] = state
        return state

    def _next_scene_archive_attempt(self, scene_state: VideoSceneState) -> int:
        scene_state.archive_generation_count = max(0, int(scene_state.archive_generation_count or 0)) + 1
        return scene_state.archive_generation_count

    def _upsert_project_video(self, project: VideoProject, video: GeneratedVideo) -> None:
        for index, existing in enumerate(project.videos):
            if existing.scene_number == video.scene_number:
                project.videos[index] = video
                project.videos.sort(key=lambda item: item.scene_number)
                return
        project.videos.append(video)
        project.videos.sort(key=lambda item: item.scene_number)

    def _normalize_video_seed(self, seed: Optional[str]) -> Optional[str]:
        if seed is None:
            return None
        normalized = str(seed).strip()
        return normalized or None

    def _has_duplicate_video_seed(self, project: VideoProject, seed: Optional[str]) -> bool:
        normalized_seed = self._normalize_video_seed(seed)
        if not normalized_seed:
            return False
        return normalized_seed in set(project.generated_video_seeds or [])

    def _register_video_seed(self, project: VideoProject, seed: Optional[str]) -> None:
        normalized_seed = self._normalize_video_seed(seed)
        if not normalized_seed:
            return
        if normalized_seed not in project.generated_video_seeds:
            project.generated_video_seeds.append(normalized_seed)

    def _recalculate_script_total_duration(self, project: VideoProject) -> None:
        if getattr(project, "script", None):
            project.script.total_duration = sum(max(0, int(scene.duration or 0)) for scene in project.script.scenes)

    def _remove_scene_from_project(self, project: VideoProject, scene_number: int) -> dict:
        if not getattr(project, "script", None):
            raise ValueError("Script not found")

        scene_index = scene_number - 1
        if scene_index < 0 or scene_index >= len(project.script.scenes):
            raise ValueError(f"Scene {scene_number} not found")

        removed_scene = project.script.scenes.pop(scene_index)
        for index, scene in enumerate(project.script.scenes, start=1):
            scene.scene_number = index
        self._recalculate_script_total_duration(project)

        updated_videos: List[GeneratedVideo] = []
        for video in project.videos:
            if video.scene_number == scene_number:
                continue
            if video.scene_number > scene_number:
                video.scene_number -= 1
            updated_videos.append(video)
        project.videos = sorted(updated_videos, key=lambda item: item.scene_number)

        updated_scene_states: Dict[int, VideoSceneState] = {}
        for original_number in sorted(project.video_scene_states.keys()):
            if original_number == scene_number:
                continue
            state = project.video_scene_states[original_number]
            if original_number > scene_number:
                state.scene_number = original_number - 1
                updated_scene_states[original_number - 1] = state
            else:
                updated_scene_states[original_number] = state
        project.video_scene_states = updated_scene_states

        if project.next_scene_index > scene_index:
            project.next_scene_index -= 1
        project.next_scene_index = max(0, min(project.next_scene_index, len(project.script.scenes)))

        return {
            "removed_scene": removed_scene,
            "removed_scene_number": scene_number,
            "next_scene_number": scene_index + 1 if scene_index < len(project.script.scenes) else None,
            "total_scenes": len(project.script.scenes),
        }

    async def _notify_scene_skipped(
        self,
        project: VideoProject,
        websocket,
        scene_number: int,
        reason: str,
    ) -> dict:
        skip_result = self._remove_scene_from_project(project, scene_number)
        next_scene_number = skip_result["next_scene_number"]
        total_scenes = skip_result["total_scenes"]
        if next_scene_number is not None:
            message = self._t(
                project,
                "message.video.scene_skipped_continue",
                scene=scene_number,
                next_scene=next_scene_number,
                reason=reason,
            )
        else:
            message = self._t(
                project,
                "message.video.scene_skipped_no_next",
                scene=scene_number,
                reason=reason,
            )

        payload = {
            "status": "scene_skipped",
            "scene_number": scene_number,
            "next_scene_number": next_scene_number,
            "total_scenes": total_scenes,
            "message": message,
            "reason": reason,
            "script": project.script.dict() if getattr(project, "script", None) else None,
        }
        await self._send_agent_output(websocket, "video_agent", payload)
        return payload

    async def skip_scene(
        self,
        project_id: str,
        scene_number: int,
        websocket=None,
        reason: Optional[str] = None,
    ) -> dict:
        project = self.projects.get(project_id)
        if not project:
            raise ValueError(f"Project not found: {project_id}")
        resolved_reason = reason or self._t(project, "message.video.scene_skipped_reason_user")
        return await self._notify_scene_skipped(project, websocket, scene_number, resolved_reason)

    def can_rewrite_script(self, project: Optional[VideoProject]) -> bool:
        if not project or not getattr(project, "script", None):
            return False
        if getattr(project, "video_review_mode", "manual") != "manual":
            return False
        if getattr(project, "character_reference_images", None) or getattr(project, "scene_reference_images", None):
            return False
        return getattr(project, "current_step", "") == "script_generated"

    def _get_previous_video_url(self, project: VideoProject, scene_index: int) -> Optional[str]:
        if scene_index <= 0:
            return None
        previous_scene_number = scene_index
        for video in project.videos:
            if video.scene_number == previous_scene_number:
                return video.url
        return None

    async def generate_video(
        self,
        project_id: str,
        websocket=None
    ) -> VideoProject:
        """
        执行完整的视频生成流程（支持两步图片生成）

        Args:
            project_id: 项目ID
            websocket: WebSocket连接（用于实时更新）

        Returns:
            完成的视频项目
        """
        project = self.projects.get(project_id)
        if not project:
            raise ValueError(f"Project not found: {project_id}")

        logger.log_agent_call("MainAgent", "generate_video", {"project_id": project_id})

        try:
            # Step 1: 生成剧本
            await self._update_progress(websocket, "script_agent", 10, self._t(project, "progress.script.generating"))
            script = await self._generate_script(project)
            project.script = script
            project.current_step = "script_generated"
            await self._send_agent_output(websocket, "script_agent", script.dict())

            # Step 2: 生成参考图库- 第一步
            await self._update_progress(websocket, "image_agent", 30, self._t(project, "progress.reference.generating"))
            reference_image = await self.generate_reference_image_with_retry(project)
            project.reference_image = reference_image  # 保存参考图库到项目
            project.current_step = "reference_image_generated"
            await self._send_agent_output(
                websocket,
                "image_agent",
                self._build_reference_output(project, self._t(project, "message.reference.confirm_prompt")),
            )

            # 等待用户确认参考图库
            logger.info(f"Waiting for user confirmation of reference image (参考图库) for project {project_id}")
            await self._update_progress(websocket, "image_agent", 35, self._t(project, "progress.reference.completed_wait"))
            
            # 设置项目状态为等待确认
            project.status = "waiting_reference_confirmation"
            project.current_step = "waiting_reference_confirmation"
            
            # 返回项目，等待用户确认
            return project

        except Exception as e:
            logger.error(f"Video generation failed: {str(e)}")
            project.status = "failed"
            await self._update_progress(websocket, "error", 0, self._t(project, "error.generation_failed", error=str(e)))
            raise

    async def continue_generate_after_reference_confirmation(
        self,
        project_id: str,
        websocket=None,
        review_mode: str = None,
        merge_after_videos: bool = True,
        resume: bool = False,
    ) -> VideoProject:
        """
        用户确认参考图库后继续生成流程

        新流程：
        1. 根据参考图和首分镜脚本生成首分镜视频
        2. 视频审核Agent审核生成的视频（评分制，>=80分通过）
        3. 如果不符合要求则重新生成（支持自动和手动模式，最多重试3次）
        4. 审核通过后，根据前一个分镜视频 + 参考图 + 脚本生成延伸视频
        5. 重复审核步骤
        6. 所有分镜视频生成且审核完毕后，合成视频

        Args:
            project_id: 项目ID
            websocket: WebSocket连接（用于实时更新）
            review_mode: 审核模式 - "auto"=自动审核（自动重试）, "manual"=手动审核（等待用户确认）
                        如果为None，则从config读取默认值
            merge_after_videos: 视频生成与审核完成后是否继续自动合成

        Returns:
            完成的视频项目
        """
        project = self.projects.get(project_id)
        if not project:
            raise ValueError(f"Project not found: {project_id}")
        self._raise_if_project_ended(project)

        if not hasattr(project, 'reference_image') or not project.reference_image:
            raise ValueError(f"Reference image not found for project {project_id}")

        # 从config读取审核模式（如果未指定）
        if review_mode is None:
            review_mode = getattr(project, "video_review_mode", None) or config.get('video_review.default_mode', 'manual')
        review_mode = self._normalize_review_mode(review_mode)
        # 从config读取最大自动重试次数（分镜视频生成失败/审核失败共享）
        configured_retries = config.get('video_review.max_retries', 2)
        max_auto_retries = max(0, int(configured_retries))
        # 每个分镜的总生成次数上限（首次生成+各类重生成合计）
        configured_total_limit = config.get('video_generation.scene_total_generate_limit', 3)
        max_total_generations = max(1, int(configured_total_limit))
        # 从config读取通过阈值
        pass_threshold = config.get('video_review.pass_threshold', 70)

        logger.log_agent_call("MainAgent", "continue_generate_after_reference_confirmation", {
            "project_id": project_id,
            "review_mode": review_mode,
            "max_auto_retries": max_auto_retries,
            "max_total_generations": max_total_generations,
            "pass_threshold": pass_threshold
        })
        logger.info(
            f"[FLOW] Starting/resuming video flow. Mode: {review_mode}, "
            f"AutoRetries: {max_auto_retries}, TotalGenerations: {max_total_generations}, Threshold: {pass_threshold}, Resume: {resume}"
        )

        try:
            # 保存参考图到项目
            project.images = list(getattr(project, "character_reference_images", []) or []) + list(getattr(project, "scene_reference_images", []) or [])
            project.current_step = "images_generated"
            self._ensure_video_flow_state(project, review_mode=review_mode, reset=not resume)

            # 新流程：逐个生成分镜视频并审核
            num_scenes = len(project.script.scenes)
            if num_scenes == 0:
                if not project.videos:
                    raise RuntimeError(self._t(project, "error.no_scenes_remaining_after_skip"))
                project.next_scene_index = 0
                project.current_step = "videos_generated"
                if review_mode == "manual":
                    project.status = "manual_wait_merge"
                    project.progress = 90
                    await self._send_agent_output(websocket, "video_agent", {
                        "status": "manual_wait_merge",
                        "scene_number": 0,
                        "total_scenes": 0,
                        "message": self._t(project, "message.video.no_remaining_scenes_ready_merge")
                    })
                    return project

            scene_index = min(project.next_scene_index, len(project.script.scenes))
            while scene_index < len(project.script.scenes):
                self._raise_if_project_ended(project)
                num_scenes = len(project.script.scenes)
                scene = project.script.scenes[scene_index]
                scene_number = scene_index + 1

                progress_base = 50 + (scene_index / num_scenes) * 40
                previous_video_url = self._get_previous_video_url(project, scene_index)

                try:
                    video, final_approved, _, final_score = await self._generate_and_review_video_with_retries(
                        project=project,
                        scene=scene,
                        scene_number=scene_number,
                        previous_video_url=previous_video_url,
                        websocket=websocket,
                        review_mode=review_mode,
                        progress_base=progress_base,
                        num_scenes=num_scenes,
                        max_retries=max_auto_retries,
                        pass_threshold=pass_threshold,
                        max_total_generations=max_total_generations,
                    )
                except SceneSkippedError:
                    if review_mode == "manual":
                        return project
                    scene_index = min(project.next_scene_index, len(project.script.scenes))
                    continue

                self._upsert_project_video(project, video)
                scene_state = self._get_scene_state(project, scene_number)
                scene_state.completed = True
                scene_state.approved = bool(final_approved)
                project.next_scene_index = min(scene_index + 1, len(project.script.scenes))

                logger.info(f"[FLOW] Scene {scene_number} completed. Approved: {final_approved}, Score: {final_score}")

                # 手动模式：每次只完成一个分镜，然后停住等待 next/go/下一步
                if review_mode == "manual":
                    if project.next_scene_index < len(project.script.scenes):
                        project.status = "manual_wait_next_scene"
                        project.current_step = "videos_generated"
                        await self._send_agent_output(websocket, "video_agent", {
                            "status": "manual_wait_next_scene",
                            "scene_number": scene_number,
                            "next_scene_number": project.next_scene_index + 1,
                            "total_scenes": len(project.script.scenes),
                            "message": translate(
                                self._project_language(project),
                                "message.video.manual_mode_keep_current",
                                scene=scene_number,
                                score=final_score,
                                feedback=self._get_scene_state(project, scene_number).last_feedback or ""
                            )
                        })
                        return project
                scene_index += 1

            project.next_scene_index = len(project.script.scenes)
            project.current_step = "videos_generated"

            if review_mode == "manual":
                project.status = "manual_wait_merge"
                project.progress = 90
                last_scene_number = len(project.script.scenes)
                last_scene_state = self._get_scene_state(project, last_scene_number) if last_scene_number > 0 else None
                await self._send_agent_output(websocket, "video_agent", {
                    "status": "manual_wait_merge",
                    "scene_number": last_scene_number,
                    "total_scenes": len(project.script.scenes),
                    "message": translate(
                        self._project_language(project),
                        "message.video.no_remaining_scenes_ready_merge" if last_scene_state is None else "message.video.manual_mode_keep_current",
                        scene=last_scene_number,
                        score=last_scene_state.last_score if last_scene_state else 0,
                        feedback=last_scene_state.last_feedback if last_scene_state else ""
                    )
                })
                return project

            await self._send_agent_output(websocket, "video_agent", {
                "count": len(project.videos),
                "urls": [v.url for v in project.videos],
                "videos": [{"scene_number": v.scene_number, "url": v.url} for v in project.videos]
            })

            if merge_after_videos:
                # 合成视频
                await self._update_progress(websocket, "merge_agent", 90, self._t(project, "progress.merge.generating"))
                logger.info(f"[FLOW] Merging {len(project.videos)} videos")
                
                # 发送合成开始消息（触发前端显示转转效果）
                await self._send_agent_output(websocket, "merge_agent", {
                    "status": "merging",
                    "message": self._t(project, "progress.merge.generating")
                })
                
                final_video_url = await self._merge_videos(project)
                project.final_video_url = final_video_url
                project.current_step = "completed"
                project.status = "completed"
                project.progress = 100
                await self._send_agent_output(websocket, "merge_agent", {
                    "final_video_url": final_video_url
                })

                logger.info(f"[FLOW] Video generation completed for project {project_id}")
            else:
                project.status = "videos_generated"
                project.progress = 90
                logger.info(f"[FLOW] Video generation completed without merge for project {project_id}")

        except Exception as e:
            logger.error(f"[FLOW] Video generation failed: {str(e)}")
            project.status = "failed"
            await self._update_progress(websocket, "error", 0, self._t(project, "error.generation_failed", error=str(e)))
            raise

        return project

    async def _generate_and_review_video_with_retries(
        self,
        project: VideoProject,
        scene,
        scene_number: int,
        previous_video_url: str,
        websocket,
        review_mode: str,
        progress_base: float,
        num_scenes: int,
        max_retries: int,
        pass_threshold: int,
        max_total_generations: int,
    ) -> tuple:
        """
        带重试机制的视频审核

        Args:
            project: 项目对象
            scene: 当前分镜
            scene_number: 分镜编号
            video: 生成的视频（会被更新为重新生成的视频）
            previous_video_url: 前一个视频URL
            websocket: WebSocket连接
            review_mode: 审核模式 ("auto" 或 "manual")
            progress_base: 进度基数
            num_scenes: 总分镜数
            max_retries: 最大重试次数
            pass_threshold: 通过阈值

        Returns:
            (video, is_approved, feedback, score)
        """
        scene_state = self._get_scene_state(project, scene_number)
        current_video = None
        project_language = self._project_language(project)
        is_manual_mode = review_mode == "manual"

        while True:
            self._raise_if_project_ended(project)
            # 这里处理的是系统自动发起的分镜生成流程。
            # 无论审核模式是 auto 还是 manual，只要分镜“生成失败/返回重复 seed”，都走自动重生成预算。
            if scene_state.total_generation_count >= max_total_generations:
                if scene_state.best_video is not None:
                    logger.warning(
                        f"[FLOW] Scene {scene_number} total generation limit reached, selecting best-scored video {scene_state.best_score}"
                    )
                    await self._send_agent_output(websocket, "video_review_agent", {
                        "scene_number": scene_number,
                        "approved": False,
                        "score": scene_state.best_score,
                        "retry_count": scene_state.auto_retry_count,
                        "max_retries": max_retries,
                        "feedback": scene_state.best_feedback,
                        "selected_video_url": scene_state.best_video.url,
                        "message": translate(
                            project_language,
                            "message.video.auto_mode_select_best",
                            scene=scene_number,
                            max_retries=max_retries,
                            score=scene_state.best_score,
                            feedback=scene_state.best_feedback
                        )
                    })
                    return scene_state.best_video, False, scene_state.best_feedback, scene_state.best_score
                raise RuntimeError(
                    translate(
                        project_language,
                        "message.video.review_failed_after_max_retry",
                        scene=scene_number,
                        max_retries=max_retries,
                        score=scene_state.last_score,
                        feedback=scene_state.last_feedback or "no reviewed video available"
                    )
                )

            attempt_number = scene_state.total_generation_count + 1
            # 先尝试生成视频。auto_retry_count 只统计系统自动触发的重生成；人工点击“重新生成”不计入。
            generation_status = "regenerating" if scene_state.total_generation_count > 0 else "generating"
            generation_message = (
                translate(project_language, "progress.video.scene_regenerating", scene=scene_number, retry=scene_state.total_generation_count)
                if scene_state.total_generation_count > 0
                else translate(project_language, "progress.video.scene_generating", scene=scene_number)
            )

            await self._update_progress(
                websocket, "video_agent",
                int(progress_base + min(scene_state.auto_retry_count, 3)),
                generation_message
            )
            await self._send_agent_output(websocket, "video_agent", {
                "scene_number": scene_number,
                "status": generation_status,
                "retry_count": scene_state.auto_retry_count,
                "max_retries": max_retries,
                "generation_count": attempt_number,
                "max_generation_count": max_total_generations,
                "total_scenes": num_scenes,
                "message": generation_message
            })

            logger.info(
                f"[FLOW] Generating video for scene {scene_number}/{num_scenes}, "
                f"attempt={attempt_number}/{max_total_generations}, auto_retry_count={scene_state.auto_retry_count}"
            )
            if scene_number == 1 and not previous_video_url:
                logger.info("[FLOW] First scene: using reference image only")
            else:
                logger.info("[FLOW] Extension scene: using previous-scene last-frame image + reference images")

            try:
                scene_state.total_generation_count = attempt_number
                current_video = await asyncio.to_thread(
                    self.video_agent.generate_video_with_previous,
                    scene=scene,
                    scene_index=scene_number - 1,
                    total_scenes=num_scenes,
                    project_id=project.project_id,
                    reference_image=project.reference_image,
                    reference_images=self._select_reference_assets_for_scene(project, scene),
                    previous_video_url=previous_video_url,
                    user_style_info=getattr(project.script, "style", None) if getattr(project, "script", None) else None,
                    user_requirement_text=getattr(project, "combined_input", None),
                    resolution=getattr(project, "video_resolution", None),
                    aspect_ratio=getattr(project, "aspect_ratio", None),
                    previous_scene=project.script.scenes[scene_number - 2] if scene_number > 1 else None,
                )
                current_video = await self.archive_scene_video_async(
                    project,
                    current_video,
                    generation_count=self._next_scene_archive_attempt(scene_state),
                )
                scene_state.last_video = current_video
                scene_state.generation_failure_count = 0
            except Exception as e:
                logger.warning(
                    f"[FLOW] Scene {scene_number} generation failed on auto retry {scene_state.auto_retry_count}/{max_retries}: {str(e)}"
                )
                scene_state.last_feedback = str(e)
                scene_state.generation_failure_count += 1
                scene_state.auto_retry_count += 1
                if scene_state.auto_retry_count > max_retries or scene_state.total_generation_count >= max_total_generations:
                    if scene_state.best_video is not None:
                        logger.warning(
                            f"[FLOW] Scene {scene_number} generation retries exhausted, selecting best-scored reviewed video {scene_state.best_score}"
                        )
                        await self._send_agent_output(websocket, "video_review_agent", {
                            "scene_number": scene_number,
                            "approved": False,
                            "score": scene_state.best_score,
                            "retry_count": scene_state.auto_retry_count,
                            "max_retries": max_retries,
                            "feedback": scene_state.best_feedback,
                            "selected_video_url": scene_state.best_video.url,
                            "message": translate(
                                project_language,
                                "message.video.auto_mode_select_best",
                                scene=scene_number,
                                max_retries=max_retries,
                                score=scene_state.best_score,
                                feedback=scene_state.best_feedback
                            )
                        })
                        return scene_state.best_video, False, scene_state.best_feedback, scene_state.best_score
                    if is_manual_mode:
                        raise RuntimeError(
                            translate(
                                project_language,
                                "message.video.scene_generation_failed_limit_reason",
                                limit=max_total_generations,
                                error=str(e),
                            )
                        ) from e
                    skip_info = await self._notify_scene_skipped(
                        project=project,
                        websocket=websocket,
                        scene_number=scene_number,
                        reason=translate(
                            project_language,
                            "message.video.scene_generation_failed_limit_reason",
                            limit=max_total_generations,
                            error=str(e),
                        ),
                    )
                    project.next_scene_index = min(scene_number - 1, len(project.script.scenes))
                    raise SceneSkippedError(skip_info) from e

                await self._send_agent_output(websocket, "video_agent", {
                    "scene_number": scene_number,
                    "status": "generation_failed",
                    "retry_count": scene_state.auto_retry_count,
                    "max_retries": max_retries,
                    "generation_count": scene_state.total_generation_count,
                    "max_generation_count": max_total_generations,
                    "total_scenes": num_scenes,
                    "message": translate(
                        project_language,
                        "message.video.scene_generation_failed_retry",
                        scene=scene_number,
                        retry=scene_state.auto_retry_count
                    )
                })
                continue

            if self._has_duplicate_video_seed(project, current_video.seed):
                duplicate_seed = self._normalize_video_seed(current_video.seed) or "unknown"
                duplicate_message = translate(
                    project_language,
                    "message.video.duplicate_seed_retry",
                    scene=scene_number,
                    seed=duplicate_seed,
                )
                logger.warning(
                    f"[FLOW] Scene {scene_number} generated duplicate seed {duplicate_seed}, skip review and regenerate"
                )
                scene_state.last_feedback = duplicate_message
                scene_state.generation_failure_count += 1
                scene_state.auto_retry_count += 1

                await self._send_agent_output(websocket, "video_agent", {
                    "scene_number": scene_number,
                    "status": "duplicate_seed",
                    "retry_count": scene_state.auto_retry_count,
                    "max_retries": max_retries,
                    "generation_count": scene_state.total_generation_count,
                    "max_generation_count": max_total_generations,
                    "total_scenes": num_scenes,
                    "seed": duplicate_seed,
                    "message": duplicate_message,
                })

                if scene_state.total_generation_count >= max_total_generations or scene_state.auto_retry_count > max_retries:
                    if scene_state.best_video is not None:
                        logger.warning(
                            f"[FLOW] Scene {scene_number} duplicate seed retries exhausted, selecting best-scored reviewed video {scene_state.best_score}"
                        )
                        await self._send_agent_output(websocket, "video_review_agent", {
                            "scene_number": scene_number,
                            "approved": False,
                            "score": scene_state.best_score,
                            "retry_count": scene_state.auto_retry_count,
                            "max_retries": max_retries,
                            "feedback": scene_state.best_feedback,
                            "selected_video_url": scene_state.best_video.url,
                            "message": translate(
                                project_language,
                                "message.video.auto_mode_select_best",
                                scene=scene_number,
                                max_retries=max_retries,
                                score=scene_state.best_score,
                                feedback=scene_state.best_feedback
                            )
                        })
                        return scene_state.best_video, False, scene_state.best_feedback, scene_state.best_score

                    if is_manual_mode:
                        raise RuntimeError(
                            translate(
                                project_language,
                                "message.video.duplicate_seed_retry_limit",
                                scene=scene_number,
                                limit=max_total_generations,
                                seed=duplicate_seed,
                            )
                        )

                    skip_info = await self._notify_scene_skipped(
                        project=project,
                        websocket=websocket,
                        scene_number=scene_number,
                        reason=translate(
                            project_language,
                            "message.video.duplicate_seed_retry_limit",
                            scene=scene_number,
                            limit=max_total_generations,
                            seed=duplicate_seed,
                        ),
                    )
                    project.next_scene_index = min(scene_number - 1, len(project.script.scenes))
                    raise SceneSkippedError(skip_info)
                continue

            logger.info(f"[FLOW] Scene {scene_number} video generated: {current_video.url[:100]}...")
            logger.info(f"[FLOW] Scene {scene_number} prompt: {current_video.prompt[:200]}...")
            if current_video.seed:
                logger.info(f"[FLOW] Scene {scene_number} seed: {current_video.seed}")
                self._register_video_seed(project, current_video.seed)

            await self._send_agent_output(websocket, "video_agent", {
                "scene_number": scene_number,
                "url": current_video.url,
                "status": "regenerated" if scene_state.total_generation_count > 1 else "generated",
                "retry_count": scene_state.auto_retry_count,
                "max_retries": max_retries,
                "generation_count": scene_state.total_generation_count,
                "max_generation_count": max_total_generations,
                "total_scenes": num_scenes,
                "seed": current_video.seed,
                "message": translate(project_language, "message.video.scene_generated_wait_review", scene=scene_number)
            })

            # 执行审核
            logger.info(f"[REVIEW] Scene {scene_number} - Review attempt {scene_state.total_generation_count}")
            # 通知前端：该分镜正在审核（在分镜视频区域显示转转 + “正在审核...”）
            await self._send_agent_output(websocket, "video_review_agent", {
                "scene_number": scene_number,
                "status": "reviewing",
                "retry_count": scene_state.auto_retry_count,
                "max_retries": max_retries,
                "generation_count": scene_state.total_generation_count,
                "max_generation_count": max_total_generations,
                "total_scenes": num_scenes,
                "message": translate(project_language, "message.video.scene_reviewing", scene=scene_number)
            })
            await self._update_progress(
                websocket, "video_review_agent",
                int(progress_base + 5),
                translate(
                    project_language,
                    "progress.video.reviewing",
                    scene=scene_number,
                    total=num_scenes,
                    attempt=scene_state.total_generation_count
                )
            )

            is_approved, feedback, score = await asyncio.to_thread(
                self.video_review_agent.review_video,
                script_scene_description=scene.description,
                video_url=current_video.url,
                previous_video_url=previous_video_url,
                reference_image_url=project.reference_image.url,
                output_language=project_language,
            )

            scene_state.last_score = score
            scene_state.last_feedback = feedback

            if score > scene_state.best_score:
                scene_state.best_video = current_video
                scene_state.best_feedback = feedback
                scene_state.best_score = score

            logger.info(f"[REVIEW] Scene {scene_number} - Score: {score}/{pass_threshold}, Approved: {is_approved}")

            manual_next_step = "merge" if scene_number == num_scenes else "videos"
            if is_approved:
                scene_state.approved = True
                logger.info(f"[REVIEW] Scene {scene_number} - PASSED on attempt {scene_state.total_generation_count}")
                review_output = {
                    "scene_number": scene_number,
                    "approved": True,
                    "score": score,
                    "retry_count": scene_state.auto_retry_count,
                    "generation_count": scene_state.total_generation_count,
                    "max_retries": max_retries,
                    "max_generation_count": max_total_generations,
                    "message": translate(project_language, "message.video.review_passed", scene=scene_number, score=score)
                }
                if is_manual_mode:
                    review_output.update({
                        "manual_continue_allowed": True,
                        "review_mode": "manual",
                        "feedback": feedback,
                        "next_step": manual_next_step,
                        "is_last_scene": scene_number == num_scenes,
                    })
                await self._send_agent_output(websocket, "video_review_agent", review_output)
                return current_video, True, feedback, score

            # 审核未通过
            scene_state.approved = False
            logger.warning(f"[REVIEW] Scene {scene_number} - FAILED attempt {scene_state.auto_retry_count}/{max_retries}: {feedback}")

            await self._send_agent_output(websocket, "video_review_agent", {
                "scene_number": scene_number,
                "approved": False,
                "score": score,
                "retry_count": scene_state.auto_retry_count + (0 if is_manual_mode else 1),
                "max_retries": max_retries,
                "generation_count": scene_state.total_generation_count,
                "max_generation_count": max_total_generations,
                "feedback": feedback,
                "message": translate(
                    project_language,
                    "message.video.review_failed",
                    scene=scene_number,
                    score=score,
                    feedback=feedback
                )
            })

            if is_manual_mode:
                logger.info(f"[REVIEW] Scene {scene_number} - Manual mode: keep current video without auto-regenerate")
                await self._send_agent_output(websocket, "video_review_agent", {
                    "scene_number": scene_number,
                    "approved": False,
                    "score": score,
                    "retry_count": scene_state.auto_retry_count,
                    "generation_count": scene_state.total_generation_count,
                    "max_generation_count": max_total_generations,
                    "manual_continue_allowed": True,
                    "review_mode": "manual",
                    "next_step": manual_next_step,
                    "is_last_scene": scene_number == num_scenes,
                    "feedback": feedback,
                    "message": translate(
                        project_language,
                        "message.video.manual_mode_keep_current",
                        scene=scene_number,
                        score=score,
                        feedback=feedback
                    )
                })
                return current_video, False, feedback, score

            # 自动模式下，审核未通过且已用尽共享重试预算，则选择得分最高的视频继续流程。
            if scene_state.auto_retry_count >= max_retries or scene_state.total_generation_count >= max_total_generations:
                if scene_state.best_video is not None:
                    logger.warning(
                        f"[REVIEW] Scene {scene_number} - Max retries exhausted, selecting best-scored video {scene_state.best_score}"
                    )
                    await self._send_agent_output(websocket, "video_review_agent", {
                        "scene_number": scene_number,
                        "approved": False,
                        "score": scene_state.best_score,
                        "retry_count": scene_state.auto_retry_count,
                        "max_retries": max_retries,
                        "generation_count": scene_state.total_generation_count,
                        "max_generation_count": max_total_generations,
                        "feedback": scene_state.best_feedback,
                        "selected_video_url": scene_state.best_video.url,
                        "message": translate(
                            project_language,
                            "message.video.auto_mode_select_best",
                            scene=scene_number,
                            max_retries=max_retries,
                            score=scene_state.best_score,
                            feedback=scene_state.best_feedback
                        )
                    })
                    return scene_state.best_video, False, scene_state.best_feedback, scene_state.best_score

                logger.error(
                    f"[REVIEW] Scene {scene_number} - Max retries exhausted and no reviewed video is available"
                )
                raise RuntimeError(
                    translate(
                        project_language,
                        "message.video.review_failed_after_max_retry",
                        scene=scene_number,
                        max_retries=max_retries,
                        score=score,
                        feedback=feedback
                    )
                )

            scene_state.auto_retry_count += 1
            logger.info(f"[REVIEW] Scene {scene_number} - Retrying generation ({scene_state.auto_retry_count}/{max_retries})")

    async def _generate_script(self, project: VideoProject) -> Script:
        """调用ScriptAgent生成剧本"""
        logger.info(f"Generating script for project {project.project_id}")

        audio_text = None
        if project.audio_url:
            try:
                audio_text = asr_service.recognize(project.audio_url)
            except Exception as e:
                logger.error(f"ASR failed: {str(e)}")
        
        # 使用合并后的输入（包含风格信息）
        # 优先使用 combined_input，如果没有则使用 user_input
        if project.combined_input:
            user_input = project.combined_input
            logger.info(f"Using combined_input for script generation ({len(user_input)} chars)")
            logger.info(f"combined_input content: {user_input}")
        else:
            user_input = project.user_input
            logger.info(f"Using user_input for script generation (no combined_input found)")
        
        logger.info(f"Script generation input preview: {user_input[:500]}...")

        return await asyncio.to_thread(
            self.script_agent.generate_script,
            user_input=user_input,
            reference_images=project.reference_images,
            uploaded_reference_images=project.uploaded_reference_images,
            audio_text=audio_text,
            output_language=self._project_language(project)
        )

    async def rewrite_script(self, project_id: str, edit_request: str) -> Script:
        """在手动模式下，根据上一版完整剧本和用户修改要求重写剧本。"""
        project = self.projects.get(project_id)
        if not project:
            raise ValueError(f"Project not found: {project_id}")
        if not self.can_rewrite_script(project):
            raise ValueError("Script rewriting is only allowed in manual mode before reference image generation")

        audio_text = None
        if project.audio_url:
            try:
                audio_text = asr_service.recognize(project.audio_url)
            except Exception as e:
                logger.error(f"ASR failed during script rewrite: {str(e)}")

        existing_input = getattr(project, 'combined_input', '') or f"原始需求：{project.user_input}"
        project.combined_input = f"""{existing_input}

剧本修改要求：{edit_request}"""

        script = await asyncio.to_thread(
            self.script_agent.rewrite_script,
            existing_script=project.script,
            edit_request=edit_request,
            reference_images=project.reference_images,
            uploaded_reference_images=project.uploaded_reference_images,
            audio_text=audio_text,
            output_language=self._project_language(project)
        )

        project.script = script
        project.current_step = "script_generated"
        project.status = "script_updated"
        project.progress = max(project.progress, 25)
        project.reference_image = None
        project.images = []
        project.character_reference_images = []
        project.scene_reference_images = []
        project.reference_image_library = {}
        project.videos = []
        project.final_video_url = None
        project.next_scene_index = 0
        project.video_scene_states = {}
        project.generated_video_seeds = []
        return script

    async def _generate_reference_image(
        self,
        project: VideoProject,
        progress_callback: Optional[Callable[[VideoProject], Awaitable[None]]] = None,
    ) -> GeneratedImage:
        """生成角色参考图与场景参考图，并保留兼容字段。"""
        self._raise_if_project_ended(project)
        logger.info(f"Step 1: Generating reference image library for project {project.project_id}")
        user_style_info = getattr(project, "combined_input", None)
        aspect_ratio = getattr(project, "aspect_ratio", None)
        uploaded_assets = list(getattr(project, "uploaded_reference_images", []) or [])
        character_assets = [asset for asset in uploaded_assets if asset.reference_type == "character"]
        scene_assets = [asset for asset in uploaded_assets if asset.reference_type == "scene"]

        character_limit = max(1, int(config.get("video_generation.reference_images.character_max_count", 20)))
        scene_limit = max(1, int(config.get("video_generation.reference_images.scene_max_count", 40)))
        reference_max_concurrency = max(1, int(config.get("video_generation.reference_images.max_concurrency", 5)))
        if not config.get("generation.concurrency.enabled", True):
            reference_max_concurrency = 1
        target_characters = list((getattr(project.script, "characters", None) or [])[:character_limit])
        scene_definitions = self._extract_scene_reference_definitions(project.script, scene_limit)
        character_asset_map = self._resolve_uploaded_assets_for_targets(
            character_assets,
            [character.name for character in target_characters],
        )
        scene_asset_map = self._resolve_uploaded_assets_for_targets(
            scene_assets,
            [item["name"] for item in scene_definitions],
        )

        logger.info(
            f"Reference library target for project {project.project_id}: "
            f"{len(target_characters)} character refs, {len(scene_definitions)} scene refs, "
            f"max_concurrency={reference_max_concurrency}"
        )

        session_slots = self._start_reference_generation_session(
            project,
            character_count=len(target_characters),
            scene_count=len(scene_definitions),
        )
        generated_character_images = session_slots["characters"]
        generated_scene_images = session_slots["scenes"]
        semaphore = asyncio.Semaphore(reference_max_concurrency)
        progress_lock = asyncio.Lock()

        async def finalize_generated_image(
            category: str,
            index: int,
            stored_image: GeneratedImage,
        ) -> None:
            self._raise_if_project_ended(project)
            async with progress_lock:
                if category == "character":
                    generated_character_images[index] = stored_image
                else:
                    generated_scene_images[index] = stored_image
                self._sync_reference_library_state(project, generated_character_images, generated_scene_images)
                if progress_callback:
                    await progress_callback(project)

        async def generate_character_job(index: int, character) -> None:
            self._raise_if_project_ended(project)
            matched_assets = character_asset_map.get(character.name, [])
            matched_urls = [asset.url for asset in matched_assets]
            async with semaphore:
                if project.use_original_reference and matched_assets:
                    generated = GeneratedImage(
                        scene_number=0,
                        url=matched_assets[0].url,
                        prompt="使用用户上传原图作为角色参考图",
                        is_reference=True,
                        name=character.name,
                        reference_type="character",
                        asset_id=matched_assets[0].asset_id,
                        asset_status=matched_assets[0].asset_status,
                    )
                    stored = await self._store_reference_asset_async(
                        project, generated, "character", character.name, index, used_original=True
                    )
                else:
                    generated = await asyncio.to_thread(
                        self.image_agent.generate_character_reference_image,
                        character=character,
                        script=project.script,
                        user_style_info=user_style_info,
                        aspect_ratio=aspect_ratio,
                        user_reference_images=matched_urls or None,
                    )
                    stored = await self._store_reference_asset_async(project, generated, "character", character.name, index)
            await finalize_generated_image("character", index, stored)

        async def generate_scene_job(index: int, scene_definition: Dict[str, str]) -> None:
            self._raise_if_project_ended(project)
            scene_name = scene_definition["name"]
            matched_assets = scene_asset_map.get(scene_name, [])
            matched_urls = [asset.url for asset in matched_assets]
            async with semaphore:
                if project.use_original_reference and matched_assets:
                    generated = GeneratedImage(
                        scene_number=0,
                        url=matched_assets[0].url,
                        prompt="使用用户上传原图作为场景参考图",
                        is_reference=True,
                        name=scene_name,
                        reference_type="scene",
                    )
                    stored = await self._store_reference_asset_async(
                        project, generated, "scene", scene_name, index, used_original=True
                    )
                else:
                    generated = await asyncio.to_thread(
                        self.image_agent.generate_scene_reference_image,
                        scene_name=scene_name,
                        scene_description=scene_definition["description"],
                        script=project.script,
                        user_style_info=user_style_info,
                        aspect_ratio=aspect_ratio,
                        user_reference_images=matched_urls or None,
                    )
                    stored = await self._store_reference_asset_async(project, generated, "scene", scene_name, index)
            await finalize_generated_image("scene", index, stored)

        tasks = [
            asyncio.create_task(generate_character_job(index, character))
            for index, character in enumerate(target_characters)
        ] + [
            asyncio.create_task(generate_scene_job(index, scene_definition))
            for index, scene_definition in enumerate(scene_definitions)
        ]

        try:
            if tasks:
                await asyncio.gather(*tasks)

            self._sync_reference_library_state(project, generated_character_images, generated_scene_images)

            if not project.reference_image:
                raise RuntimeError("No reference images were generated")

            return project.reference_image
        finally:
            self._finish_reference_generation_session(project)

    def _reset_reference_library_state(self, project: VideoProject) -> None:
        project.character_reference_images = []
        project.scene_reference_images = []
        project.reference_image_library = {}
        project.images = []
        project.reference_image = None

    async def generate_reference_image_with_retry(
        self,
        project: VideoProject,
        progress_callback: Optional[Callable[[VideoProject], Awaitable[None]]] = None,
    ) -> GeneratedImage:
        self._raise_if_project_ended(project)
        active_task = self._reference_generation_tasks.get(project.project_id)
        if active_task and not active_task.done():
            return await asyncio.shield(active_task)

        async def run_generation() -> GeneratedImage:
            max_auto_retries = max(0, int(config.get("video_generation.reference_images.auto_retry_count", 2)))
            last_error: Optional[Exception] = None

            for attempt in range(max_auto_retries + 1):
                self._raise_if_project_ended(project)
                self._reset_reference_library_state(project)
                try:
                    if attempt > 0:
                        logger.warning(
                            f"Reference library generation retry {attempt}/{max_auto_retries} for project {project.project_id}"
                        )
                    return await self._generate_reference_image(project, progress_callback=progress_callback)
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        f"Reference library generation failed on attempt {attempt + 1}/{max_auto_retries + 1} "
                        f"for project {project.project_id}: {str(exc)}"
                    )
                    if attempt >= max_auto_retries:
                        break

            raise RuntimeError(
                self._t(
                    project,
                    "error.reference_generation_failed_after_retries",
                    retries=max_auto_retries,
                    error=str(last_error) if last_error else "unknown error",
                )
            ) from last_error

        generation_task = asyncio.create_task(
            run_generation(),
            name=f"reference-generation:{project.project_id}",
        )
        self._reference_generation_tasks[project.project_id] = generation_task
        try:
            return await asyncio.shield(generation_task)
        finally:
            if self._reference_generation_tasks.get(project.project_id) is generation_task:
                self._reference_generation_tasks.pop(project.project_id, None)

    async def regenerate_reference_asset(
        self,
        project: VideoProject,
        reference_type: str,
        asset_name: str,
        reference_slot_index: Optional[int] = None,
        feedback: str = "用户要求重新生成",
    ) -> GeneratedImage:
        self._raise_if_project_ended(project)
        reference_type = str(reference_type or "").strip().lower()
        normalized_name = str(asset_name or "").strip()
        if reference_type not in {"character", "scene"}:
            raise ValueError(self._t(project, "error.unsupported_reference_type", reference_type=reference_type or "unknown"))
        if not normalized_name:
            raise ValueError(self._t(project, "error.reference_asset_name_required"))
        task_key = self._build_reference_asset_task_key(project.project_id, reference_type, normalized_name)
        active_task = self._reference_asset_regeneration_tasks.get(task_key)
        if active_task and not active_task.done():
            return await asyncio.shield(active_task)

        async def run_regeneration() -> GeneratedImage:
            self._raise_if_project_ended(project)

            session_slots = self._get_reference_generation_session(project)
            image_list = (
                project.character_reference_images
                if reference_type == "character"
                else project.scene_reference_images
            )
            slot_index = (
                int(reference_slot_index)
                if reference_slot_index is not None and int(reference_slot_index) >= 0
                else self._get_reference_slot_index(project, reference_type, normalized_name)
            )

            target_image: Optional[GeneratedImage] = None
            if session_slots and slot_index >= 0:
                active_images = session_slots["characters"] if reference_type == "character" else session_slots["scenes"]
                if slot_index < len(active_images):
                    target_image = active_images[slot_index]

            image_index = next(
                (
                    index for index, image in enumerate(image_list)
                    if self._normalize_name_key(getattr(image, "name", "")) == self._normalize_name_key(normalized_name)
                ),
                -1,
            )
            if target_image is None and image_index >= 0:
                target_image = image_list[image_index]

            if target_image is None:
                raise ValueError(
                    self._t(
                        project,
                        "error.reference_asset_not_found",
                        reference_type=reference_type,
                        name=normalized_name,
                    )
                )
            if getattr(target_image, "regenerate_locked", False):
                raise ValueError(self._t(project, "error.reference_regeneration_locked_original"))

            user_style_info = getattr(project, "combined_input", None)
            aspect_ratio = getattr(project, "aspect_ratio", None)
            uploaded_assets = [
                asset for asset in (getattr(project, "uploaded_reference_images", []) or [])
                if asset.reference_type == reference_type
            ]
            assigned_assets = self._resolve_uploaded_assets_for_targets(uploaded_assets, [normalized_name]).get(normalized_name, [])
            assigned_urls = [asset.url for asset in assigned_assets]

            if reference_type == "character":
                target_character = next(
                    (
                        character for character in (getattr(project.script, "characters", None) or [])
                        if self._normalize_name_key(character.name) == self._normalize_name_key(normalized_name)
                    ),
                    None,
                )
                if not target_character:
                    raise ValueError(self._t(project, "error.reference_character_not_found", name=normalized_name))
                generated = await asyncio.to_thread(
                    self.image_agent.regenerate_character_reference_image,
                    character=target_character,
                    script=project.script,
                    feedback=feedback,
                    user_style_info=user_style_info,
                    aspect_ratio=aspect_ratio,
                    user_reference_images=assigned_urls or None,
                )
            else:
                target_scene = next(
                    (
                        item for item in self._extract_scene_reference_definitions(
                            project.script,
                            max(1, int(config.get("video_generation.reference_images.scene_max_count", 40))),
                        )
                        if self._normalize_name_key(item["name"]) == self._normalize_name_key(normalized_name)
                    ),
                    None,
                )
                if not target_scene:
                    raise ValueError(self._t(project, "error.reference_scene_not_found", name=normalized_name))
                generated = await asyncio.to_thread(
                    self.image_agent.regenerate_scene_reference_image,
                    scene_name=target_scene["name"],
                    scene_description=target_scene["description"],
                    script=project.script,
                    feedback=feedback,
                    user_style_info=user_style_info,
                    aspect_ratio=aspect_ratio,
                    user_reference_images=assigned_urls or None,
                )

            storage_index = slot_index if slot_index >= 0 else image_index

            stored = await self._store_reference_asset_async(
                project,
                generated,
                reference_type,
                normalized_name,
                storage_index,
                used_original=False,
            )
            if session_slots and slot_index >= 0:
                if reference_type == "character":
                    if slot_index < len(session_slots["characters"]):
                        session_slots["characters"][slot_index] = stored
                else:
                    if slot_index < len(session_slots["scenes"]):
                        session_slots["scenes"][slot_index] = stored
                self._sync_reference_library_state(project, session_slots["characters"], session_slots["scenes"])
            else:
                image_list[image_index] = stored
                if reference_type == "character":
                    project.reference_image_library["characters"] = project.character_reference_images
                else:
                    project.reference_image_library["scenes"] = project.scene_reference_images
                project.images = list(getattr(project, "character_reference_images", []) or []) + list(getattr(project, "scene_reference_images", []) or [])
                project.reference_image = (
                    project.character_reference_images[0]
                    if project.character_reference_images
                    else (project.scene_reference_images[0] if project.scene_reference_images else None)
                )
            return stored

        regeneration_task = asyncio.create_task(
            run_regeneration(),
            name=f"reference-regeneration:{task_key}",
        )
        self._reference_asset_regeneration_tasks[task_key] = regeneration_task
        try:
            return await asyncio.shield(regeneration_task)
        finally:
            if self._reference_asset_regeneration_tasks.get(task_key) is regeneration_task:
                self._reference_asset_regeneration_tasks.pop(task_key, None)

    async def _regenerate_reference_image(
        self,
        project: VideoProject,
        feedback: str = "用户要求重新生成",
    ) -> GeneratedImage:
        """重新生成整套参考图库，并返回兼容链路使用的主参考图。"""
        self._raise_if_project_ended(project)
        logger.info(f"Regenerating reference image (参考图库) for project {project.project_id}")

        if getattr(project, "use_original_reference", False) and getattr(project, "reference_images", None):
            raise ValueError(self._t(project, "error.reference_regeneration_locked_original"))
        _ = feedback
        return await self.generate_reference_image_with_retry(project)

    async def _generate_videos(self, project: VideoProject) -> List[GeneratedVideo]:
        """调用VideoAgent生成视频 - 简化版本，直接使用参考图"""
        logger.info(f"Generating videos for project {project.project_id}")

        # 已有分镜脚本时，视频提示词只使用 script.style（简短），避免塞入初始用户长文本
        user_style_info = getattr(getattr(project, "script", None), "style", None)
        if user_style_info:
            logger.info(f"Using script.style for video generation ({len(user_style_info)} chars)")
        else:
            logger.warning("No script.style found for video generation!")

        # 获取参考图库
        reference_image = getattr(project, 'reference_image', None)
        if not reference_image:
            raise ValueError(f"Reference image (参考图库) not found for project {project.project_id}")

        logger.info(f"Using reference image for video generation: {reference_image.url}")

        # 简化流程：直接使用参考图生成分镜视频
        return self.video_agent.generate_videos(
            script=project.script,
            reference_image=reference_image,
            user_style_info=user_style_info
        )

    async def _regenerate_video(self, project: VideoProject, scene_number: int, feedback: str):
        """重新生成视频 - 简化版本"""
        logger.info(f"Regenerating video for scene {scene_number}")

        # 已有分镜脚本时，视频提示词只使用 script.style（简短）
        user_style_info = getattr(getattr(project, "script", None), "style", None)

        # 获取参考图
        reference_image = getattr(project, 'reference_image', None)
        if not reference_image:
            raise ValueError(f"Reference image not found for project {project.project_id}")

        previous_video_url = self._get_previous_video_url(project, scene_number - 1)

        new_video = self.video_agent.regenerate_video(
            scene_number=scene_number,
            script=project.script,
            project_id=project.project_id,
            reference_image=reference_image,
            reference_images=self._select_reference_assets_for_scene(project, project.script.scenes[scene_number - 1]),
            previous_video_url=previous_video_url,
            feedback=feedback,
            user_style_info=user_style_info,
            user_requirement_text=getattr(project, "combined_input", None),
            resolution=getattr(project, "video_resolution", None),
            aspect_ratio=getattr(project, "aspect_ratio", None),
        )
        # 更新项目中的视频
        for i, vid in enumerate(project.videos):
            if vid.scene_number == scene_number:
                project.videos[i] = new_video
                break

    async def _merge_videos(self, project: VideoProject) -> str:
        """调用MergeAgent合成视频"""
        logger.info(f"Merging videos for project {project.project_id}")

        final_url = self.merge_agent.merge_videos(
            script=project.script,
            videos=project.videos,
            project_id=project.project_id
        )

        # 保存项目元数据
        self.merge_agent.save_project_metadata(
            project_id=project.project_id,
            script=project.script,
            videos=project.videos,
            final_video_url=final_url
        )

        return final_url

    async def _update_progress(self, websocket, agent: str, progress: int, message: str):
        """更新进度"""
        logger.log_progress(agent, progress)
        if websocket:
            try:
                await websocket.send_json(jsonable_encoder({
                    "type": "progress",
                    "data": {
                        "agent": agent,
                        "progress": progress,
                        "message": message
                    }
                }))
            except Exception as e:
                logger.error(f"WebSocket send error: {str(e)}")

    async def _send_agent_output(self, websocket, agent: str, data: dict):
        """发送Agent输出"""
        if websocket:
            try:
                await websocket.send_json(jsonable_encoder({
                    "type": "agent_output",
                    "data": {
                        "agent": agent,
                        "output": data
                    }
                }))
            except Exception as e:
                logger.error(f"WebSocket send error: {str(e)}")

    def get_project(self, project_id: str) -> Optional[VideoProject]:
        """获取项目信息"""
        return self.projects.get(project_id)

    def chat_with_user(self, message: str, project_id: str = None, output_language: Optional[str] = None) -> str:
        """处理已有项目的补充需求，不再执行旧的风格收集/预处理 LLM 对话。"""
        logger.log_agent_call("MainAgent", "chat_with_user", {"project_id": project_id})

        is_existing_project = project_id and project_id in self.projects
        project = self.projects.get(project_id) if is_existing_project else None
        if project and output_language:
            project.output_language = normalize_locale(output_language)
        if not is_existing_project or not project:
            return translate(output_language or "zh-CN", "message.direct_start_script")

        is_control_command = is_control_command_text(message)
        if not is_control_command:
            return self._append_user_input(message, project)

        logger.info(f"Control command received: {message}, combined_input has {len(project.combined_input or '')} chars")
        return ""

    def _append_user_input(self, message: str, project) -> str:
        """追加用户的进一步需求到 combined_input"""
        logger.info(f"Appending user input for project {project.project_id}")

        # 获取现有的 combined_input
        existing_input = getattr(project, 'combined_input', '') or f"原始需求：{project.user_input}"

        # 追加新的输入
        updated_input = f"""{existing_input}

补充需求：{message}"""

        project.combined_input = updated_input

        logger.info(f"Updated combined input: {updated_input[:200]}...")

        return self._t(
            project,
            "chat.append_input",
            details=f"{updated_input[:200]}{'...' if len(updated_input) > 200 else ''}"
        )


# 全局主Agent实例
main_agent = MainAgent()
