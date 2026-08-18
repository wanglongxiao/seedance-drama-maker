# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.
import uuid
import asyncio
import re
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Any, Callable, Awaitable
from fastapi.encoders import jsonable_encoder
from app.config import config
from app.prompt_skill import nsfw_content_requested, private_nsfw_enabled
from app.services.asr_service import asr_service
from app.utils.i18n import normalize_locale, translate
from app.utils.logger import get_logger
from app.utils.task_paths import ensure_project_temp_dir
from app.utils.thread_pools import run_generation, run_interactive
from app.models.schemas import VideoProject, Script, GeneratedImage, GeneratedVideo, VideoSceneState, UploadedReferenceImage
from app.agents.script_agent import ScriptAgent
from app.agents.image_agent import ImageAgent
from app.agents.video_agent import VideoAgent
from app.agents.video_review_agent import VideoReviewAgent
from app.agents.storyboard_review_agent import StoryboardReviewAgent
from app.agents.merge_agent import MergeAgent
from app.services.asset_library_service import asset_library_service, AssetLibraryError
from app.services.comic_pdf_service import comic_pdf_service
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
        self.storyboard_review_agent = StoryboardReviewAgent()
        self.merge_agent = MergeAgent()
        self.projects: Dict[str, VideoProject] = {}
        self._reference_generation_slots: Dict[str, Dict[str, List[Optional[GeneratedImage]]]] = {}
        self._reference_asset_cache: Dict[str, Dict[str, str]] = {}
        self._reference_generation_tasks: Dict[str, asyncio.Task] = {}
        self._reference_asset_regeneration_tasks: Dict[str, asyncio.Task] = {}
        self._project_prepare_tasks: Dict[str, asyncio.Task] = {}
        self._comic_pdf_tasks: Dict[str, asyncio.Task] = {}

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

        for task_key, generation_task in list(self._reference_generation_tasks.items()):
            if task_key != project_id and not task_key.startswith(f"{project_id}:"):
                continue
            self._reference_generation_tasks.pop(task_key, None)
            if generation_task and not generation_task.done():
                generation_task.cancel()

        comic_task = self._comic_pdf_tasks.pop(project_id, None)
        if comic_task and not comic_task.done():
            comic_task.cancel()

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
        for task_key in list(self._reference_generation_tasks.keys()):
            if task_key == project_id or task_key.startswith(f"{project_id}:"):
                self._reference_generation_tasks.pop(task_key, None)
        self._project_prepare_tasks.pop(project_id, None)
        self._comic_pdf_tasks.pop(project_id, None)
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

    def _register_uploaded_reference_assets(self, project: VideoProject) -> None:
        self._ensure_project_asset_group(project)
        for asset in getattr(project, "uploaded_reference_images", []) or []:
            if getattr(asset, "asset_id", None):
                continue
            asset_info = asset_library_service.register_image_asset(
                group_id=project.asset_group_id,
                url=asset.url,
                name=asset.name or f"{getattr(asset, 'reference_type', 'reference')}-{project.project_id}",
                project_name=project.asset_project_name,
            )
            asset.asset_id = str(asset_info.get("Id") or "").strip() or None
            asset.asset_status = str(asset_info.get("Status") or "").strip() or None

    def _find_uploaded_reference_asset(
        self,
        project: VideoProject,
        asset_name: str,
        source_url: str,
        reference_type: Optional[str] = None,
    ) -> Optional[UploadedReferenceImage]:
        normalized_name = self._normalize_name_key(asset_name)
        for item in getattr(project, "uploaded_reference_images", []) or []:
            if reference_type and getattr(item, "reference_type", None) != reference_type:
                continue
            if source_url and str(getattr(item, "url", "") or "").strip() == source_url:
                return item
            if normalized_name and self._normalize_name_key(getattr(item, "name", "")) == normalized_name:
                return item
        return None

    def _register_generated_reference_asset(
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

    async def _recognize_audio_interactive(self, audio_url: Optional[str], context: str = "ASR") -> Optional[str]:
        """Run blocking ASR polling outside the event loop and generation pool."""
        if not audio_url:
            return None
        try:
            audio_text = await run_interactive(asr_service.recognize, audio_url)
            logger.info(f"{context} recognized: {audio_text[:100]}...")
            return audio_text
        except Exception as e:
            logger.error(f"{context} failed: {str(e)}")
            return None

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
            audio_text = await self._recognize_audio_interactive(
                audio_url,
                context=f"Audio for project {project_id}",
            )

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

        # 素材组创建与上传参考图登记涉及外部网络调用，较慢。
        # 云端若在 /chat 的 HTTP 请求内同步等待，叠加冷启动极易撞 API 网关超时，
        # 前端表现为“发送失败”。这里改为后台任务执行，create_project 立即返回；
        # 真正需要 asset_id 的步骤（剧本/参考图生成）会先 await 该准备任务。
        async def _prepare_project_assets() -> None:
            await run_generation(self._ensure_project_asset_group, project)
            await run_generation(self._register_uploaded_reference_assets, project)
            self.save_project_state(project_id)

        prep_task = asyncio.create_task(
            _prepare_project_assets(),
            name=f"project-prepare:{project_id}",
        )
        self._project_prepare_tasks[project_id] = prep_task

        logger.info(f"Project created with combined_input: {combined_input[:200]}...")

        # 持久化初始状态，云端多实例场景下可被其它实例回源恢复。
        self.save_project_state(project_id)

        return project

    async def ensure_project_prepared(self, project_id: str) -> None:
        """等待项目的素材组/上传参考图后台准备任务完成（幂等）。"""
        task = self._project_prepare_tasks.get(project_id)
        if task is None:
            return
        try:
            await asyncio.shield(task)
        finally:
            if self._project_prepare_tasks.get(project_id) is task:
                self._project_prepare_tasks.pop(project_id, None)

    def _normalize_uploaded_reference_images(
        self,
        uploaded_reference_images: Optional[List[Dict[str, Any]]] = None,
        reference_images: Optional[List[str]] = None,
        output_language: Optional[str] = None,
    ) -> List[UploadedReferenceImage]:
        normalized: List[UploadedReferenceImage] = []
        locale = normalize_locale(output_language)
        upload_total_limit = max(1, int(config.get("video_generation.reference_images.upload_max_count", 40)))
        upload_character_limit = max(1, int(config.get("video_generation.reference_images.upload_character_max_count", 20)))
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

    def _extract_scene_reference_definitions(self, script: Script, limit: int) -> List[Dict[str, Any]]:
        definitions: List[Dict[str, Any]] = []
        seen = set()

        for index, item in enumerate(getattr(script, "scene_definitions", []) or [], start=1):
            name = str(getattr(item, "name", "") or "").strip()[:24] or f"Scene {index}"
            description = str(getattr(item, "description", "") or "").strip() or name
            time_of_day = str(getattr(item, "time_of_day", "") or "").strip()
            weather = str(getattr(item, "weather", "") or "").strip()
            scene_features = [
                str(feature or "").strip()
                for feature in (getattr(item, "scene_features", None) or [])
                if str(feature or "").strip()
            ]
            key = self._normalize_name_key(name)
            if not key or key in seen:
                continue
            seen.add(key)
            definitions.append({
                "name": name,
                "description": description,
                "time_of_day": time_of_day,
                "weather": weather,
                "scene_features": scene_features,
            })
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

        uploaded_asset = self._find_uploaded_reference_asset(
            project,
            asset_name,
            source_url,
            reference_type=category if category in {"character", "scene"} else None,
        )
        if uploaded_asset and getattr(uploaded_asset, "asset_id", None):
            image.asset_id = uploaded_asset.asset_id
            image.asset_status = uploaded_asset.asset_status or "Active"
        elif getattr(image, "asset_id", None):
            image.asset_status = image.asset_status or "Active"
        else:
            self._register_generated_reference_asset(project, image, asset_name)
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
        return await run_generation(
            self._store_reference_asset,
            project,
            image,
            category,
            asset_name,
            index,
            used_original,
        )

    def _expected_reference_counts(self, project: VideoProject) -> Dict[str, int]:
        character_limit = max(1, int(config.get("video_generation.reference_images.character_max_count", 30)))
        scene_limit = max(1, int(config.get("video_generation.reference_images.scene_max_count", 30)))
        character_count = len(list((getattr(getattr(project, "script", None), "characters", None) or [])[:character_limit]))
        scene_count = len(self._extract_scene_reference_definitions(getattr(project, "script", None), scene_limit))
        scene_story_count = len(getattr(getattr(project, "script", None), "scenes", None) or [])
        variant_plan = self._plan_scene_variant_assets(project) if getattr(project, "script", None) else {"outfits": [], "scene_states": [], "key_actions": []}
        outfit_count = len(variant_plan.get("outfits", []))
        scene_state_count = len(variant_plan.get("scene_states", []))
        key_action_count = len(variant_plan.get("key_actions", []))
        return {
            "characters": character_count,
            "scenes": scene_count,
            "character_outfits": outfit_count,
            "scene_states": scene_state_count,
            "key_actions": key_action_count,
            "storyboards": scene_story_count,
            "total": character_count + scene_count + outfit_count + scene_state_count + key_action_count + scene_story_count,
        }

    def _is_reference_library_ready_for_confirmation(self, project: VideoProject) -> bool:
        session_slots = self._get_reference_generation_session(project)
        if session_slots is not None:
            return all(
                image is not None
                for key in ("characters", "scenes", "character_outfits", "scene_states", "key_actions", "storyboards")
                for image in session_slots.get(key, [])
            )

        expected = self._expected_reference_counts(project)
        actual_character_count = len(getattr(project, "character_reference_images", []) or [])
        actual_scene_count = len(getattr(project, "scene_reference_images", []) or [])
        actual_outfit_count = len(getattr(project, "character_outfit_images", []) or [])
        actual_scene_state_count = len(getattr(project, "scene_state_images", []) or [])
        actual_key_action_count = len(getattr(project, "key_action_reference_images", []) or [])
        actual_storyboard_count = len(getattr(project, "storyboard_images", []) or [])
        return (
            actual_character_count >= expected["characters"]
            and actual_scene_count >= expected["scenes"]
            and actual_outfit_count >= expected["character_outfits"]
            and actual_scene_state_count >= expected["scene_states"]
            and actual_key_action_count >= expected["key_actions"]
            and actual_storyboard_count >= expected["storyboards"]
        )

    def _reference_stage_has_category2(self, project: VideoProject) -> bool:
        """判断分类2（角色装扮图/布景状态图/关键动作参考图）是否存在。"""
        expected = self._expected_reference_counts(project)
        return (expected["character_outfits"] + expected["scene_states"] + expected["key_actions"]) > 0

    def _is_reference_stage_ready(self, project: VideoProject, stage: str) -> bool:
        """按子阶段判定是否就绪：category1=角色+场景；category2=装扮+状态；category3=故事版。"""
        if stage == "category1":
            character_limit = max(1, int(config.get("video_generation.reference_images.character_max_count", 30)))
            scene_limit = max(1, int(config.get("video_generation.reference_images.scene_max_count", 30)))
            expected_character_names = {
                self._normalize_name_key(getattr(character, "name", ""))
                for character in (getattr(project.script, "characters", None) or [])[:character_limit]
            }
            expected_scene_names = {
                self._normalize_name_key(item["name"])
                for item in self._extract_scene_reference_definitions(project.script, scene_limit)
            }
            actual_character_names = {
                self._normalize_name_key(getattr(image, "name", ""))
                for image in (getattr(project, "character_reference_images", []) or [])
            }
            actual_scene_names = {
                self._normalize_name_key(getattr(image, "name", ""))
                for image in (getattr(project, "scene_reference_images", []) or [])
            }
            return (
                expected_character_names.issubset(actual_character_names)
                and expected_scene_names.issubset(actual_scene_names)
            )
        if stage == "category2":
            variant_plan = self._plan_scene_variant_assets(project)
            expected_outfit_keys = {
                str(task.get("dedup_key") or "")
                for task in (variant_plan.get("outfits", []) or [])
            }
            expected_scene_state_keys = {
                str(task.get("dedup_key") or "")
                for task in (variant_plan.get("scene_states", []) or [])
            }
            expected_key_action_keys = {
                str(task.get("dedup_key") or "")
                for task in (variant_plan.get("key_actions", []) or [])
            }
            actual_outfit_keys = {
                str(getattr(image, "variant_key", "") or "")
                for image in (getattr(project, "character_outfit_images", []) or [])
            }
            actual_scene_state_keys = {
                str(getattr(image, "variant_key", "") or "")
                for image in (getattr(project, "scene_state_images", []) or [])
            }
            actual_key_action_keys = {
                str(getattr(image, "variant_key", "") or "")
                for image in (getattr(project, "key_action_reference_images", []) or [])
            }
            return (
                expected_outfit_keys.issubset(actual_outfit_keys)
                and expected_scene_state_keys.issubset(actual_scene_state_keys)
                and expected_key_action_keys.issubset(actual_key_action_keys)
            )
        if stage == "category3":
            expected_scene_numbers = {
                int(getattr(scene, "scene_number", 0) or index + 1)
                for index, scene in enumerate(getattr(getattr(project, "script", None), "scenes", None) or [])
            }
            actual_scene_numbers = {
                int(getattr(image, "scene_number", 0) or 0)
                for image in (getattr(project, "storyboard_images", []) or [])
            }
            return bool(expected_scene_numbers) and expected_scene_numbers.issubset(actual_scene_numbers)
        return self._is_reference_library_ready_for_confirmation(project)

    def _serialize_reference_images(
        self,
        project: VideoProject,
        images: List[GeneratedImage],
        reference_type: str,
    ) -> List[Dict[str, Any]]:
        serialized: List[Dict[str, Any]] = []
        for fallback_index, image in enumerate(images):
            item = image.model_dump()
            if reference_type == "storyboard":
                slot_index = max(0, int(getattr(image, "scene_number", 1) or 1) - 1)
            elif reference_type in ("character_outfit", "scene_state", "key_action"):
                # 装扮图/布景状态图/关键动作参考图为去重复用列表，直接按顺序索引
                slot_index = fallback_index
            else:
                slot_index = self._get_reference_slot_index(
                    project,
                    reference_type,
                    getattr(image, "name", ""),
                )
            item["slot_index"] = slot_index if slot_index >= 0 else fallback_index
            serialized.append(item)
        return serialized

    def _build_scene_reference_mappings(self, project: VideoProject) -> Dict[int, Dict[str, Any]]:
        mappings: Dict[int, Dict[str, Any]] = {}
        scene_definitions = self._extract_scene_reference_definitions(
            getattr(project, "script", None),
            max(1, int(config.get("video_generation.reference_images.scene_max_count", 30))),
        )
        scene_definition_map = {
            self._normalize_name_key(item["name"]): item
            for item in scene_definitions
            if self._normalize_name_key(item["name"])
        }
        for scene in getattr(getattr(project, "script", None), "scenes", None) or []:
            scene_number = max(1, int(getattr(scene, "scene_number", 1) or 1))
            base_assets = self._select_base_reference_assets_for_scene(project, scene)
            storyboard = next(
                (
                    image for image in (getattr(project, "storyboard_images", []) or [])
                    if int(getattr(image, "scene_number", 0) or 0) == scene_number
                ),
                None,
            )
            key_action = self._find_key_action_asset_for_scene(project, scene_number)
            scene_feature_names: List[str] = []
            seen_scene_feature_keys = set()
            for part in re.split(r"[、,，/|]+", str(getattr(scene, "scene_name", "") or "")):
                definition = scene_definition_map.get(self._normalize_name_key(part))
                if not definition:
                    continue
                for feature in definition.get("scene_features", []) or []:
                    feature_text = str(feature or "").strip()
                    feature_key = self._normalize_name_key(feature_text)
                    if not feature_key or feature_key in seen_scene_feature_keys:
                        continue
                    seen_scene_feature_keys.add(feature_key)
                    scene_feature_names.append(feature_text)
            mappings[scene_number] = {
                "scene_name": getattr(scene, "scene_name", ""),
                "scene_state": getattr(scene, "scene_state", "") or "",
                "time_of_day": getattr(scene, "time_of_day", ""),
                "weather": getattr(scene, "weather", ""),
                "scene_features": scene_feature_names,
                "character_outfits": dict(getattr(scene, "character_outfits", None) or {}),
                "character_assets": [
                    {"name": image.name, "asset_id": image.asset_id, "url": image.url}
                    for image in base_assets
                    if getattr(image, "reference_type", None) in ("character", "character_outfit")
                ],
                "scene_assets": [
                    {"name": image.name, "asset_id": image.asset_id, "url": image.url}
                    for image in base_assets
                    if getattr(image, "reference_type", None) in ("scene", "scene_state")
                ],
                "key_action": (
                    {"name": key_action.name, "asset_id": key_action.asset_id, "url": key_action.url}
                    if key_action else None
                ),
                "storyboard": (
                    {"name": storyboard.name, "asset_id": storyboard.asset_id, "url": storyboard.url}
                    if storyboard else None
                ),
            }
        return mappings

    def _build_reference_output(
        self,
        project: VideoProject,
        message: Optional[str] = None,
        include_default_message: bool = False,
        stage: Optional[str] = None,
    ) -> Dict[str, Any]:
        character_images = list(getattr(project, "character_reference_images", []) or [])
        scene_images = list(getattr(project, "scene_reference_images", []) or [])
        outfit_images = list(getattr(project, "character_outfit_images", []) or [])
        scene_state_images = list(getattr(project, "scene_state_images", []) or [])
        key_action_images = list(getattr(project, "key_action_reference_images", []) or [])
        storyboard_images = list(getattr(project, "storyboard_images", []) or [])
        images = character_images + scene_images + outfit_images + scene_state_images + key_action_images + storyboard_images
        serialized_character_images = self._serialize_reference_images(project, character_images, "character")
        serialized_scene_images = self._serialize_reference_images(project, scene_images, "scene")
        serialized_outfit_images = self._serialize_reference_images(project, outfit_images, "character_outfit")
        serialized_scene_state_images = self._serialize_reference_images(project, scene_state_images, "scene_state")
        serialized_key_action_images = self._serialize_reference_images(project, key_action_images, "key_action")
        serialized_storyboard_images = self._serialize_reference_images(project, storyboard_images, "storyboard")
        expected_counts = self._expected_reference_counts(project)
        resolved_message = message
        if resolved_message is None and include_default_message:
            resolved_message = self._t(project, "message.reference.confirm_prompt")
        return {
            "step": "reference_image",
            "count": len(images),
            "urls": [image.url for image in images],
            "images": (
                serialized_character_images
                + serialized_scene_images
                + serialized_outfit_images
                + serialized_scene_state_images
                + serialized_key_action_images
                + serialized_storyboard_images
            ),
            "character_images": serialized_character_images,
            "scene_images": serialized_scene_images,
            "character_outfit_images": serialized_outfit_images,
            "scene_state_images": serialized_scene_state_images,
            "key_action_reference_images": serialized_key_action_images,
            "storyboard_images": serialized_storyboard_images,
            "library": {
                "characters": serialized_character_images,
                "scenes": serialized_scene_images,
                "character_outfits": serialized_outfit_images,
                "scene_states": serialized_scene_state_images,
                "key_actions": serialized_key_action_images,
                "storyboards": serialized_storyboard_images,
            },
            "scene_reference_mappings": self._build_scene_reference_mappings(project),
            "ready_for_confirmation": self._is_reference_library_ready_for_confirmation(project),
            "reference_stage": stage,
            "stage_ready": self._is_reference_stage_ready(project, stage) if stage else None,
            "has_category2": self._reference_stage_has_category2(project),
            "expected_count": expected_counts["total"],
            "expected_character_count": expected_counts["characters"],
            "expected_scene_count": expected_counts["scenes"],
            "expected_character_outfit_count": expected_counts["character_outfits"],
            "expected_scene_state_count": expected_counts["scene_states"],
            "expected_key_action_count": expected_counts["key_actions"],
            "expected_storyboard_count": expected_counts["storyboards"],
            "message": resolved_message,
        }

    def _sync_reference_library_state(
        self,
        project: VideoProject,
        character_images: List[Optional[GeneratedImage]],
        scene_images: List[Optional[GeneratedImage]],
        storyboard_images: List[Optional[GeneratedImage]],
        outfit_images: Optional[List[Optional[GeneratedImage]]] = None,
        scene_state_images: Optional[List[Optional[GeneratedImage]]] = None,
        key_action_images: Optional[List[Optional[GeneratedImage]]] = None,
    ) -> None:
        completed_character_images = [image for image in character_images if image is not None]
        completed_scene_images = [image for image in scene_images if image is not None]
        completed_storyboards = [image for image in storyboard_images if image is not None]
        completed_outfit_images = [image for image in (outfit_images or []) if image is not None]
        completed_scene_state_images = [image for image in (scene_state_images or []) if image is not None]
        completed_key_action_images = [image for image in (key_action_images or []) if image is not None]
        project.character_reference_images = completed_character_images
        project.scene_reference_images = completed_scene_images
        project.character_outfit_images = completed_outfit_images
        project.scene_state_images = completed_scene_state_images
        project.key_action_reference_images = completed_key_action_images
        project.storyboard_images = completed_storyboards
        project.reference_image_library = {
            "characters": completed_character_images,
            "scenes": completed_scene_images,
            "character_outfits": completed_outfit_images,
            "scene_states": completed_scene_state_images,
            "key_actions": completed_key_action_images,
            "storyboards": completed_storyboards,
        }
        project.scene_reference_mappings = self._build_scene_reference_mappings(project)
        project.images = (
            completed_character_images
            + completed_scene_images
            + completed_outfit_images
            + completed_scene_state_images
            + completed_key_action_images
            + completed_storyboards
        )
        project.reference_image = (
            project.character_reference_images[0]
            if project.character_reference_images
            else (
                project.scene_reference_images[0]
                if project.scene_reference_images
                else (project.storyboard_images[0] if project.storyboard_images else None)
            )
        )

    def _start_reference_generation_session(
        self,
        project: VideoProject,
        character_count: int,
        scene_count: int,
        storyboard_count: int,
        outfit_count: int = 0,
        scene_state_count: int = 0,
        key_action_count: int = 0,
    ) -> Dict[str, List[Optional[GeneratedImage]]]:
        slots = {
            "characters": [None] * max(0, character_count),
            "scenes": [None] * max(0, scene_count),
            "character_outfits": [None] * max(0, outfit_count),
            "scene_states": [None] * max(0, scene_state_count),
            "key_actions": [None] * max(0, key_action_count),
            "storyboards": [None] * max(0, storyboard_count),
        }
        self._reference_generation_slots[project.project_id] = slots
        self._hydrate_reference_generation_slots(project, slots)
        return slots

    def _hydrate_reference_generation_slots(
        self,
        project: VideoProject,
        slots: Dict[str, List[Optional[GeneratedImage]]],
    ) -> None:
        """Backfill generation slots from persisted project state.

        Stage retries and browser reconnects may create a fresh in-memory session while
        some assets have already been generated and persisted on the project. Hydrating
        prevents retrying the entire storyboard batch when only a subset is missing.
        """
        def put(slot_name: str, index: int, image: GeneratedImage) -> None:
            slot = slots.get(slot_name, [])
            if 0 <= index < len(slot) and slot[index] is None:
                slot[index] = image

        for image in getattr(project, "character_reference_images", []) or []:
            index = self._get_reference_slot_index(project, "character", getattr(image, "name", ""))
            put("characters", index, image)

        for image in getattr(project, "scene_reference_images", []) or []:
            index = self._get_reference_slot_index(project, "scene", getattr(image, "name", ""))
            put("scenes", index, image)

        variant_plan = self._plan_scene_variant_assets(project) if getattr(project, "script", None) else {"outfits": [], "scene_states": [], "key_actions": []}
        outfit_index_by_key = {
            str(task.get("dedup_key") or ""): index
            for index, task in enumerate(variant_plan.get("outfits", []) or [])
        }
        scene_state_index_by_key = {
            str(task.get("dedup_key") or ""): index
            for index, task in enumerate(variant_plan.get("scene_states", []) or [])
        }
        key_action_index_by_key = {
            str(task.get("dedup_key") or ""): index
            for index, task in enumerate(variant_plan.get("key_actions", []) or [])
        }

        for index, image in enumerate(getattr(project, "character_outfit_images", []) or []):
            variant_key = str(getattr(image, "variant_key", "") or "")
            put("character_outfits", outfit_index_by_key.get(variant_key, index), image)

        for index, image in enumerate(getattr(project, "scene_state_images", []) or []):
            variant_key = str(getattr(image, "variant_key", "") or "")
            put("scene_states", scene_state_index_by_key.get(variant_key, index), image)

        for index, image in enumerate(getattr(project, "key_action_reference_images", []) or []):
            variant_key = str(getattr(image, "variant_key", "") or "")
            put("key_actions", key_action_index_by_key.get(variant_key, index), image)

        for image in getattr(project, "storyboard_images", []) or []:
            index = int(getattr(image, "scene_number", 0) or 0) - 1
            put("storyboards", index, image)

    def _get_reference_generation_session(
        self,
        project: VideoProject,
    ) -> Optional[Dict[str, List[Optional[GeneratedImage]]]]:
        return self._reference_generation_slots.get(project.project_id)

    def _ensure_reference_generation_session(
        self,
        project: VideoProject,
        character_count: int,
        scene_count: int,
        storyboard_count: int,
        outfit_count: int = 0,
        scene_state_count: int = 0,
        key_action_count: int = 0,
    ) -> Dict[str, List[Optional[GeneratedImage]]]:
        """分阶段生成复用同一 session：已存在则直接返回，避免重建清空前阶段成果。"""
        existing = self._reference_generation_slots.get(project.project_id)
        if existing is not None:
            self._hydrate_reference_generation_slots(project, existing)
            return existing
        return self._start_reference_generation_session(
            project,
            character_count=character_count,
            scene_count=scene_count,
            storyboard_count=storyboard_count,
            outfit_count=outfit_count,
            scene_state_count=scene_state_count,
            key_action_count=key_action_count,
        )

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
            scene_limit = max(1, int(config.get("video_generation.reference_images.scene_max_count", 30)))
            targets = [item["name"] for item in self._extract_scene_reference_definitions(project.script, scene_limit)]

        for index, name in enumerate(targets):
            if self._normalize_name_key(name) == normalized_name:
                return index
        return -1

    def _build_reference_asset_task_key(self, project_id: str, reference_type: str, asset_name: str) -> str:
        return f"{project_id}:{reference_type}:{self._normalize_name_key(asset_name)}"

    def _nsfw_enhancement_active(self, project: VideoProject) -> bool:
        """Private NSFW enhancement is opt-in and additionally gated by project content."""
        if not private_nsfw_enabled():
            return False
        script = getattr(project, "script", None)
        trigger_values: List[Any] = [
            getattr(project, "user_input", ""),
            getattr(project, "combined_input", ""),
            getattr(script, "tone", ""),
            getattr(script, "background", ""),
        ]
        for scene in getattr(script, "scenes", None) or []:
            trigger_values.extend([
                getattr(scene, "description", ""),
                getattr(scene, "dialogue", ""),
                getattr(scene, "character_description", ""),
                getattr(scene, "mood", ""),
                getattr(scene, "character_outfits", None),
            ])
        return nsfw_content_requested(*trigger_values)

    def _plan_scene_variant_assets(
        self,
        project: VideoProject,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """基于剧本分镜规划需要生成的角色装扮图与布景状态图（已去重）。

        去重复用（用户确认）：
        - 角色装扮图按 (角色名, 装扮) 去重，相同组合只生成一次。
        - 布景状态图按 (场景名, time_of_day, weather) 去重；scene_state 只作为展示/提示词字段，不参与去重。
        触发判定（用户确认）：
        - 角色装扮图仅当分镜装扮 != 该角色默认 clothing 时才生成。
        - 仅当 time_of_day/weather != 场景定义默认值时生成布景状态图；相同布景、相同时间、相同天气只生成一次。
        计数仅依赖剧本，可在生成主图前提前得知；base 主图在执行时再查找。
        """
        script = getattr(project, "script", None)
        scene_limit = max(1, int(config.get("video_generation.reference_images.scene_max_count", 30)))
        character_map = {
            self._normalize_name_key(getattr(character, "name", "")): character
            for character in (getattr(script, "characters", None) or [])
        }
        scene_definition_map = {
            self._normalize_name_key(item["name"]): item
            for item in self._extract_scene_reference_definitions(script, scene_limit)
        }

        outfit_tasks: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        scene_state_tasks: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        key_action_tasks: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        nsfw_active = self._nsfw_enhancement_active(project)

        for scene in getattr(script, "scenes", None) or []:
            scene_number = max(1, int(getattr(scene, "scene_number", 1) or 1))
            if nsfw_active:
                dedup_key = f"scene_{scene_number:03d}::key_action"
                key_action_tasks[dedup_key] = {
                    "dedup_key": dedup_key,
                    "scene": scene,
                    "scene_number": scene_number,
                }
            outfits = getattr(scene, "character_outfits", None) or {}
            for character_name, outfit in outfits.items():
                outfit_desc = str(outfit or "").strip()
                if not outfit_desc:
                    continue
                character_key = self._normalize_name_key(character_name)
                character = character_map.get(character_key)
                if character is None:
                    continue
                default_clothing = self._normalize_name_key(getattr(character, "clothing", "") or "")
                if self._normalize_name_key(outfit_desc) == default_clothing and default_clothing:
                    continue
                dedup_key = f"{character_key}::{self._normalize_name_key(outfit_desc)}"
                if dedup_key in outfit_tasks:
                    continue
                outfit_tasks[dedup_key] = {
                    "dedup_key": dedup_key,
                    "character": character,
                    "character_key": character_key,
                    "outfit": outfit_desc,
                    "scene_number": scene_number,
                }

            scene_tod = str(getattr(scene, "time_of_day", "") or "").strip()
            scene_weather = str(getattr(scene, "weather", "") or "").strip()
            if not scene_tod and not scene_weather:
                continue
            for part in re.split(r"[、,，/|]+", str(getattr(scene, "scene_name", "") or "")):
                scene_key = self._normalize_name_key(part)
                definition = scene_definition_map.get(scene_key)
                if not definition:
                    continue
                default_tod = self._normalize_name_key(definition.get("time_of_day", "") or "")
                default_weather = self._normalize_name_key(definition.get("weather", "") or "")
                scene_tod_key = self._normalize_name_key(scene_tod)
                scene_weather_key = self._normalize_name_key(scene_weather)
                if scene_tod_key == default_tod and scene_weather_key == default_weather:
                    continue
                scene_state = "，".join(part for part in [scene_tod, scene_weather] if part)
                dedup_key = f"{scene_key}::state::{scene_tod_key}::{scene_weather_key}"
                if dedup_key in scene_state_tasks:
                    continue
                scene_state_tasks[dedup_key] = {
                    "dedup_key": dedup_key,
                    "scene_name": str(definition.get("name") or part).strip(),
                    "scene_key": scene_key,
                    "scene_description": definition.get("description", ""),
                    "scene_state": scene_state,
                    "time_of_day": scene_tod,
                    "weather": scene_weather,
                    "scene_number": scene_number,
                }

        return {
            "outfits": list(outfit_tasks.values()),
            "scene_states": list(scene_state_tasks.values()),
            "key_actions": list(key_action_tasks.values()),
        }

    def _find_outfit_asset_for_scene(
        self,
        project: VideoProject,
        character_key: str,
        outfit_desc: str,
    ) -> Optional[GeneratedImage]:
        dedup_key = f"{character_key}::{self._normalize_name_key(outfit_desc)}"
        for image in getattr(project, "character_outfit_images", []) or []:
            if getattr(image, "variant_key", None) == dedup_key:
                return image
        return None

    def _find_scene_state_asset(
        self,
        project: VideoProject,
        scene_key: str,
        scene_state: str = "",
        time_of_day: str = "",
        weather: str = "",
    ) -> Optional[GeneratedImage]:
        dedup_key = f"{scene_key}::state::{self._normalize_name_key(time_of_day)}::{self._normalize_name_key(weather)}"
        for image in getattr(project, "scene_state_images", []) or []:
            if getattr(image, "variant_key", None) == dedup_key:
                return image
        return None

    def _select_key_action_reference_assets_for_scene(self, project: VideoProject, scene) -> List[GeneratedImage]:
        """关键动作参考图专用参考资产选择。

        优先级固定为：
        1. 角色装扮图
        2. 布景状态图
        3. 角色主图（仅在对应装扮图缺失时兜底）
        4. 布景主图（仅在对应状态图缺失时兜底）
        """
        preferred_assets: List[GeneratedImage] = []
        fallback_assets: List[GeneratedImage] = []

        outfits = getattr(scene, "character_outfits", None) or {}
        outfit_key_by_char = {
            self._normalize_name_key(name): str(outfit or "").strip()
            for name, outfit in outfits.items()
            if str(outfit or "").strip()
        }
        character_ref_map = {
            self._normalize_name_key(getattr(image, "name", "")): image
            for image in (getattr(project, "character_reference_images", []) or [])
            if self._normalize_name_key(getattr(image, "name", ""))
        }
        present_characters = [
            self._normalize_name_key(name)
            for name in (getattr(scene, "characters_present", None) or [])
            if str(name or "").strip()
        ]
        for character_key in present_characters:
            outfit_desc = outfit_key_by_char.get(character_key, "")
            outfit_image = self._find_outfit_asset_for_scene(project, character_key, outfit_desc) if outfit_desc else None
            if outfit_image is not None:
                preferred_assets.append(outfit_image)
                continue
            base_character_image = character_ref_map.get(character_key)
            if base_character_image is not None:
                fallback_assets.append(base_character_image)

        scene_ref_map = {
            self._normalize_name_key(getattr(image, "name", "")): image
            for image in (getattr(project, "scene_reference_images", []) or [])
            if self._normalize_name_key(getattr(image, "name", ""))
        }
        raw_scene_name = str(getattr(scene, "scene_name", "") or "")
        scene_name_keys = [
            self._normalize_name_key(part)
            for part in re.split(r"[、,，/|]+", raw_scene_name)
            if str(part or "").strip()
        ]
        scene_tod = str(getattr(scene, "time_of_day", "") or "").strip()
        scene_weather = str(getattr(scene, "weather", "") or "").strip()
        scene_state = str(getattr(scene, "scene_state", "") or "").strip()
        for scene_key in scene_name_keys:
            state_image = self._find_scene_state_asset(project, scene_key, scene_state, scene_tod, scene_weather)
            if state_image is not None:
                preferred_assets.append(state_image)
                continue
            base_scene_image = scene_ref_map.get(scene_key)
            if base_scene_image is not None:
                fallback_assets.append(base_scene_image)

        if not preferred_assets and not fallback_assets:
            fallback_assets.extend((getattr(project, "scene_reference_images", []) or [])[:2])
        if not preferred_assets and not fallback_assets:
            fallback_assets.extend((getattr(project, "character_reference_images", []) or [])[:2])

        selected: List[GeneratedImage] = []
        seen_keys = set()
        for image in preferred_assets + fallback_assets:
            unique_key = str(getattr(image, "asset_id", "") or getattr(image, "url", "") or "")
            if not unique_key or unique_key in seen_keys:
                continue
            seen_keys.add(unique_key)
            selected.append(image)
        return selected

    def _select_base_reference_assets_for_scene(self, project: VideoProject, scene) -> List[GeneratedImage]:
        selected: List[GeneratedImage] = []
        outfits = getattr(scene, "character_outfits", None) or {}
        outfit_key_by_char = {
            self._normalize_name_key(name): str(outfit or "").strip()
            for name, outfit in outfits.items()
            if str(outfit or "").strip()
        }
        present_characters = [
            self._normalize_name_key(name)
            for name in (getattr(scene, "characters_present", None) or [])
            if str(name).strip()
        ]
        for image in getattr(project, "character_reference_images", []) or []:
            image_name = self._normalize_name_key(getattr(image, "name", ""))
            if present_characters and image_name in present_characters:
                # 若该角色在本分镜有装扮图，则优先使用装扮图代替主图
                outfit_desc = outfit_key_by_char.get(image_name)
                outfit_image = (
                    self._find_outfit_asset_for_scene(project, image_name, outfit_desc)
                    if outfit_desc else None
                )
                selected.append(outfit_image if outfit_image is not None else image)

        raw_scene_name = str(getattr(scene, "scene_name", "") or "")
        scene_name_keys = [
            self._normalize_name_key(part)
            for part in re.split(r"[、,，/|]+", raw_scene_name)
            if str(part).strip()
        ]
        scene_tod = str(getattr(scene, "time_of_day", "") or "").strip()
        scene_weather = str(getattr(scene, "weather", "") or "").strip()
        scene_state = str(getattr(scene, "scene_state", "") or "").strip()
        matched_scene_images = []
        for image in getattr(project, "scene_reference_images", []) or []:
            image_name = self._normalize_name_key(getattr(image, "name", ""))
            if scene_name_keys and image_name in scene_name_keys:
                # 若该场景在本分镜有状态图，则优先使用状态图代替主图
                state_image = self._find_scene_state_asset(project, image_name, scene_state, scene_tod, scene_weather)
                matched_scene_images.append(state_image if state_image is not None else image)
        if matched_scene_images:
            selected.extend(matched_scene_images[: max(1, len(scene_name_keys))])

        if not selected and getattr(project, "scene_reference_images", None):
            selected.extend(project.scene_reference_images[:2])
        if not selected and getattr(project, "character_reference_images", None):
            selected.extend(project.character_reference_images[:2])

        return selected

    def _get_scene_level_reference_asset(
        self,
        project: VideoProject,
        scene_number: int,
    ) -> Optional[GeneratedImage]:
        for image in getattr(project, "storyboard_images", []) or []:
            if int(getattr(image, "scene_number", 0) or 0) == scene_number:
                return image
        return None

    def _find_key_action_asset_for_scene(
        self,
        project: VideoProject,
        scene_number: int,
    ) -> Optional[GeneratedImage]:
        dedup_key = f"scene_{max(1, int(scene_number or 1)):03d}::key_action"
        for image in getattr(project, "key_action_reference_images", []) or []:
            if getattr(image, "variant_key", None) == dedup_key:
                return image
            if int(getattr(image, "scene_number", 0) or 0) == scene_number:
                return image
        return None

    def _select_reference_assets_for_scene(self, project: VideoProject, scene) -> List[GeneratedImage]:
        selected = self._select_base_reference_assets_for_scene(project, scene)
        scene_number = max(1, int(getattr(scene, "scene_number", 1) or 1))
        key_action_image = self._find_key_action_asset_for_scene(project, scene_number)
        if key_action_image is not None:
            selected.append(key_action_image)
        scene_level_image = self._get_scene_level_reference_asset(project, scene_number)
        if scene_level_image is not None:
            selected.append(scene_level_image)

        deduped: List[GeneratedImage] = []
        seen_keys = set()
        for image in selected:
            unique_key = str(getattr(image, "asset_id", "") or getattr(image, "url", "") or "")
            if not unique_key or unique_key in seen_keys:
                continue
            seen_keys.add(unique_key)
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
        return await run_generation(
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

    def set_project_video_generation_mode(self, project_id: str, generation_mode: Optional[str]) -> None:
        project = self.projects.get(project_id)
        if project and generation_mode is not None:
            project.video_generation_mode = self._normalize_generation_mode(generation_mode)

    def _project_language(self, project: Optional[VideoProject], fallback: str = "zh-CN") -> str:
        return normalize_locale(getattr(project, "output_language", fallback))

    def _t(self, project: Optional[VideoProject], key: str, **kwargs) -> str:
        return translate(self._project_language(project), key, **kwargs)

    def _normalize_review_mode(self, review_mode: Optional[str]) -> str:
        return "auto" if str(review_mode or "").strip().lower() == "auto" else "manual"

    def _normalize_generation_mode(self, generation_mode: Optional[str]) -> str:
        """归一化视频生成模式：extend=延长（串行），parallel=并行（默认）。"""
        default_mode = str(config.get("video_generation.default_generation_mode", "parallel") or "parallel").strip().lower()
        default_mode = "extend" if default_mode == "extend" else "parallel"
        value = str(generation_mode or "").strip().lower()
        if value in ("extend", "延长", "serial", "sequential"):
            return "extend"
        if value in ("parallel", "并行", "concurrent"):
            return "parallel"
        return default_mode

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
        if (
            getattr(project, "character_reference_images", None)
            or getattr(project, "scene_reference_images", None)
            or getattr(project, "storyboard_images", None)
        ):
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

    def start_comic_pdf_generation(self, project: VideoProject, websocket=None) -> None:
        """Start comic PDF generation once, without blocking video generation."""
        self._raise_if_project_ended(project)
        if getattr(project, "comic_pdf_url", None):
            project.comic_pdf_status = "completed"
            return

        existing_task = self._comic_pdf_tasks.get(project.project_id)
        if existing_task and not existing_task.done():
            return

        project.comic_pdf_status = "generating"
        project.comic_pdf_error = None
        self.save_project_state(project.project_id)

        async def _run() -> None:
            current_project = self.get_project(project.project_id)
            if not current_project:
                return
            try:
                pdf_url = await run_generation(comic_pdf_service.generate_and_upload, current_project)
                current_project.comic_pdf_url = pdf_url
                current_project.comic_pdf_status = "completed"
                current_project.comic_pdf_error = None
                self.save_project_state(current_project.project_id)
                await self._send_agent_output(websocket, "comic_pdf_agent", {
                    "status": "completed",
                    "comic_pdf_url": pdf_url,
                })
                logger.info(f"Comic PDF generated for project {current_project.project_id}: {pdf_url}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                current_project.comic_pdf_status = "failed"
                current_project.comic_pdf_error = str(e)
                self.save_project_state(current_project.project_id)
                await self._send_agent_output(websocket, "comic_pdf_agent", {
                    "status": "failed",
                    "error": str(e),
                })
                logger.error(f"Comic PDF generation failed for project {current_project.project_id}: {str(e)}")
            finally:
                task = self._comic_pdf_tasks.get(project.project_id)
                if task is asyncio.current_task():
                    self._comic_pdf_tasks.pop(project.project_id, None)

        self._comic_pdf_tasks[project.project_id] = asyncio.create_task(
            _run(),
            name=f"comic-pdf:{project.project_id}",
        )

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
            project.images = (
                list(getattr(project, "character_reference_images", []) or [])
                + list(getattr(project, "scene_reference_images", []) or [])
                + list(getattr(project, "character_outfit_images", []) or [])
                + list(getattr(project, "scene_state_images", []) or [])
                + list(getattr(project, "key_action_reference_images", []) or [])
                + list(getattr(project, "storyboard_images", []) or [])
            )
            project.current_step = "images_generated"
            self._ensure_video_flow_state(project, review_mode=review_mode, reset=not resume)
            self.start_comic_pdf_generation(project, websocket=websocket)

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

            generation_mode = self._normalize_generation_mode(getattr(project, "video_generation_mode", None))
            project.video_generation_mode = generation_mode
            logger.info(f"[FLOW] Video generation mode: {generation_mode}")

            # 并行模式（默认）：各分镜互不依赖前一分镜视频，按视频生成并发数并行生成。
            # 生成完成后 next_scene_index 推进到末尾，使下方串行 while 循环成为空操作，
            # 直接进入统一的合成/收尾逻辑。
            # 注意：手动审核模式需要逐个分镜暂停等待用户确认，与批量并行不兼容，
            # 因此并行仅在自动审核模式下生效；手动模式仍走下方串行逐个分镜流程。
            if (
                generation_mode == "parallel"
                and review_mode != "manual"
                and scene_index < len(project.script.scenes)
            ):
                await self._run_parallel_video_generation(
                    project=project,
                    start_scene_index=scene_index,
                    websocket=websocket,
                    review_mode=review_mode,
                    num_scenes=len(project.script.scenes),
                    max_retries=max_auto_retries,
                    pass_threshold=pass_threshold,
                    max_total_generations=max_total_generations,
                )
                project.next_scene_index = len(project.script.scenes)
                scene_index = len(project.script.scenes)

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
                        use_previous_video=generation_mode != "parallel",
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
                if final_approved and not getattr(scene_state, "accepted_over_retry", False):
                    scene_state.accepted_over_retry = False
                project.next_scene_index = min(scene_index + 1, len(project.script.scenes))

                # 每完成一个分镜即持久化，云端实例回收/切换后仍可回源恢复。
                self.save_project_state(project.project_id)

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

            # 视频流程结束后持久化，保证合成步骤在任意实例都能恢复项目。
            self.save_project_state(project_id)

        except Exception as e:
            logger.error(f"[FLOW] Video generation failed: {str(e)}")
            project.status = "failed"
            await self._update_progress(websocket, "error", 0, self._t(project, "error.generation_failed", error=str(e)))
            raise

        return project

    async def _run_parallel_video_generation(
        self,
        project: VideoProject,
        start_scene_index: int,
        websocket,
        review_mode: str,
        num_scenes: int,
        max_retries: int,
        pass_threshold: int,
        max_total_generations: int,
    ) -> None:
        """并行模式：从 start_scene_index 起的所有分镜互不依赖前一分镜视频，
        按视频生成并发数并行生成+审核，全部完成后返回。

        每个分镜仍复用 `_generate_and_review_video_with_retries`，但传入
        `use_previous_video=False`，因此参考素材只包含角色图 + 场景图 + 该分镜 storyboard，
        不再串联前一分镜视频，从而可以并行生成以提高速度。
        """
        video_workers = int(config.get("generation.concurrency.video_workers", 0) or 0)
        scene_max_concurrency = int(config.get("video_generation.scene_max_concurrency", 10) or 10)
        if video_workers <= 0:
            video_workers = scene_max_concurrency
        if not config.get("generation.concurrency.enabled", True):
            video_workers = 1
        video_workers = max(1, video_workers)

        pending_indexes = list(range(start_scene_index, num_scenes))
        logger.info(
            f"[FLOW] Parallel video generation: {len(pending_indexes)} scenes, "
            f"video_workers={video_workers}"
        )

        semaphore = asyncio.Semaphore(video_workers)
        results_lock = asyncio.Lock()

        async def generate_one(scene_index: int) -> None:
            self._raise_if_project_ended(project)
            scene = project.script.scenes[scene_index]
            scene_number = scene_index + 1
            progress_base = 50 + (scene_index / max(1, num_scenes)) * 40
            async with semaphore:
                try:
                    video, final_approved, _, final_score = await self._generate_and_review_video_with_retries(
                        project=project,
                        scene=scene,
                        scene_number=scene_number,
                        previous_video_url=None,
                        websocket=websocket,
                        review_mode=review_mode,
                        progress_base=progress_base,
                        num_scenes=num_scenes,
                        max_retries=max_retries,
                        pass_threshold=pass_threshold,
                        max_total_generations=max_total_generations,
                        use_previous_video=False,
                    )
                except SceneSkippedError:
                    logger.warning(f"[FLOW] Parallel mode: scene {scene_number} skipped")
                    self.save_project_state(project.project_id)
                    return
                except Exception as e:
                    logger.error(f"[FLOW] Parallel mode: scene {scene_number} failed: {str(e)}")
                    scene_state = self._get_scene_state(project, scene_number)
                    scene_state.completed = True
                    scene_state.approved = False
                    scene_state.accepted_over_retry = False
                    scene_state.last_feedback = str(e)
                    await self._send_agent_output(websocket, "video_agent", {
                        "scene_number": scene_number,
                        "status": "scene_failed",
                        "total_scenes": num_scenes,
                        "message": translate(
                            self._project_language(project),
                            "error.generation_failed",
                            error=str(e),
                        ),
                    })
                    self.save_project_state(project.project_id)
                    return

            async with results_lock:
                self._upsert_project_video(project, video)
                scene_state = self._get_scene_state(project, scene_number)
                scene_state.completed = True
                scene_state.approved = bool(final_approved)
                if final_approved and not getattr(scene_state, "accepted_over_retry", False):
                    scene_state.accepted_over_retry = False
                self.save_project_state(project.project_id)
                logger.info(
                    f"[FLOW] Scene {scene_number} completed (parallel). "
                    f"Approved: {final_approved}, Score: {final_score}"
                )

        tasks = [asyncio.create_task(generate_one(index)) for index in pending_indexes]
        if tasks:
            await asyncio.gather(*tasks)

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
        use_previous_video: bool = True,
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
                    scene_state.completed = True
                    scene_state.approved = True
                    scene_state.accepted_over_retry = True
                    await self._send_agent_output(websocket, "video_review_agent", {
                        "scene_number": scene_number,
                        "approved": True,
                        "accepted_over_retry": True,
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
                    return scene_state.best_video, True, scene_state.best_feedback, scene_state.best_score
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
            elif not use_previous_video:
                logger.info("[FLOW] Parallel mode: using reference images only (no previous-scene video)")
            else:
                logger.info("[FLOW] Extension scene: referencing previous-scene video (consistency only, must advance to new shot)")

            # 并行模式下不参考前一分镜视频，effective_previous_* 置空
            effective_previous_video_url = previous_video_url if use_previous_video else None
            effective_previous_scene = (
                project.script.scenes[scene_number - 2]
                if (use_previous_video and scene_number > 1)
                else None
            )

            try:
                scene_state.total_generation_count = attempt_number
                current_video = await run_generation(
                    self.video_agent.generate_video_with_previous,
                    scene=scene,
                    scene_index=scene_number - 1,
                    total_scenes=num_scenes,
                    project_id=project.project_id,
                    reference_image=project.reference_image,
                    reference_images=self._select_reference_assets_for_scene(project, scene),
                    previous_video_url=effective_previous_video_url,
                    user_style_info=getattr(project.script, "style", None) if getattr(project, "script", None) else None,
                    user_requirement_text=getattr(project, "combined_input", None),
                    resolution=getattr(project, "video_resolution", None),
                    aspect_ratio=getattr(project, "aspect_ratio", None),
                    previous_scene=effective_previous_scene,
                    characters=getattr(project.script, "characters", None),
                    scene_definitions=getattr(project.script, "scene_definitions", None),
                    asset_group_id=project.asset_group_id,
                    asset_project_name=project.asset_project_name,
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
                        scene_state.completed = True
                        scene_state.approved = True
                        scene_state.accepted_over_retry = True
                        await self._send_agent_output(websocket, "video_review_agent", {
                            "scene_number": scene_number,
                            "approved": True,
                            "accepted_over_retry": True,
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
                        return scene_state.best_video, True, scene_state.best_feedback, scene_state.best_score
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
                        scene_state.completed = True
                        scene_state.approved = True
                        scene_state.accepted_over_retry = True
                        await self._send_agent_output(websocket, "video_review_agent", {
                            "scene_number": scene_number,
                            "approved": True,
                            "accepted_over_retry": True,
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
                        return scene_state.best_video, True, scene_state.best_feedback, scene_state.best_score

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

            is_approved, feedback, score = await run_generation(
                self.video_review_agent.review_video,
                script_scene_description=scene.description,
                video_url=current_video.url,
                previous_video_url=effective_previous_video_url,
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
                    scene_state.completed = True
                    scene_state.approved = True
                    scene_state.accepted_over_retry = True
                    await self._send_agent_output(websocket, "video_review_agent", {
                        "scene_number": scene_number,
                        "approved": True,
                        "accepted_over_retry": True,
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
                    return scene_state.best_video, True, scene_state.best_feedback, scene_state.best_score

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

        # 确保后台的素材组准备任务已完成（create_project 已改为非阻塞）。
        await self.ensure_project_prepared(project.project_id)

        audio_text = None
        if project.audio_url:
            audio_text = await self._recognize_audio_interactive(
                project.audio_url,
                context=f"ASR for script generation project {project.project_id}",
            )
        
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

        return await run_generation(
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
            audio_text = await self._recognize_audio_interactive(
                project.audio_url,
                context=f"ASR for script rewrite project {project.project_id}",
            )

        existing_input = getattr(project, 'combined_input', '') or f"原始需求：{project.user_input}"
        project.combined_input = f"""{existing_input}

剧本修改要求：{edit_request}"""

        script = await run_generation(
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
        project.character_outfit_images = []
        project.scene_state_images = []
        project.key_action_reference_images = []
        project.storyboard_images = []
        project.reference_image_library = {}
        project.scene_reference_mappings = {}
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
        stage: str = "all",
    ) -> GeneratedImage:
        """生成角色参考图与场景参考图，并保留兼容字段。

        stage 控制惰性分阶段生成：
        - "category1": 仅角色图库 + 布景参考图库
        - "category2": 仅角色装扮图 + 布景状态图（无差异则跳过）
        - "category3": 仅各分镜故事版
        - "all": 三类一次性生成（兼容旧 execute_images_step 路径）
        """
        self._raise_if_project_ended(project)
        logger.info(f"Step 1: Generating reference image library (stage={stage}) for project {project.project_id}")
        user_style_info = getattr(project, "combined_input", None)
        aspect_ratio = getattr(project, "aspect_ratio", None)
        uploaded_assets = list(getattr(project, "uploaded_reference_images", []) or [])
        character_assets = [asset for asset in uploaded_assets if asset.reference_type == "character"]
        scene_assets = [asset for asset in uploaded_assets if asset.reference_type == "scene"]

        character_limit = max(1, int(config.get("video_generation.reference_images.character_max_count", 30)))
        scene_limit = max(1, int(config.get("video_generation.reference_images.scene_max_count", 30)))
        reference_max_concurrency = max(1, int(config.get("video_generation.reference_images.max_concurrency", 10)))
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

        variant_plan = self._plan_scene_variant_assets(project)
        outfit_tasks_plan = variant_plan["outfits"]
        scene_state_tasks_plan = variant_plan["scene_states"]
        key_action_tasks_plan = variant_plan["key_actions"]

        logger.info(
            f"Reference library target for project {project.project_id}: "
            f"{len(target_characters)} character refs, {len(scene_definitions)} scene refs, "
            f"{len(outfit_tasks_plan)} outfit refs, {len(scene_state_tasks_plan)} scene-state refs, "
            f"{len(key_action_tasks_plan)} key-action refs, "
            f"max_concurrency={reference_max_concurrency}"
        )

        session_slots = self._ensure_reference_generation_session(
            project,
            character_count=len(target_characters),
            scene_count=len(scene_definitions),
            storyboard_count=len(getattr(project.script, "scenes", None) or []),
            outfit_count=len(outfit_tasks_plan),
            scene_state_count=len(scene_state_tasks_plan),
            key_action_count=len(key_action_tasks_plan),
        )
        generated_character_images = session_slots["characters"]
        generated_scene_images = session_slots["scenes"]
        generated_outfit_images = session_slots["character_outfits"]
        generated_scene_state_images = session_slots["scene_states"]
        generated_key_action_images = session_slots["key_actions"]
        generated_storyboard_images = session_slots["storyboards"]
        semaphore = asyncio.Semaphore(reference_max_concurrency)
        progress_lock = asyncio.Lock()

        def _sync_all() -> None:
            self._sync_reference_library_state(
                project,
                generated_character_images,
                generated_scene_images,
                generated_storyboard_images,
                outfit_images=generated_outfit_images,
                scene_state_images=generated_scene_state_images,
                key_action_images=generated_key_action_images,
            )

        async def finalize_generated_image(
            category: str,
            index: int,
            stored_image: GeneratedImage,
        ) -> None:
            self._raise_if_project_ended(project)
            async with progress_lock:
                if category == "character":
                    generated_character_images[index] = stored_image
                elif category == "scene":
                    generated_scene_images[index] = stored_image
                elif category == "character_outfit":
                    generated_outfit_images[index] = stored_image
                elif category == "scene_state":
                    generated_scene_state_images[index] = stored_image
                elif category == "key_action":
                    generated_key_action_images[index] = stored_image
                else:
                    generated_storyboard_images[index] = stored_image
                _sync_all()
                if progress_callback:
                    await progress_callback(project)

        async def generate_character_job(index: int, character) -> None:
            self._raise_if_project_ended(project)
            if index < len(generated_character_images) and generated_character_images[index] is not None:
                logger.info(f"Skipping existing character reference slot {index} for project {project.project_id}")
                return
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
                    generated = await run_generation(
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
            if index < len(generated_scene_images) and generated_scene_images[index] is not None:
                logger.info(f"Skipping existing scene reference slot {index} for project {project.project_id}")
                return
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
                    generated = await run_generation(
                        self.image_agent.generate_scene_reference_image,
                        scene_name=scene_name,
                        scene_description=scene_definition["description"],
                        script=project.script,
                        user_style_info=user_style_info,
                        aspect_ratio=aspect_ratio,
                        user_reference_images=matched_urls or None,
                        scene_features=scene_definition.get("scene_features"),
                        time_of_day=scene_definition.get("time_of_day"),
                        weather=scene_definition.get("weather"),
                    )
                    stored = await self._store_reference_asset_async(project, generated, "scene", scene_name, index)
            await finalize_generated_image("scene", index, stored)

        def _find_character_base_image(character_key: str) -> Optional[GeneratedImage]:
            for image in generated_character_images:
                if image is not None and self._normalize_name_key(getattr(image, "name", "")) == character_key:
                    return image
            return None

        def _find_scene_base_image(scene_key: str) -> Optional[GeneratedImage]:
            for image in generated_scene_images:
                if image is not None and self._normalize_name_key(getattr(image, "name", "")) == scene_key:
                    return image
            return None

        async def generate_outfit_job(index: int, task: Dict[str, Any]) -> None:
            self._raise_if_project_ended(project)
            if index < len(generated_outfit_images) and generated_outfit_images[index] is not None:
                logger.info(f"Skipping existing outfit reference slot {index} for project {project.project_id}")
                return
            base_image = _find_character_base_image(task["character_key"])
            if base_image is None:
                logger.warning(
                    "No base character image found for outfit variant %s; skipping",
                    task["dedup_key"],
                )
                return
            async with semaphore:
                generated = await run_generation(
                    self.image_agent.generate_character_outfit_image,
                    character=task["character"],
                    outfit=task["outfit"],
                    base_reference_image=base_image,
                    script=project.script,
                    user_style_info=user_style_info,
                    aspect_ratio=aspect_ratio,
                )
                generated.variant_key = task["dedup_key"]
                stored = await self._store_reference_asset_async(
                    project, generated, "character_outfit", generated.name or f"outfit_{index + 1:02d}", index
                )
                stored.variant_key = task["dedup_key"]
            await finalize_generated_image("character_outfit", index, stored)

        async def generate_scene_state_job(index: int, task: Dict[str, Any]) -> None:
            self._raise_if_project_ended(project)
            if index < len(generated_scene_state_images) and generated_scene_state_images[index] is not None:
                logger.info(f"Skipping existing scene-state reference slot {index} for project {project.project_id}")
                return
            base_image = _find_scene_base_image(task["scene_key"])
            if base_image is None:
                logger.warning(
                    "No base scene image found for scene-state variant %s; skipping",
                    task["dedup_key"],
                )
                return
            async with semaphore:
                generated = await run_generation(
                    self.image_agent.generate_scene_state_image,
                    scene_name=task["scene_name"],
                    base_reference_image=base_image,
                    script=project.script,
                    scene_state=task.get("scene_state"),
                    time_of_day=task.get("time_of_day"),
                    weather=task.get("weather"),
                    scene_description=task.get("scene_description"),
                    user_style_info=user_style_info,
                    aspect_ratio=aspect_ratio,
                )
                generated.variant_key = task["dedup_key"]
                stored = await self._store_reference_asset_async(
                    project, generated, "scene_state", generated.name or f"scene_state_{index + 1:02d}", index
                )
                stored.variant_key = task["dedup_key"]
            await finalize_generated_image("scene_state", index, stored)

        async def generate_key_action_job(index: int, task: Dict[str, Any]) -> None:
            self._raise_if_project_ended(project)
            if index < len(generated_key_action_images) and generated_key_action_images[index] is not None:
                logger.info(f"Skipping existing key-action reference slot {index + 1} for project {project.project_id}")
                return
            scene = task["scene"]
            reference_images = self._select_key_action_reference_assets_for_scene(project, scene)
            async with semaphore:
                generated = await run_generation(
                    self.image_agent.generate_key_action_reference_image,
                    scene=scene,
                    script=project.script,
                    reference_images=reference_images,
                    user_style_info=user_style_info,
                    aspect_ratio=aspect_ratio,
                )
                generated.variant_key = task["dedup_key"]
                stored = await self._store_reference_asset_async(
                    project,
                    generated,
                    "key_action",
                    generated.name or f"scene_{index + 1:02d}_key_action",
                    index,
                )
                stored.variant_key = task["dedup_key"]
            await finalize_generated_image("key_action", index, stored)

        async def generate_storyboard_job(index: int, scene) -> None:
            self._raise_if_project_ended(project)
            if index < len(generated_storyboard_images) and generated_storyboard_images[index] is not None:
                logger.info(f"Skipping existing storyboard slot {index + 1} for project {project.project_id}")
                return
            base_reference_images = self._select_base_reference_assets_for_scene(project, scene)
            key_action_image = self._find_key_action_asset_for_scene(
                project,
                max(1, int(getattr(scene, "scene_number", index + 1) or index + 1)),
            )
            if key_action_image is not None:
                base_reference_images.append(key_action_image)
            async with semaphore:
                storyboard = await self._generate_storyboard_with_review(
                    project=project,
                    scene=scene,
                    reference_images=base_reference_images,
                    user_style_info=user_style_info,
                    aspect_ratio=aspect_ratio,
                    scene_number=index + 1,
                )
                stored_storyboard = await self._store_reference_asset_async(
                    project,
                    storyboard,
                    "storyboard",
                    storyboard.name or f"scene_{index + 1:02d}_storyboard",
                    index,
                )
            await finalize_generated_image("storyboard", index, stored_storyboard)

        run_category1 = stage in ("all", "category1")
        run_category2 = stage in ("all", "category2")
        run_category3 = stage in ("all", "category3")

        async def gather_or_raise(stage_name: str, pending_tasks: List[asyncio.Task]) -> None:
            if not pending_tasks:
                return
            results = await asyncio.gather(*pending_tasks, return_exceptions=True)
            errors = [result for result in results if isinstance(result, Exception)]
            if errors:
                logger.warning(
                    f"Reference stage {stage_name} finished with {len(errors)} failed task(s) "
                    f"for project {project.project_id}; successful tasks were preserved"
                )
                raise errors[0]

        tasks = []
        if run_category1:
            tasks = [
                asyncio.create_task(generate_character_job(index, character))
                for index, character in enumerate(target_characters)
                if not (index < len(generated_character_images) and generated_character_images[index] is not None)
            ] + [
                asyncio.create_task(generate_scene_job(index, scene_definition))
                for index, scene_definition in enumerate(scene_definitions)
                if not (index < len(generated_scene_images) and generated_scene_images[index] is not None)
            ]
            logger.info(
                f"Reference category1 stage for project {project.project_id}: "
                f"characters_total={len(target_characters)}, scenes_total={len(scene_definitions)}, "
                f"pending={len(tasks)}, "
                f"skipped={len(target_characters) + len(scene_definitions) - len(tasks)}, "
                f"max_concurrency={reference_max_concurrency}"
            )

        try:
            if tasks:
                started_at = time.monotonic()
                await gather_or_raise("category1", tasks)
                logger.info(
                    f"Reference category1 stage completed for project {project.project_id}: "
                    f"generated={len(tasks)}, elapsed={time.monotonic() - started_at:.2f}s"
                )

            _sync_all()

            if not project.reference_image:
                raise RuntimeError("No reference images were generated")

            if run_category2:
                # 所有角色主图 + 场景主图生成完成后，再按每个分镜的角色装扮 / 布景状态信息，
                # 生成角色装扮图与布景状态图（已去重复用）。并行生成，受 max_concurrency 控制。
                variant_tasks = [
                    asyncio.create_task(generate_outfit_job(index, task))
                    for index, task in enumerate(outfit_tasks_plan)
                    if not (index < len(generated_outfit_images) and generated_outfit_images[index] is not None)
                ] + [
                    asyncio.create_task(generate_scene_state_job(index, task))
                    for index, task in enumerate(scene_state_tasks_plan)
                    if not (index < len(generated_scene_state_images) and generated_scene_state_images[index] is not None)
                ] + [
                    asyncio.create_task(generate_key_action_job(index, task))
                    for index, task in enumerate(key_action_tasks_plan)
                    if not (index < len(generated_key_action_images) and generated_key_action_images[index] is not None)
                ]
                logger.info(
                    f"Reference category2 stage for project {project.project_id}: "
                    f"outfits_total={len(outfit_tasks_plan)}, scene_states_total={len(scene_state_tasks_plan)}, "
                    f"key_actions_total={len(key_action_tasks_plan)}, "
                    f"pending={len(variant_tasks)}, "
                    f"skipped={len(outfit_tasks_plan) + len(scene_state_tasks_plan) + len(key_action_tasks_plan) - len(variant_tasks)}, "
                    f"max_concurrency={reference_max_concurrency}"
                )
                if variant_tasks:
                    started_at = time.monotonic()
                    await gather_or_raise("category2", variant_tasks)
                    logger.info(
                        f"Reference category2 stage completed for project {project.project_id}: "
                        f"generated={len(variant_tasks)}, elapsed={time.monotonic() - started_at:.2f}s"
                    )
                    _sync_all()

            if run_category3:
                # 所有角色图（含装扮图）+ 场景图（含状态图）生成完成后，再逐个分镜生成 9 宫格 storyboard。
                # 多个分镜的 storyboard 并行生成，并发数由参考图库配置的 max_concurrency（semaphore）控制。
                scenes = list(getattr(project.script, "scenes", None) or [])
                storyboard_tasks = [
                    asyncio.create_task(generate_storyboard_job(index, scene))
                    for index, scene in enumerate(scenes)
                    if not (index < len(generated_storyboard_images) and generated_storyboard_images[index] is not None)
                ]
                logger.info(
                    f"Storyboard generation stage for project {project.project_id}: "
                    f"total={len(scenes)}, pending={len(storyboard_tasks)}, "
                    f"skipped={len(scenes) - len(storyboard_tasks)}, max_concurrency={reference_max_concurrency}"
                )
                if storyboard_tasks:
                    started_at = time.monotonic()
                    await gather_or_raise("category3", storyboard_tasks)
                    logger.info(
                        f"Storyboard generation stage completed for project {project.project_id}: "
                        f"generated={len(storyboard_tasks)}, elapsed={time.monotonic() - started_at:.2f}s"
                    )

            _sync_all()

            return project.reference_image
        finally:
            # 分阶段生成时，仅在 category3（或旧 all 路径）完成后才结束 session，
            # 以保住 category1/category2 之间单张重生成所需的 session_slots 上下文。
            if stage in ("all", "category3"):
                self._finish_reference_generation_session(project)

    def _reset_reference_library_state(self, project: VideoProject) -> None:
        project.character_reference_images = []
        project.scene_reference_images = []
        project.character_outfit_images = []
        project.scene_state_images = []
        project.key_action_reference_images = []
        project.storyboard_images = []
        project.reference_image_library = {}
        project.scene_reference_mappings = {}
        project.images = []
        project.reference_image = None

    async def generate_reference_image_with_retry(
        self,
        project: VideoProject,
        progress_callback: Optional[Callable[[VideoProject], Awaitable[None]]] = None,
    ) -> GeneratedImage:
        self._raise_if_project_ended(project)
        # 确保后台素材组准备任务完成，再进行参考图生成/登记。
        await self.ensure_project_prepared(project.project_id)
        active_task = self._reference_generation_tasks.get(project.project_id)
        if active_task and not active_task.done():
            return await asyncio.shield(active_task)
        self._reset_reference_library_state(project)

        async def run_generation() -> GeneratedImage:
            max_auto_retries = max(0, int(config.get("video_generation.reference_images.auto_retry_count", 2)))
            last_error: Optional[Exception] = None

            for attempt in range(max_auto_retries + 1):
                self._raise_if_project_ended(project)
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

    async def generate_reference_stage_with_retry(
        self,
        project: VideoProject,
        stage: str,
        progress_callback: Optional[Callable[[VideoProject], Awaitable[None]]] = None,
    ) -> GeneratedImage:
        """分阶段生成参考图（category1/category2/category3），含自动重试。

        - 分阶段重试不清空已成功图片，只幂等重跑本阶段缺失槽位。
        - 幂等表 key 使用 `f"{project_id}:{stage}"`，避免不同阶段互相 shield。
        - session 仅在 category3 完成后清理（见 `_generate_reference_image` finally）。
        """
        self._raise_if_project_ended(project)
        await self.ensure_project_prepared(project.project_id)
        stage_key = f"{project.project_id}:{stage}"
        active_task = self._reference_generation_tasks.get(stage_key)
        if active_task and not active_task.done():
            return await asyncio.shield(active_task)
        if self._is_reference_stage_ready(project, stage):
            logger.info(
                f"Reference stage {stage} already ready for project {project.project_id}; "
                "skipping duplicate generation"
            )
            if not project.reference_image:
                project.reference_image = (
                    (project.character_reference_images[0] if project.character_reference_images else None)
                    or (project.scene_reference_images[0] if project.scene_reference_images else None)
                    or (project.storyboard_images[0] if project.storyboard_images else None)
                )
            if project.reference_image:
                return project.reference_image

        async def run_generation() -> GeneratedImage:
            max_auto_retries = max(0, int(config.get("video_generation.reference_images.auto_retry_count", 2)))
            last_error: Optional[Exception] = None

            for attempt in range(max_auto_retries + 1):
                self._raise_if_project_ended(project)
                try:
                    if attempt > 0:
                        logger.warning(
                            f"Reference stage {stage} generation retry {attempt}/{max_auto_retries} "
                            f"for project {project.project_id}"
                        )
                    return await self._generate_reference_image(
                        project, progress_callback=progress_callback, stage=stage
                    )
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        f"Reference stage {stage} generation failed on attempt {attempt + 1}/{max_auto_retries + 1} "
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
            name=f"reference-generation:{stage_key}",
        )
        self._reference_generation_tasks[stage_key] = generation_task
        try:
            return await asyncio.shield(generation_task)
        finally:
            if self._reference_generation_tasks.get(stage_key) is generation_task:
                self._reference_generation_tasks.pop(stage_key, None)

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
                generated = await run_generation(
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
                            max(1, int(config.get("video_generation.reference_images.scene_max_count", 30))),
                        )
                        if self._normalize_name_key(item["name"]) == self._normalize_name_key(normalized_name)
                    ),
                    None,
                )
                if not target_scene:
                    raise ValueError(self._t(project, "error.reference_scene_not_found", name=normalized_name))
                generated = await run_generation(
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
                self._sync_reference_library_state(
                    project,
                    session_slots["characters"],
                    session_slots["scenes"],
                    session_slots.get("storyboards", []),
                    outfit_images=session_slots.get("character_outfits", []),
                    scene_state_images=session_slots.get("scene_states", []),
                    key_action_images=session_slots.get("key_actions", []),
                )
            else:
                image_list[image_index] = stored
                if reference_type == "character":
                    project.reference_image_library["characters"] = project.character_reference_images
                else:
                    project.reference_image_library["scenes"] = project.scene_reference_images
                project.scene_reference_mappings = self._build_scene_reference_mappings(project)
                project.images = (
                    list(getattr(project, "character_reference_images", []) or [])
                    + list(getattr(project, "scene_reference_images", []) or [])
                    + list(getattr(project, "character_outfit_images", []) or [])
                    + list(getattr(project, "scene_state_images", []) or [])
                    + list(getattr(project, "key_action_reference_images", []) or [])
                    + list(getattr(project, "storyboard_images", []) or [])
                )
                project.reference_image = (
                    project.character_reference_images[0]
                    if project.character_reference_images
                    else (
                        project.scene_reference_images[0]
                        if project.scene_reference_images
                        else (project.storyboard_images[0] if project.storyboard_images else None)
                    )
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

    async def regenerate_variant_asset(
        self,
        project: VideoProject,
        reference_type: str,
        variant_key: str,
        feedback: str = "用户要求重新生成",
    ) -> GeneratedImage:
        """重新生成指定的角色装扮图、布景状态图或关键动作参考图。"""
        self._raise_if_project_ended(project)
        reference_type = str(reference_type or "").strip().lower()
        variant_key = str(variant_key or "").strip()
        if reference_type == "scene_state":
            reference_type = "scene_state"
        if reference_type not in {"character_outfit", "scene_state", "key_action"}:
            raise ValueError(self._t(project, "error.unsupported_reference_type", reference_type=reference_type or "unknown"))
        if not variant_key:
            raise ValueError(self._t(project, "error.reference_asset_name_required"))

        if reference_type == "character_outfit":
            image_list = project.character_outfit_images
        elif reference_type == "scene_state":
            image_list = project.scene_state_images
        else:
            image_list = project.key_action_reference_images
        target_index = next(
            (
                index for index, image in enumerate(image_list)
                if getattr(image, "variant_key", None) == variant_key
            ),
            -1,
        )
        if target_index < 0:
            raise ValueError(
                self._t(
                    project,
                    "error.reference_asset_not_found",
                    reference_type=reference_type,
                    name=variant_key,
                )
            )

        task_key = self._build_reference_asset_task_key(project.project_id, reference_type, variant_key)
        active_task = self._reference_asset_regeneration_tasks.get(task_key)
        if active_task and not active_task.done():
            return await asyncio.shield(active_task)

        async def run_regeneration() -> GeneratedImage:
            self._raise_if_project_ended(project)
            user_style_info = getattr(project, "combined_input", None)
            aspect_ratio = getattr(project, "aspect_ratio", None)
            variant_plan = self._plan_scene_variant_assets(project)

            if reference_type == "character_outfit":
                task = next(
                    (item for item in variant_plan.get("outfits", []) if item["dedup_key"] == variant_key),
                    None,
                )
                if task is None:
                    raise ValueError(
                        self._t(project, "error.reference_asset_not_found", reference_type=reference_type, name=variant_key)
                    )
                base_image = next(
                    (
                        image for image in (getattr(project, "character_reference_images", []) or [])
                        if self._normalize_name_key(getattr(image, "name", "")) == task["character_key"]
                    ),
                    None,
                )
                if base_image is None:
                    raise ValueError(self._t(project, "error.reference_character_not_found", name=variant_key))
                generated = await run_generation(
                    self.image_agent.generate_character_outfit_image,
                    character=task["character"],
                    outfit=task["outfit"],
                    base_reference_image=base_image,
                    script=project.script,
                    user_style_info=user_style_info,
                    aspect_ratio=aspect_ratio,
                )
                generated.variant_key = variant_key
                stored = await self._store_reference_asset_async(
                    project, generated, "character_outfit", generated.name or f"outfit_{target_index + 1:02d}", target_index
                )
            elif reference_type == "scene_state":
                task = next(
                    (item for item in variant_plan.get("scene_states", []) if item["dedup_key"] == variant_key),
                    None,
                )
                if task is None:
                    raise ValueError(
                        self._t(project, "error.reference_asset_not_found", reference_type=reference_type, name=variant_key)
                    )
                base_image = next(
                    (
                        image for image in (getattr(project, "scene_reference_images", []) or [])
                        if self._normalize_name_key(getattr(image, "name", "")) == task["scene_key"]
                    ),
                    None,
                )
                if base_image is None:
                    raise ValueError(self._t(project, "error.reference_scene_not_found", name=variant_key))
                generated = await run_generation(
                    self.image_agent.generate_scene_state_image,
                    scene_name=task["scene_name"],
                    base_reference_image=base_image,
                    script=project.script,
                    scene_state=task.get("scene_state"),
                    time_of_day=task.get("time_of_day"),
                    weather=task.get("weather"),
                    scene_description=task.get("scene_description"),
                    user_style_info=user_style_info,
                    aspect_ratio=aspect_ratio,
                )
                generated.variant_key = variant_key
                stored = await self._store_reference_asset_async(
                    project, generated, "scene_state", generated.name or f"scene_state_{target_index + 1:02d}", target_index
                )
            else:
                task = next(
                    (item for item in variant_plan.get("key_actions", []) if item["dedup_key"] == variant_key),
                    None,
                )
                if task is None:
                    raise ValueError(
                        self._t(project, "error.reference_asset_not_found", reference_type=reference_type, name=variant_key)
                    )
                scene = task["scene"]
                reference_images = self._select_key_action_reference_assets_for_scene(project, scene)
                generated = await run_generation(
                    self.image_agent.generate_key_action_reference_image,
                    scene=scene,
                    script=project.script,
                    reference_images=reference_images,
                    user_style_info=user_style_info,
                    aspect_ratio=aspect_ratio,
                )
                generated.variant_key = variant_key
                stored = await self._store_reference_asset_async(
                    project, generated, "key_action", generated.name or f"key_action_{target_index + 1:02d}", target_index
                )
            stored.variant_key = variant_key

            if reference_type == "character_outfit":
                outfit_images = list(getattr(project, "character_outfit_images", []) or [])
                if 0 <= target_index < len(outfit_images):
                    outfit_images[target_index] = stored
                scene_state_images = list(getattr(project, "scene_state_images", []) or [])
                key_action_images = list(getattr(project, "key_action_reference_images", []) or [])
            elif reference_type == "scene_state":
                scene_state_images = list(getattr(project, "scene_state_images", []) or [])
                if 0 <= target_index < len(scene_state_images):
                    scene_state_images[target_index] = stored
                outfit_images = list(getattr(project, "character_outfit_images", []) or [])
                key_action_images = list(getattr(project, "key_action_reference_images", []) or [])
            else:
                key_action_images = list(getattr(project, "key_action_reference_images", []) or [])
                if 0 <= target_index < len(key_action_images):
                    key_action_images[target_index] = stored
                outfit_images = list(getattr(project, "character_outfit_images", []) or [])
                scene_state_images = list(getattr(project, "scene_state_images", []) or [])

            self._sync_reference_library_state(
                project,
                list(getattr(project, "character_reference_images", []) or []),
                list(getattr(project, "scene_reference_images", []) or []),
                list(getattr(project, "storyboard_images", []) or []),
                outfit_images=outfit_images,
                scene_state_images=scene_state_images,
                key_action_images=key_action_images,
            )
            return stored

        regeneration_task = asyncio.create_task(
            run_regeneration(),
            name=f"variant-regeneration:{task_key}",
        )
        self._reference_asset_regeneration_tasks[task_key] = regeneration_task
        try:
            return await asyncio.shield(regeneration_task)
        finally:
            if self._reference_asset_regeneration_tasks.get(task_key) is regeneration_task:
                self._reference_asset_regeneration_tasks.pop(task_key, None)

    def _build_character_gender_map(self, project: VideoProject) -> Dict[str, str]:
        """构建 角色名 -> 性别 映射，供故事版审核判断性别是否画错。"""
        gender_map: Dict[str, str] = {}
        for character in getattr(getattr(project, "script", None), "characters", None) or []:
            name = str(getattr(character, "name", "") or "").strip()
            gender = str(getattr(character, "gender", "") or "").strip()
            if name:
                gender_map[name] = gender
        return gender_map

    async def _generate_storyboard_with_review(
        self,
        project: VideoProject,
        scene,
        reference_images: List[GeneratedImage],
        user_style_info: Optional[str],
        aspect_ratio: Optional[str],
        websocket=None,
        scene_number: Optional[int] = None,
    ) -> GeneratedImage:
        """生成单个分镜的 9 宫格故事版，并按 3 条件自动审核 + 自动重生成。

        审核 3 条件（任一不满足即重生成）：白描线稿多宫格 / 同一角色不重复且肢体正常 / 性别正确。
        重生成次数上限参考视频重试逻辑，由 `storyboard_review.max_retries` 控制。
        """
        review_enabled = bool(config.get('storyboard_review.enabled', True))
        review_mode = str(config.get('storyboard_review.default_mode', 'auto') or 'auto').strip().lower()
        max_retries = max(0, int(config.get('storyboard_review.max_retries', 2)))
        # 总生成次数 = 首次 + 最多 max_retries 次重生成
        max_attempts = max_retries + 1

        project_language = self._project_language(project)
        gender_map = self._build_character_gender_map(project)
        characters_present = list(getattr(scene, "characters_present", None) or [])
        scene_desc = str(getattr(scene, "description", "") or "")
        scene_no = int(scene_number if scene_number is not None else (getattr(scene, "scene_number", 0) or 0))

        generated: Optional[GeneratedImage] = None
        for attempt in range(1, max_attempts + 1):
            self._raise_if_project_ended(project)
            generated = await run_generation(
                self.image_agent.generate_scene_storyboard_image,
                scene=scene,
                script=project.script,
                reference_images=reference_images,
                user_style_info=user_style_info,
                aspect_ratio=aspect_ratio,
            )

            if not review_enabled or review_mode != "auto":
                return generated

            self._raise_if_project_ended(project)
            approved, feedback = await run_generation(
                self.storyboard_review_agent.review_storyboard,
                image_url=generated.url,
                scene_description=scene_desc,
                characters_present=characters_present,
                character_gender_map=gender_map,
                output_language=project_language,
            )
            logger.info(
                f"[SB_REVIEW] Scene {scene_no} storyboard review attempt {attempt}/{max_attempts}: "
                f"approved={approved}"
            )
            if websocket is not None:
                await self._send_agent_output(websocket, "storyboard_review_agent", {
                    "scene_number": scene_no,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "approved": bool(approved),
                    "feedback": feedback,
                })

            if approved or attempt >= max_attempts:
                if not approved:
                    logger.warning(
                        f"[SB_REVIEW] Scene {scene_no} storyboard still not approved after "
                        f"{max_attempts} attempts, keeping last result"
                    )
                return generated
            logger.info(f"[SB_REVIEW] Scene {scene_no} storyboard rejected, regenerating (attempt {attempt + 1})")

        return generated

    async def regenerate_storyboard_asset(
        self,
        project: VideoProject,
        scene_number: int,
        feedback: str = "用户要求重新生成",
    ) -> GeneratedImage:
        """重新生成指定分镜的 9 宫格故事版图片（参考该分镜的角色图 + 场景图 + 分镜内容）。"""
        self._raise_if_project_ended(project)
        scene_number = int(scene_number or 0)
        scenes = list(getattr(getattr(project, "script", None), "scenes", None) or [])
        target_scene = next(
            (
                scene for scene in scenes
                if int(getattr(scene, "scene_number", 0) or 0) == scene_number
            ),
            None,
        )
        if target_scene is None and 1 <= scene_number <= len(scenes):
            target_scene = scenes[scene_number - 1]
        if target_scene is None:
            raise ValueError(
                self._t(
                    project,
                    "error.reference_asset_not_found",
                    reference_type="storyboard",
                    name=str(scene_number),
                )
            )

        task_key = self._build_reference_asset_task_key(project.project_id, "storyboard", str(scene_number))
        active_task = self._reference_asset_regeneration_tasks.get(task_key)
        if active_task and not active_task.done():
            return await asyncio.shield(active_task)

        async def run_regeneration() -> GeneratedImage:
            self._raise_if_project_ended(project)
            index = scene_number - 1 if scene_number >= 1 else 0
            base_reference_images = self._select_base_reference_assets_for_scene(project, target_scene)
            key_action_image = self._find_key_action_asset_for_scene(project, scene_number)
            if key_action_image is not None:
                base_reference_images.append(key_action_image)
            user_style_info = getattr(project, "combined_input", None)
            aspect_ratio = getattr(project, "aspect_ratio", None)

            generated = await self._generate_storyboard_with_review(
                project=project,
                scene=target_scene,
                reference_images=base_reference_images,
                user_style_info=user_style_info,
                aspect_ratio=aspect_ratio,
                scene_number=scene_number,
            )
            stored = await self._store_reference_asset_async(
                project,
                generated,
                "storyboard",
                generated.name or f"scene_{index + 1:02d}_storyboard",
                index,
            )

            # 用新故事版替换现有列表中同分镜的条目。
            storyboards = list(getattr(project, "storyboard_images", []) or [])
            replaced = False
            for i, image in enumerate(storyboards):
                if int(getattr(image, "scene_number", 0) or 0) == scene_number:
                    storyboards[i] = stored
                    replaced = True
                    break
            if not replaced:
                storyboards.append(stored)
            self._sync_reference_library_state(
                project,
                list(getattr(project, "character_reference_images", []) or []),
                list(getattr(project, "scene_reference_images", []) or []),
                storyboards,
                outfit_images=list(getattr(project, "character_outfit_images", []) or []),
                scene_state_images=list(getattr(project, "scene_state_images", []) or []),
                key_action_images=list(getattr(project, "key_action_reference_images", []) or []),
            )
            return stored

        regeneration_task = asyncio.create_task(
            run_regeneration(),
            name=f"storyboard-regeneration:{task_key}",
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
            characters=getattr(project.script, "characters", None),
            scene_definitions=getattr(project.script, "scene_definitions", None),
            asset_group_id=project.asset_group_id,
            asset_project_name=project.asset_project_name,
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

    def save_project_state(self, project_id: str) -> None:
        """将项目状态快照持久化到 TOS，用于云端多实例/实例回收后的回源恢复。

        注意：持久化失败不能影响主流程（best-effort），仅记录日志。
        """
        project = self.projects.get(project_id)
        if not project:
            return
        try:
            state_json = project.model_dump_json()
            tos_service.put_project_state_json(project_id, state_json)
        except Exception as e:
            logger.warning(f"save_project_state failed for {project_id}: {str(e)}")

    def _restore_project_from_tos(self, project_id: str) -> Optional[VideoProject]:
        """内存未命中时，尝试从 TOS 快照恢复项目到内存。"""
        try:
            state_json = tos_service.get_project_state_json(project_id)
            if not state_json:
                return None
            project = VideoProject.model_validate_json(state_json)
            self.projects[project_id] = project
            logger.info(f"Project {project_id} restored from TOS snapshot")
            return project
        except Exception as e:
            logger.warning(f"Failed to restore project {project_id} from TOS: {str(e)}")
            return None

    def get_project(self, project_id: str) -> Optional[VideoProject]:
        """获取项目信息；内存未命中时回源 TOS 快照（云端跨实例恢复）。"""
        if not project_id:
            return None
        project = self.projects.get(project_id)
        if project is not None:
            return project
        return self._restore_project_from_tos(project_id)

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
