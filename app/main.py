# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import os
import json
import asyncio
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.config import config
from app.utils.i18n import normalize_locale, translate
from app.utils.logger import get_logger
from app.utils.task_paths import ensure_project_temp_dir, cleanup_project_temp_dir
from app.utils.thread_pools import run_interactive, run_generation, shutdown_pools
from app.agents.main_agent import main_agent
from app.services.asset_library_service import asset_library_service
from app.services.tos_service import tos_service
from app.services.asr_service import asr_service
from app.models.schemas import UploadResponse, ASRResponse
from app import __version__

logger = get_logger("main")



# 创建FastAPI应用
app = FastAPI(
    title="Video Chatbot Agent",
    description="AI视频生成聊天机器人",
    version=__version__
)


@app.middleware("http")
async def disable_cache_for_frontend(request: Request, call_next):
    response = await call_next(request)
    if request.method == "GET" and (
        request.url.path == "/" or
        request.url.path.startswith("/static/")
    ):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# 云端准入密码中间件：仅当环境变量 ACCESS_PASSWORD 非空时启用（本地默认不设置 => 放行）。
from app.middleware.access_gate import AccessGateMiddleware
app.add_middleware(AccessGateMiddleware)

# 存储WebSocket连接和等待状态
websocket_connections: Dict[str, WebSocket] = {}
step_confirmations: Dict[str, asyncio.Event] = {}
project_client_owners: Dict[str, str] = {}
disconnect_cleanup_tasks: Dict[str, asyncio.Task] = {}
# 视频单分镜重生成按 project + scene 去重；不同分镜仍可并发执行。
video_scene_regeneration_tasks: Dict[str, asyncio.Task] = {}


class ReconnectingWebSocketProxy:
    """Route background task messages to the latest WebSocket for a client_id."""

    def __init__(self, client_id: str):
        self.client_id = client_id

    async def send_json(self, message: dict):
        websocket = websocket_connections.get(self.client_id)
        if websocket is None:
            logger.debug(f"Client {self.client_id} has no active WebSocket, skipping background message")
            return
        client_state = getattr(websocket, "client_state", None)
        client_state_name = getattr(client_state, "name", "CONNECTED")
        if client_state_name != "CONNECTED":
            logger.warning(
                f"WebSocket for client {self.client_id} is not connected "
                f"(state: {client_state_name})"
            )
            return
        await websocket.send_json(jsonable_encoder(message))


def get_reconnecting_websocket(client_id: Optional[str]):
    if not client_id:
        return None
    return ReconnectingWebSocketProxy(client_id)


@app.on_event("shutdown")
async def _shutdown_thread_pools() -> None:
    """进程退出时优雅关闭交互式/生成线程池。"""
    shutdown_pools()


def parse_form_bool(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def bind_project_to_client(project_id: str, client_id: Optional[str]) -> None:
    if project_id and client_id:
        project_client_owners[project_id] = client_id
        logger.info(f"Project {project_id} is bound to client {client_id}")


def validate_project_client_access(project_id: str, client_id: Optional[str]) -> Optional[str]:
    """校验项目是否由当前客户端持有；若旧连接已断开，则允许重新绑定。"""
    if not project_id or not client_id:
        return None

    owner_client_id = project_client_owners.get(project_id)
    if not owner_client_id:
        bind_project_to_client(project_id, client_id)
        return None

    if owner_client_id == client_id:
        return None

    if owner_client_id not in websocket_connections:
        logger.warning(
            f"Project {project_id} owner {owner_client_id} is offline; "
            f"rebinding to client {client_id}"
        )
        bind_project_to_client(project_id, client_id)
        return None

    project = main_agent.get_project(project_id)
    locale = normalize_locale(getattr(project, "output_language", "zh-CN")) if project else "zh-CN"
    return translate(locale, "error.project_access_denied", project_id=project_id)


def validate_project_cleanup_access(project_id: str, client_id: Optional[str]) -> Optional[str]:
    project = main_agent.get_project(project_id)
    if not project:
        return None

    owner_client_id = project_client_owners.get(project_id)
    if not owner_client_id:
        return None

    if client_id and owner_client_id == client_id:
        return None

    if owner_client_id not in websocket_connections:
        return None

    locale = normalize_locale(getattr(project, "output_language", "zh-CN"))
    return translate(locale, "error.project_access_denied", project_id=project_id)


def resolve_regenerate_client_id(
    project_id: str,
    client_id: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """解析重新生成时应使用的 client_id，兼容云端多实例 / WS 短暂重连。

    返回 (effective_client_id, error_key)。error_key 为 None 表示校验通过。

    背景：弹性多实例部署下，/regenerate 的 HTTP 请求可能落在与持有 WebSocket
    不同的实例上；此时全局有效的 client_id 在本实例内存的 websocket_connections
    中查不到，旧逻辑会硬报 "client_id 无效或连接已断开"，导致重新生成整体失败。

    处理策略（按优先级）：
    1. client_id 已在本实例活跃连接中 -> 直接使用。
    2. client_id 缺失但本实例有连接 -> 退化为项目属主或第一个连接（兼容旧前端）。
    3. client_id 提供但不在本实例连接中：
       - 若其为该项目登记的属主 -> 信任它（结果经 WS 代理按 client_id 路由，
         WS 恢复/其它实例持有时仍可送达）。
       - 否则若本实例存在该项目属主连接 -> 使用属主连接。
       - 否则若本实例有任意连接 -> 退化为第一个连接。
       - 否则若有登记属主 -> 信任属主；都没有才返回 no_active_websocket。
    """
    owner_client_id = project_client_owners.get(project_id)

    # 1. 常规命中：client_id 就在本实例活跃连接里。
    if client_id and client_id in websocket_connections:
        return client_id, None

    # 2. 未传 client_id：兼容旧前端。
    if not client_id:
        if owner_client_id and owner_client_id in websocket_connections:
            logger.warning(
                f"/regenerate: missing client_id, fallback to project owner {owner_client_id}"
            )
            return owner_client_id, None
        if websocket_connections:
            fallback = next(iter(websocket_connections.keys()))
            logger.warning(f"/regenerate: missing client_id, fallback to {fallback}")
            return fallback, None
        return None, "error.no_active_websocket"

    # 3. client_id 提供但不在本实例连接中（多实例 / 重连中）。
    if owner_client_id and client_id == owner_client_id:
        # 属主本人发起：信任该 client_id，结果经 ReconnectingWebSocketProxy 按 id 路由。
        logger.warning(
            f"/regenerate: client {client_id} not on this instance but is project "
            f"{project_id} owner; trusting id for cross-instance/reconnect delivery"
        )
        return client_id, None
    if owner_client_id and owner_client_id in websocket_connections:
        logger.warning(
            f"/regenerate: client {client_id} not on this instance; routing to "
            f"project owner {owner_client_id}"
        )
        return owner_client_id, None
    if websocket_connections:
        fallback = next(iter(websocket_connections.keys()))
        logger.warning(
            f"/regenerate: client {client_id} not on this instance; fallback to {fallback}"
        )
        return fallback, None
    if owner_client_id:
        logger.warning(
            f"/regenerate: no local WS on this instance; trusting registered owner "
            f"{owner_client_id} for project {project_id}"
        )
        return owner_client_id, None
    return None, "error.no_active_websocket"


def build_end_cleanup_keep_prefixes(project) -> list[str]:
    keep_prefixes = []
    if getattr(project, "final_video_url", None):
        keep_prefixes.append("videos/final")
    if getattr(project, "comic_pdf_url", None):
        keep_prefixes.append("documents/comics")
    return keep_prefixes


def _scene_regeneration_blocks_merge(project) -> bool:
    """判断是否存在「正在重新生成 / 尚未通过审核」的分镜，从而必须阻塞 merge。

    两种阻塞情形：
    1. regenerating_scene_numbers 非空：有分镜的重新生成+审核尚未结束。
    2. 已生成完所有分镜后，仍有分镜 scene_state 未 approved：重新生成完成但未通过审核。
    """
    if list(getattr(project, "regenerating_scene_numbers", []) or []):
        return True

    scene_states = getattr(project, "video_scene_states", None) or {}
    total_scenes = len(getattr(getattr(project, "script", None), "scenes", []) or [])
    # 仅当已进入「全部分镜生成完毕」阶段才用 approved 兜底判断，避免在正常串行生成中途误伤。
    next_scene_index = int(getattr(project, "next_scene_index", 0) or 0)
    if total_scenes > 0 and next_scene_index >= total_scenes:
        for state in scene_states.values():
            if getattr(state, "completed", False) and not getattr(state, "approved", False):
                return True
    return False


async def notify_videos_step_complete_if_ready(client_id: str, project, lang: str) -> None:
    total_scenes = len(getattr(getattr(project, "script", None), "scenes", []) or [])
    completed_videos = len(getattr(project, "videos", None) or [])
    next_scene_index = int(getattr(project, "next_scene_index", 0) or 0)
    if (
        total_scenes > 0
        and completed_videos >= total_scenes
        and next_scene_index >= total_scenes
        and not _scene_regeneration_blocks_merge(project)
    ):
        await manager.send_message(client_id, {
            "type": "step_complete",
            "data": {
                "step": "videos",
                "message": translate(lang, "step.videos.complete")
            }
        })


async def end_project_resources(
    project_id: str,
    *,
    reason: str,
    client_id: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    project = main_agent.get_project(project_id)
    if not project:
        try:
            tos_service.cleanup_project_directory(project_id, keep_prefixes=[])
        except Exception as e:
            logger.error(f"TOS cleanup failed for missing project {project_id}: {str(e)}")
        try:
            cleanup_project_temp_dir(project_id)
        except Exception as e:
            logger.error(f"Local temp cleanup failed for missing project {project_id}: {str(e)}")
        project_client_owners.pop(project_id, None)
        return {"project_id": project_id, "status": "missing", "end_reason": reason}

    main_agent.mark_project_ended(project_id, reason=reason)
    await cleanup_project_files(
        project,
        keep_prefixes=build_end_cleanup_keep_prefixes(project),
        cleanup_asset_library=True,
    )
    main_agent.remove_project(project_id)
    project_client_owners.pop(project_id, None)

    if client_id:
        disconnect_cleanup_tasks.pop(f"{project_id}:{client_id}", None)

    return {"project_id": project_id, "status": "ended", "end_reason": reason}


async def schedule_disconnect_project_cleanup(client_id: str) -> None:
    owned_project_ids = [
        project_id for project_id, owner_client_id in list(project_client_owners.items())
        if owner_client_id == client_id
    ]
    if not owned_project_ids:
        return

    for project_id in owned_project_ids:
        task_key = f"{project_id}:{client_id}"
        existing_task = disconnect_cleanup_tasks.get(task_key)
        if existing_task and not existing_task.done():
            continue

        async def delayed_cleanup(current_project_id: str, current_client_id: str, current_task_key: str) -> None:
            try:
                # 云端网关在长任务静默期断连后，浏览器通常需要数秒才能重连。
                # 给足宽限时间（15s），避免把“断连-重连”窗口误判为用户关闭页面。
                await asyncio.sleep(15)
                # 稳定 client_id 重连后会重新登记到 websocket_connections，这里即可自愈。
                if current_client_id in websocket_connections:
                    return
                if project_client_owners.get(current_project_id) != current_client_id:
                    return
                # 关键防线：绝不清理仍在生成中的项目。只有已结束/已失败的项目才允许因断连而清理。
                # 否则会重演“视频生成中断连 -> 项目被删 -> 合成阶段未找到项目”的云端故障。
                project = main_agent.get_project(current_project_id)
                if project is not None:
                    status = getattr(project, "status", "") or ""
                    is_ended = bool(getattr(project, "is_ended", False))
                    if not is_ended and status not in {"failed"}:
                        logger.info(
                            f"Skip disconnect cleanup for in-progress project "
                            f"{current_project_id} (status={status})"
                        )
                        return
                await end_project_resources(
                    current_project_id,
                    reason="browser_disconnect",
                    client_id=current_client_id,
                )
            except Exception as e:
                logger.error(f"Disconnect cleanup failed for project {current_project_id}: {str(e)}")
            finally:
                disconnect_cleanup_tasks.pop(current_task_key, None)

        disconnect_cleanup_tasks[task_key] = asyncio.create_task(
            delayed_cleanup(project_id, client_id, task_key)
        )


@app.get("/", response_class=HTMLResponse)
async def get_index():
    """主页面"""
    return FileResponse(
        "static/index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    project_id: Optional[str] = Form(None),
) -> UploadResponse:
    """上传文件到TOS"""
    try:
        project_temp_dir = ensure_project_temp_dir(project_id or "shared")
        temp_path = str(project_temp_dir / file.filename)
        content = await file.read()

        file_extension = os.path.splitext(file.filename or "")[1].lower()
        audio_extensions = {".wav", ".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".webm", ".weba"}
        file_category = "uploads/audio" if file_extension in audio_extensions else "uploads/images"

        # 磁盘写入与 TOS 上传均为阻塞式外部 IO。云端单实例在跑生成管线时，
        # 若在事件循环内同步执行会阻塞整个循环，导致 /upload 请求撞网关超时
        # （前端表现为 "upstream request timeout" 解析失败 / 发送失败）。
        # 使用独立的「交互式线程池」执行，避免被生成管线的阻塞 IO 占满线程而排队超时。
        def _write_and_upload() -> str:
            with open(temp_path, "wb") as f:
                f.write(content)
            try:
                return tos_service.upload_file(
                    temp_path,
                    project_id=project_id,
                    category=file_category,
                )
            finally:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

        url = await run_interactive(_write_and_upload)

        return UploadResponse(
            success=True,
            url=url,
            filename=file.filename
        )
        
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        return UploadResponse(
            success=False,
            error=str(e)
        )


@app.post("/asr")
async def speech_to_text(audio_url: str = Form(...)) -> ASRResponse:
    """语音识别"""
    try:
        parsed_url = urlparse(audio_url)
        audio_extension = os.path.splitext(parsed_url.path)[1].lower()
        logger.info(f"ASR request for {audio_url} with extension {audio_extension}")
        # ASR 为阻塞式网络调用，放到交互式线程池执行，避免阻塞事件循环
        # 或与生成管线争抢线程导致排队超时。
        text = await run_interactive(asr_service.recognize, audio_url)
        return ASRResponse(success=True, text=text)
    except Exception as e:
        logger.error(f"ASR failed: {str(e)}", exc_info=True)
        return ASRResponse(success=False, error=str(e))


@app.post("/chat")
async def chat(
    message: str = Form(...),
    project_id: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    image_urls: Optional[str] = Form("[]"),
    image_assets: Optional[str] = Form("[]"),
    audio_url: Optional[str] = Form(None),
    ui_language: Optional[str] = Form("zh-CN"),
    use_original_reference: Optional[str] = Form("false"),
):
    """聊天接口"""
    ui_language = normalize_locale(ui_language)
    try:
        # 解析图片URL
        images = json.loads(image_urls) if image_urls else []
        uploaded_image_assets = json.loads(image_assets) if image_assets else []
        existing_project = main_agent.get_project(project_id) if project_id else None
        is_new_project = existing_project is None
        
        # 如果没有项目ID，创建新项目
        if is_new_project:
            project = await main_agent.create_project(
                user_input=message,
                reference_images=images,
                uploaded_reference_images=uploaded_image_assets,
                audio_url=audio_url,
                output_language=ui_language,
                use_original_reference=parse_form_bool(use_original_reference),
                project_id=project_id,
            )
            project_id = project.project_id
            bind_project_to_client(project_id, client_id)
        else:
            access_error = validate_project_client_access(project_id, client_id)
            if access_error:
                return {"success": False, "error": access_error}
            main_agent.set_project_output_language(project_id, ui_language)
        
        project = main_agent.get_project(project_id)
        if is_new_project:
            # 首次输入后直接进入剧本生成，不再经过冗余 LLM 对话预处理
            response = translate(ui_language, "message.direct_start_script")
            ready_for_script_start = True
            script_updated = False
            script_output = None
        else:
            # 手动模式下，剧本生成完成后允许通过聊天框直接提交“改剧本”要求
            if main_agent.can_rewrite_script(project):
                script = await main_agent.rewrite_script(project_id, message)
                response = translate(ui_language, "message.script.updated")
                ready_for_script_start = False
                script_updated = True
                script_output = script.dict()
            else:
                # 已有项目时，仍保留“补充需求追加”能力
                response = main_agent.chat_with_user(message, project_id, output_language=ui_language)
                ready_for_script_start = False
                script_updated = False
                script_output = None
        
        return {
            "success": True,
            "project_id": project_id,
            "is_new_project": is_new_project,
            "response": response,
            "ready_for_script_start": ready_for_script_start,
            "script_updated": script_updated,
            "script_output": script_output,
        }
        
    except Exception as e:
        logger.error(f"Chat failed: {str(e)}")
        return {
            "success": False,
            "error": translate(ui_language, "chat.error", error=str(e))
        }


@app.get("/project/{project_id}")
async def get_project(project_id: str):
    """获取项目信息"""
    project = main_agent.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=translate("zh-CN", "error.project_not_found"))
    
    return {
        "success": True,
        "project": project.dict()
    }


@app.get("/project/{project_id}/restore")
async def restore_project_snapshot(project_id: str):
    """页面刷新后恢复项目 UI 状态所需的快照。

    只读场景使用 get_project_for_read：内存未命中时回源 TOS，命中时按 state_version
    与 TOS 对账并取较新副本。这样云端多实例下即便请求落到「持有陈旧内存态」的实例，
    也能拿到属主实例已推进的阶段产出，兜底右侧 UI 长期不显示的问题（本地单实例不复现）。
    """
    project = main_agent.get_project_for_read(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=translate("zh-CN", "error.project_not_found"))

    lang = normalize_locale(getattr(project, "output_language", "zh-CN"))

    # 视频分镜：仅回传已生成成功且有 URL 的分镜，附带审核结论用于前端复原。
    scene_states = getattr(project, "video_scene_states", None) or {}
    videos_payload: List[Dict[str, Any]] = []
    for video in getattr(project, "videos", None) or []:
        scene_number = int(getattr(video, "scene_number", 0) or 0)
        url = getattr(video, "url", "") or ""
        if not url:
            continue
        state = scene_states.get(scene_number)
        videos_payload.append({
            "scene_number": scene_number,
            "url": url,
            "approved": bool(getattr(state, "approved", False)) if state else False,
            "accepted_over_retry": bool(getattr(state, "accepted_over_retry", False)) if state else False,
            "completed": bool(getattr(state, "completed", False)) if state else True,
            "score": int(getattr(state, "best_score", -1) or -1) if state else -1,
            "feedback": (getattr(state, "best_feedback", "") or "") if state else "",
        })

    total_scenes = len(getattr(getattr(project, "script", None), "scenes", []) or [])
    reference_ready = bool(getattr(project, "character_reference_images", None)) or bool(
        getattr(project, "scene_reference_images", None)
    ) or bool(getattr(project, "storyboard_images", None))

    # 视频阶段是否已启动（用于云端多实例下前端权威对账）。
    # 参考图确认后即进入 images_generated；此时可能尚无任何视频 URL，
    # 但前端应当立即渲染“视频生成”模块，避免跨实例 WS 消息丢失导致 UI 空白。
    current_step_value = getattr(project, "current_step", "") or ""
    video_phase_started = current_step_value in {
        "images_generated",
        "videos_generated",
        "completed",
    }

    return {
        "success": True,
        "snapshot": {
            "project_id": project.project_id,
            "is_ended": bool(getattr(project, "is_ended", False)),
            "current_step": getattr(project, "current_step", "") or "",
            "status": getattr(project, "status", "") or "",
            # 当前正在执行（生成中）的阶段：前端据此在「已进入但尚无数据」的生成窗口
            # 也能补出底部状态栏与右侧占位，兜底云端多实例下丢失的 status/agent_output 推送。
            "processing_phase": getattr(project, "processing_phase", "") or "",
            "output_language": lang,
            "video_review_mode": getattr(project, "video_review_mode", "manual") or "manual",
            "video_generation_mode": getattr(project, "video_generation_mode", "parallel") or "parallel",
            "script": project.script.dict() if getattr(project, "script", None) else None,
            "reference_output": (
                main_agent._build_reference_output(project) if reference_ready else None
            ),
            "videos": videos_payload,
            "total_scenes": total_scenes,
            # 视频阶段已启动标记：前端据此立即渲染视频生成模块并开启对账轮询，
            # 即使跨实例 WS 消息全部丢失也能保证 UI 与项目状态一致。
            "video_phase_started": video_phase_started,
            "next_scene_index": int(getattr(project, "next_scene_index", 0) or 0),
            "regenerating_scene_numbers": list(getattr(project, "regenerating_scene_numbers", []) or []),
            "final_video_url": getattr(project, "final_video_url", None),
            "comic_pdf_url": getattr(project, "comic_pdf_url", None),
            "comic_pdf_status": getattr(project, "comic_pdf_status", "pending") or "pending",
            "comic_pdf_error": getattr(project, "comic_pdf_error", None),
            # 是否仍有分镜在重新生成/未通过审核（用于恢复后判断能否进入合成）。
            "merge_blocked": _scene_regeneration_blocks_merge(project),
        }
    }


@app.get("/api/frontend-config")
async def get_frontend_config():
    """返回前端可安全读取的 UI 配置"""
    auto_run_countdown_seconds = config.get('ui.auto_run_countdown_seconds', 10)
    reference_config = config.get('video_generation.reference_images', {}) or {}
    default_generation_mode = str(config.get('video_generation.default_generation_mode', 'parallel') or 'parallel').strip().lower()
    default_generation_mode = 'extend' if default_generation_mode == 'extend' else 'parallel'
    return {
        "success": True,
        "config": {
            "auto_run_countdown_seconds": max(0, int(auto_run_countdown_seconds)),
            "reference_image_max_count": max(1, int(reference_config.get("upload_max_count", 40))),
            "character_reference_max_count": max(1, int(reference_config.get("upload_character_max_count", 20))),
            "scene_reference_max_count": max(1, int(reference_config.get("upload_scene_max_count", 20))),
            "default_video_generation_mode": default_generation_mode,
        }
    }


@app.post("/continue_generate_after_reference")
async def continue_generate_after_reference(
    project_id: str = Form(...),
    client_id: str = Form(None),
    ui_language: Optional[str] = Form("zh-CN"),
    review_mode: Optional[str] = Form(None),
    generation_mode: Optional[str] = Form(None),
):
    """用户确认参考图后，开始新流程视频生成（逐个生成+审核）"""
    ui_language = normalize_locale(ui_language)
    try:
        # 优先使用前端传入的 client_id；兼容云端多实例 / WS 短暂重连：
        # 若 client_id 是项目属主或本实例有可路由连接，则不再硬报 client_id 无效。
        client_id, client_error_key = resolve_regenerate_client_id(project_id, client_id)
        if client_error_key:
            return {"success": False, "error": translate(ui_language, client_error_key)}

        access_error = validate_project_client_access(project_id, client_id)
        if access_error:
            return {"success": False, "error": access_error}

        main_agent.set_project_output_language(project_id, ui_language)
        main_agent.set_project_video_review_mode(project_id, review_mode)
        main_agent.set_project_video_generation_mode(project_id, generation_mode)
        # 非阻塞：视频生成为分钟级长任务，若在 HTTP 内 await 会撞 API 网关超时（约60s），
        # 导致前端误报“启动视频生成失败”。这里改为后台任务执行，立即返回；
        # 进度与结果统一通过 WebSocket 推送。
        asyncio.create_task(
            continue_generate_after_reference_confirmation(client_id, project_id, review_mode=review_mode)
        )

        return {"success": True}
    except Exception as e:
        logger.error(f"continue_generate_after_reference failed: {str(e)}")
        return {"success": False, "error": translate(ui_language, "error.step_execute_failed", error=str(e))}


@app.post("/continue_reference_stage")
async def continue_reference_stage(
    project_id: str = Form(...),
    client_id: str = Form(None),
    stage: str = Form(...),
    generation_mode: Optional[str] = Form(None),
    ui_language: Optional[str] = Form("zh-CN"),
):
    """用户确认某个参考图子阶段后推进下一子阶段（或进入视频）。

    遵守 API 网关约 60s 超时约束：立即 create_task 触发生成并返回 {"success": True}，
    生成进度与结果统一通过 WebSocket 推送。stage 为“当前已完成阶段”。
    """
    ui_language = normalize_locale(ui_language)
    try:
        # 兼容云端多实例 / WS 短暂重连：自动进入下一阶段时，HTTP 可能落在与持有
        # WebSocket 不同的实例上，不应因本实例查不到 client_id 就硬报无效。
        client_id, client_error_key = resolve_regenerate_client_id(project_id, client_id)
        if client_error_key:
            return {"success": False, "error": translate(ui_language, client_error_key)}

        access_error = validate_project_client_access(project_id, client_id)
        if access_error:
            return {"success": False, "error": access_error}

        if stage not in ("category1", "category2", "category3"):
            return {"success": False, "error": translate(ui_language, "error.invalid_step", step=stage)}

        main_agent.set_project_output_language(project_id, ui_language)
        main_agent.set_project_video_generation_mode(project_id, generation_mode)

        project = main_agent.get_project(project_id)
        has_category2 = bool(main_agent._reference_stage_has_category2(project)) if project else False
        next_stage = _compute_next_reference_stage(stage, has_category2)

        if next_stage == "videos":
            asyncio.create_task(
                continue_generate_after_reference_confirmation(client_id, project_id)
            )
        else:
            asyncio.create_task(
                execute_reference_stage(client_id, project_id, next_stage)
            )

        return {"success": True}
    except Exception as e:
        logger.error(f"continue_reference_stage failed: {str(e)}")
        return {"success": False, "error": translate(ui_language, "error.step_execute_failed", error=str(e))}


@app.post("/regenerate")
async def regenerate(
    project_id: str = Form(...),
    type: str = Form(...),  # 'image' 或 'video'
    scene_number: int = Form(...),
    client_id: Optional[str] = Form(None),
    ui_language: Optional[str] = Form("zh-CN"),
    reference_type: Optional[str] = Form(None),
    reference_name: Optional[str] = Form(None),
    reference_slot_index: Optional[int] = Form(None),
):
    """重新生成图片或视频"""
    ui_language = normalize_locale(ui_language)
    try:
        project = main_agent.get_project(project_id)
        if not project:
            return {"success": False, "error": translate(ui_language, "error.project_not_found")}
        access_error = validate_project_client_access(project_id, client_id)
        if access_error:
            return {"success": False, "error": access_error}
        main_agent.set_project_output_language(project_id, ui_language)
        project = main_agent.get_project(project_id)

        # 获取用户指定的比例和风格信息
        aspect_ratio = getattr(project, 'aspect_ratio', None)
        user_style_info = getattr(project, 'combined_input', None)

        logger.info(f"Regenerating {type} for scene {scene_number}, aspect_ratio: {aspect_ratio or 'default'}")

        if type == "image":
            if scene_number == 0:
                current_step = getattr(project, "current_step", "")
                video_flow_started = current_step in {"images_generated", "videos_generated", "completed"}
                video_flow_started = video_flow_started or bool(getattr(project, "videos", None))
                video_flow_started = video_flow_started or getattr(project, "next_scene_index", 0) > 0
                logger.info(
                    f"Reference image regenerate gate: current_step={current_step}, "
                    f"videos={len(getattr(project, 'videos', []) or [])}, "
                    f"next_scene_index={getattr(project, 'next_scene_index', 0)}, "
                    f"locked={video_flow_started}"
                )
                if video_flow_started:
                    return {
                        "success": False,
                        "error": translate(ui_language, "error.reference_regeneration_locked")
                    }

                normalized_reference_type = str(reference_type or "").strip().lower()
                normalized_reference_name = str(reference_name or "").strip()
                if not normalized_reference_type or not normalized_reference_name:
                    return {
                        "success": False,
                        "error": translate(ui_language, "error.reference_asset_target_required")
                    }

                # 校验目标是否存在 / 是否被锁定（同步、轻量），实际生成放到后台执行。
                if normalized_reference_type == "storyboard":
                    pass  # 故事版目标由后台任务按 scene_number 定位
                elif normalized_reference_type in {"character_outfit", "scene_state", "key_action"}:
                    pass  # 装扮/状态/关键动作目标由后台任务按 variant_key 定位
                else:
                    existing_reference_images = (
                        getattr(project, "character_reference_images", [])
                        if normalized_reference_type == "character"
                        else getattr(project, "scene_reference_images", [])
                    )
                    target_reference = next(
                        (
                            item for item in (existing_reference_images or [])
                            if str(getattr(item, "name", "") or "").strip() == normalized_reference_name
                        ),
                        None
                    )
                    if target_reference and getattr(target_reference, "regenerate_locked", False):
                        return {
                            "success": False,
                            "error": translate(ui_language, "error.reference_regeneration_locked_original")
                        }

                # 需要有效的 WebSocket 连接以推送最终结果。兼容云端多实例 / WS 重连：
                # 只要 client_id 是项目属主或本实例有可路由连接，就不再硬报 client_id 无效。
                effective_client_id, client_error_key = resolve_regenerate_client_id(
                    project_id, client_id
                )
                if client_error_key:
                    return {"success": False, "error": translate(ui_language, client_error_key)}

                # 非阻塞：单张图片重生成耗时可达 40~60s，云端 API 网关约 60s 超时，
                # 若在 HTTP 内同步 await 会触发网关断连，前端误报“重新生成失败”。
                # 改为后台任务执行，立即返回；结果统一通过 WebSocket 推送。
                asyncio.create_task(
                    regenerate_reference_asset_background(
                        client_id=effective_client_id,
                        project_id=project_id,
                        ui_language=ui_language,
                        reference_type=normalized_reference_type,
                        reference_name=normalized_reference_name,
                        reference_slot_index=reference_slot_index,
                    )
                )
                return {
                    "success": True,
                    "async": True,
                    "type": "image",
                    "scene_number": scene_number,
                    "reference_type": normalized_reference_type,
                    "reference_name": normalized_reference_name,
                }

            # 获取参考图URL
            reference_image_url = None
            if scene_number == 999:
                # 重新生成尾帧图时，使用参考图库作为角色参考
                if hasattr(project, 'reference_image') and project.reference_image:
                    reference_image_url = project.reference_image.url
                    logger.info(f"Using reference image for end frame (999) regeneration: {reference_image_url}")
            else:
                # 分镜图片需要使用生成的参考图作为参考
                if hasattr(project, 'reference_image') and project.reference_image:
                    reference_image_url = project.reference_image.url
                    logger.info(f"Using reference image for scene {scene_number} regeneration: {reference_image_url}")

            # 重新生成图片，保持用户指定的比例和风格
            new_image = await run_generation(
                main_agent.image_agent.regenerate_image,
                scene_number=scene_number,
                script=project.script,
                feedback="用户要求重新生成",
                user_style_info=user_style_info,
                aspect_ratio=aspect_ratio,
                reference_image_url=reference_image_url
            )

            # 更新项目中的图片
            for i, img in enumerate(project.images):
                if img.scene_number == scene_number:
                    project.images[i] = new_image
                    break

            # 如果是参考图（scene_number == 0），同时更新 project.reference_image
            if scene_number == 0:
                project.reference_image = new_image
                logger.info(f"Updated project.reference_image to new reference image: {new_image.url}")

            return {
                "success": True,
                "url": new_image.url,
                "type": "image",
                "scene_number": scene_number
            }

        elif type == "video":
            logger.info(f"Regenerating video for scene {scene_number}")
            logger.info(f"Total scenes in script: {len(project.script.scenes)}")
            logger.info(f"Total videos: {len(project.videos)}")

            # 需要有效的 WebSocket 连接以推送最终结果。兼容云端多实例 / WS 重连。
            effective_client_id, client_error_key = resolve_regenerate_client_id(
                project_id, client_id
            )
            if client_error_key:
                return {"success": False, "error": translate(ui_language, client_error_key)}

            regeneration_key = f"{project_id}:{int(scene_number)}"
            active_task = video_scene_regeneration_tasks.get(regeneration_key)
            if active_task and not active_task.done():
                logger.info(
                    f"/regenerate: video scene {scene_number} already has an active task; "
                    "skip duplicate submission"
                )
                return {
                    "success": True,
                    "async": True,
                    "deduplicated": True,
                    "type": "video",
                    "scene_number": scene_number,
                }

            # 非阻塞：单个分镜视频重生成耗时可达数分钟，远超云端 API 网关约 60s 超时，
            # 若在 HTTP 内同步 await 会触发网关 504 断连，前端误报“重新生成失败”（但后台仍在跑）。
            # 改为后台任务执行，立即返回；最终结果统一通过 WebSocket（video_scene_regenerated）推送。
            regeneration_task = asyncio.create_task(
                regenerate_video_scene_background(
                    project_id=project_id,
                    scene_number=scene_number,
                    client_id=effective_client_id,
                    ui_language=ui_language,
                )
            )
            video_scene_regeneration_tasks[regeneration_key] = regeneration_task

            def _clear_video_regeneration_task(done_task: asyncio.Task) -> None:
                if video_scene_regeneration_tasks.get(regeneration_key) is done_task:
                    video_scene_regeneration_tasks.pop(regeneration_key, None)

            regeneration_task.add_done_callback(_clear_video_regeneration_task)
            return {
                "success": True,
                "async": True,
                "type": "video",
                "scene_number": scene_number,
            }
        else:
            return {"success": False, "error": translate(ui_language, "error.invalid_type")}

    except Exception as e:
        logger.error(f"Regenerate failed: {str(e)}")
        return {"success": False, "error": translate(ui_language, "error.generation_failed", error=str(e))}


async def _regenerate_video_scene(
    *,
    project,
    project_id: str,
    scene_number: int,
    client_id: Optional[str],
    ui_language: str,
):
    """执行单个分镜视频的重新生成 + 审核；由 /regenerate 的 video 分支包裹调用。

    调用方负责在外层维护 project.regenerating_scene_numbers（阻塞 merge）。
    """
    try:
        # 获取参考图
        reference_image = getattr(project, 'reference_image', None)
        if not reference_image:
            return {"success": False, "error": translate(ui_language, "error.reference_missing")}

        review_mode = getattr(project, "video_review_mode", "manual")
        scene_state = main_agent._get_scene_state(project, scene_number)
        total_generation_limit = max(1, int(config.get('video_generation.scene_total_generate_limit', 3)))
        # 仅延长模式参考前一分镜视频；并行模式各分镜独立生成，不引用上一分镜视频。
        generation_mode = main_agent._normalize_generation_mode(getattr(project, "video_generation_mode", None))
        previous_video_url = (
            main_agent._get_previous_video_url(project, scene_number - 1)
            if generation_mode == "extend"
            else None
        )
        project_language = normalize_locale(getattr(project, "output_language", "zh-CN"))
        manual_request_attempt = 0
        while True:
            manual_request_attempt += 1
            scene_state.manual_regeneration_count += 1

            try:
                # 重新生成视频，使用参考图保持人物一致性
                new_video = await run_generation(
                    main_agent.video_agent.regenerate_video,
                    scene_number=scene_number,
                    script=project.script,
                    project_id=project.project_id,
                    reference_image=reference_image,
                    reference_images=main_agent._select_reference_assets_for_scene(project, project.script.scenes[scene_number - 1]),
                    previous_video_url=previous_video_url,
                    feedback="用户要求重新生成",
                    # 已有分镜脚本时，不再把初始用户长文本塞进视频提示词
                    user_style_info=getattr(project.script, "style", None),
                    user_requirement_text=getattr(project, "combined_input", None),
                    resolution=getattr(project, "video_resolution", None),
                    aspect_ratio=getattr(project, "aspect_ratio", None),
                )
                new_video = await main_agent.archive_scene_video_async(
                    project,
                    new_video,
                    generation_count=main_agent._next_scene_archive_attempt(scene_state),
                )
            except Exception as e:
                scene_state.generation_failure_count += 1
                scene_state.last_feedback = str(e)
                can_skip_scene = (
                    review_mode == "manual" and
                    scene_state.generation_failure_count >= total_generation_limit
                )
                return {
                    "success": False,
                    "error": translate(project_language, "error.video_generation_failed", error=str(e)),
                    "scene_number": scene_number,
                    "can_skip_scene": can_skip_scene,
                    "generation_failure_count": scene_state.generation_failure_count,
                    "max_generation_count": total_generation_limit,
                    "skip_message": translate(
                        project_language,
                        "message.video.scene_can_skip",
                        scene=scene_number,
                        limit=total_generation_limit,
                    ) if can_skip_scene else None,
                }

            if main_agent._has_duplicate_video_seed(project, new_video.seed):
                duplicate_seed = main_agent._normalize_video_seed(new_video.seed) or "unknown"
                duplicate_message = translate(
                    project_language,
                    "message.video.duplicate_seed_retry",
                    scene=scene_number,
                    seed=duplicate_seed,
                )
                logger.warning(
                    f"Scene {scene_number} regenerate got duplicate seed {duplicate_seed}, retry without review"
                )
                scene_state.generation_failure_count += 1
                scene_state.last_feedback = duplicate_message
                can_skip_scene = (
                    review_mode == "manual" and
                    scene_state.generation_failure_count >= total_generation_limit
                )
                if manual_request_attempt >= total_generation_limit:
                    return {
                        "success": False,
                        "error": translate(
                            project_language,
                            "message.video.duplicate_seed_retry_limit",
                            scene=scene_number,
                            limit=total_generation_limit,
                            seed=duplicate_seed,
                        ),
                        "scene_number": scene_number,
                        "can_skip_scene": False,
                        "generation_failure_count": scene_state.generation_failure_count,
                        "max_generation_count": total_generation_limit,
                    }
                if can_skip_scene:
                    return {
                        "success": False,
                        "error": duplicate_message,
                        "scene_number": scene_number,
                        "can_skip_scene": True,
                        "generation_failure_count": scene_state.generation_failure_count,
                        "max_generation_count": total_generation_limit,
                        "skip_message": translate(
                            project_language,
                            "message.video.scene_can_skip",
                            scene=scene_number,
                            limit=total_generation_limit,
                        ),
                    }
                continue
            break

        logger.info(f"Video regenerated successfully: {new_video.url}")
        scene_state.last_video = new_video
        scene_state.generation_failure_count = 0
        main_agent._register_video_seed(project, new_video.seed)

        # 更新项目中的视频
        video_found = False
        for i, vid in enumerate(project.videos):
            if vid.scene_number == scene_number:
                project.videos[i] = new_video
                video_found = True
                logger.info(f"Updated video at index {i}")
                break

        if not video_found:
            logger.warning(f"Video for scene {scene_number} not found in project, adding new video")
            project.videos.append(new_video)

        is_approved, feedback, score = await run_generation(
            main_agent.video_review_agent.review_video,
            script_scene_description=project.script.scenes[scene_number - 1].description,
            video_url=new_video.url,
            previous_video_url=previous_video_url,
            reference_image_url=project.reference_image.url,
            output_language=normalize_locale(getattr(project, "output_language", "zh-CN")),
        )

        scene_state.last_score = score
        scene_state.last_feedback = feedback
        scene_state.completed = True
        scene_state.approved = bool(is_approved)
        if score > scene_state.best_score:
            scene_state.best_score = score
            scene_state.best_feedback = feedback
            scene_state.best_video = new_video

        review_output = {
            "scene_number": scene_number,
            "approved": is_approved,
            "score": score,
            "retry_count": scene_state.auto_retry_count,
            "max_retries": max(0, int(config.get('video_review.max_retries', 2))),
            "generation_count": scene_state.total_generation_count,
            "manual_regeneration_count": scene_state.manual_regeneration_count,
            "max_generation_count": total_generation_limit,
            "feedback": feedback,
            "review_mode": review_mode,
            "manual_continue_allowed": review_mode == "manual",
            "next_step": "merge" if scene_number == len(project.script.scenes) else "videos",
            "is_last_scene": scene_number == len(project.script.scenes),
            "message": translate(
                normalize_locale(getattr(project, "output_language", "zh-CN")),
                "message.video.review_passed" if is_approved else "message.video.review_failed",
                scene=scene_number,
                score=score,
                feedback=feedback
            )
        }

        websocket = get_reconnecting_websocket(client_id)
        if websocket:
            await websocket.send_json(jsonable_encoder({
                "type": "agent_output",
                "data": {
                    "agent": "video_review_agent",
                    "output": review_output
                }
            }))

        return {
            "success": True,
            "url": new_video.url,
            "type": "video",
            "scene_number": scene_number,
            "review": review_output,
        }

    except Exception as e:
        logger.error(f"Regenerate video failed: {str(e)}")
        return {"success": False, "error": translate(ui_language, "error.generation_failed", error=str(e))}



async def regenerate_video_scene_background(
    *,
    project_id: str,
    scene_number: int,
    client_id: str,
    ui_language: str,
) -> None:
    """后台执行单个分镜视频的重新生成 + 审核，结果统一通过 WebSocket 推送。

    单个分镜视频重生成耗时可达数分钟，远超云端 API 网关约 60s 超时。若在 HTTP 请求内
    同步 await，会触发网关 504 断连，前端误报“重新生成失败”，而后台任务其实仍在运行。
    因此改为后台任务：/regenerate 立即返回，最终结果经 `video_scene_regenerated` 消息推送。
    """
    project = main_agent.get_project(project_id)
    if not project:
        websocket = get_reconnecting_websocket(client_id)
        if websocket:
            await websocket.send_json(jsonable_encoder({
                "type": "video_scene_regenerated",
                "data": {
                    "success": False,
                    "scene_number": scene_number,
                    "error": translate(ui_language, "error.project_not_found"),
                },
            }))
        return

    # 登记「正在重新生成」的分镜：只要该集合非空，就会阻塞 merge 步骤。
    if scene_number not in project.regenerating_scene_numbers:
        project.regenerating_scene_numbers.append(scene_number)
    main_agent.save_project_state(project_id)

    result: Dict[str, Any] = {"success": False, "scene_number": scene_number}
    try:
        result = await _regenerate_video_scene(
            project=project,
            project_id=project_id,
            scene_number=scene_number,
            client_id=client_id,
            ui_language=ui_language,
        )
    except Exception as e:
        logger.error(f"Background regenerate video failed: {str(e)}")
        result = {
            "success": False,
            "scene_number": scene_number,
            "error": translate(ui_language, "error.generation_failed", error=str(e)),
        }
    finally:
        if scene_number in project.regenerating_scene_numbers:
            project.regenerating_scene_numbers.remove(scene_number)
        main_agent.save_project_state(project_id)

    # 把最终结果推送给前端（无论成功/失败），前端据此更新缩略图、审核结论或错误提示。
    websocket = get_reconnecting_websocket(client_id)
    if websocket:
        payload = dict(result) if isinstance(result, dict) else {"success": False}
        payload.setdefault("scene_number", scene_number)
        await websocket.send_json(jsonable_encoder({
            "type": "video_scene_regenerated",
            "data": payload,
        }))

    # 该分镜重新生成并通过审核后，若所有分镜都已完成且不再阻塞 merge，
    # 主动重新下发 videos 的 step_complete，让前端重新进入 merge 倒计时并继续。
    review_info = result.get("review") if isinstance(result, dict) else None
    if (
        isinstance(result, dict)
        and result.get("success")
        and isinstance(review_info, dict)
        and review_info.get("approved")
        and client_id
    ):
        project = main_agent.get_project(project_id)
        lang = normalize_locale(getattr(project, "output_language", "zh-CN"))
        await notify_videos_step_complete_if_ready(client_id, project, lang)



@app.post("/skip_scene")
async def skip_scene(
    project_id: str = Form(...),
    scene_number: int = Form(...),
    client_id: Optional[str] = Form(None),
    ui_language: Optional[str] = Form("zh-CN"),
):
    """跳过当前分镜并继续后续流程"""
    ui_language = normalize_locale(ui_language)
    try:
        project = main_agent.get_project(project_id)
        if not project:
            return {"success": False, "error": translate(ui_language, "error.project_not_found")}

        access_error = validate_project_client_access(project_id, client_id)
        if access_error:
            return {"success": False, "error": access_error}

        main_agent.set_project_output_language(project_id, ui_language)
        project = main_agent.get_project(project_id)
        websocket = get_reconnecting_websocket(client_id)
        skip_result = await main_agent.skip_scene(
            project_id=project_id,
            scene_number=scene_number,
            websocket=websocket,
        )

        review_mode = getattr(project, "video_review_mode", "manual")
        if review_mode == "manual" and websocket:
            await main_agent.continue_generate_after_reference_confirmation(
                project_id=project_id,
                websocket=websocket,
                review_mode=review_mode,
                merge_after_videos=False,
                resume=True,
            )

        return {
            "success": True,
            **skip_result,
        }
    except Exception as e:
        logger.error(f"Skip scene failed: {str(e)}")
        return {"success": False, "error": translate(ui_language, "error.step_execute_failed", error=str(e))}


@app.post("/rollback")
async def rollback_step(request: Request):
    """退回步骤并重新生成"""
    try:
        data = await request.json()
        project_id = data.get("project_id")
        target_step = data.get("target_step")
        ui_language = normalize_locale(data.get("ui_language"))
        client_id = data.get("client_id")

        if not project_id or not target_step:
            return {"success": False, "error": translate(ui_language, "error.missing_project_or_target_step")}

        project = main_agent.get_project(project_id)
        if not project:
            return {"success": False, "error": translate(ui_language, "error.project_not_found")}
        access_error = validate_project_client_access(project_id, client_id)
        if access_error:
            return {"success": False, "error": access_error}
        main_agent.set_project_output_language(project_id, ui_language)
        project = main_agent.get_project(project_id)

        #// 步骤顺序
        step_order = ['script', 'reference_image', 'videos', 'merge']
        if target_step not in step_order:
            return {"success": False, "error": translate(ui_language, "error.invalid_step", step=target_step)}

        target_index = step_order.index(target_step)

        # 重置该步骤及之后的所有步骤状态
        # 清空剧本（如果是退回剧本）
        if target_step == 'script':
            project.script = None

        # 清空参考图（如果是退回参考图或更早）
        if target_index <= step_order.index('reference_image'):
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
            project.comic_pdf_url = None
            project.comic_pdf_status = "pending"
            project.comic_pdf_error = None
            # 保留 reference_images（用户上传的原图）

        # 清空视频（如果是退回视频或更早）
        if target_index <= step_order.index('videos'):
            project.videos = []
            project.video_scene_states = {}
            project.next_scene_index = 0
            project.generated_video_seeds = []

        # 清空合成视频（如果是退回合成或更早）
        if target_index <= step_order.index('merge'):
            project.final_video_url = None

        # 更新当前步骤
        project.current_step = target_step
        project.status = f"rolled_back_to_{target_step}"
        project.progress = max(0, target_index * 25)
        # 退回步骤时清空生成中标记，避免快照残留旧阶段导致状态栏误显示「生成中」。
        project.processing_phase = ""
        main_agent.save_project_state(project_id)

        logger.info(f"Project {project_id} rolled back to step: {target_step}")

        return {
            "success": True,
            "target_step": target_step,
            "message": translate(ui_language, "message.rollback.success", step=target_step)
        }

    except Exception as e:
        logger.error(f"Rollback failed: {str(e)}")
        return {"success": False, "error": translate("zh-CN", "error.step_execute_failed", error=str(e))}


@app.post("/project/{project_id}/end")
async def end_project(
    project_id: str,
    client_id: Optional[str] = Form(None),
    reason: Optional[str] = Form("user_end"),
    ui_language: Optional[str] = Form("zh-CN"),
):
    ui_language = normalize_locale(ui_language)
    try:
        access_error = validate_project_cleanup_access(project_id, client_id)
        if access_error:
            return {"success": False, "error": access_error}

        result = await end_project_resources(
            project_id,
            reason=str(reason or "user_end"),
            client_id=client_id,
        )
        return {
            "success": True,
            "project_id": project_id,
            "status": result["status"],
            "message": translate(ui_language, "message.project.ended"),
        }
    except Exception as e:
        logger.error(f"End project failed for {project_id}: {str(e)}")
        return {"success": False, "error": translate(ui_language, "error.project_end_failed", error=str(e))}


# WebSocket连接管理
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
    
    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        # 若同一 client_id 存在旧连接（网关断连后重连的典型情况），先关闭旧连接，
        # 再登记新连接。旧连接后续触发的 disconnect 会因对象不一致而被忽略（见 disconnect）。
        old_ws = self.active_connections.get(client_id)
        if old_ws is not None and old_ws is not websocket:
            try:
                await old_ws.close()
            except Exception:
                pass
        self.active_connections[client_id] = websocket
        websocket_connections[client_id] = websocket
        logger.info(f"WebSocket connected. Client: {client_id}, Total: {len(self.active_connections)}")
        # Tell frontend its server-assigned client_id so HTTP endpoints can target the correct WS.
        try:
            await websocket.send_json(jsonable_encoder({
                "type": "connection",
                "data": {"client_id": client_id}
            }))
        except Exception as e:
            logger.warning(f"Failed to send connection client_id to {client_id}: {str(e)}")
    
    def disconnect(self, client_id: str, websocket: Optional[WebSocket] = None):
        # 仅当断开的正是当前登记的连接时才清理，避免“旧连接的延迟断开事件”
        # 误删同一 client_id 重连后的新连接（会导致新连接静默失效）。
        current = self.active_connections.get(client_id)
        if websocket is not None and current is not None and current is not websocket:
            logger.info(f"Ignore stale disconnect for client {client_id} (superseded by reconnect)")
            return
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        if client_id in websocket_connections:
            del websocket_connections[client_id]
        if client_id in step_confirmations:
            del step_confirmations[client_id]
        asyncio.create_task(schedule_disconnect_project_cleanup(client_id))
        logger.info(f"WebSocket disconnected. Client: {client_id}, Total: {len(self.active_connections)}")
    
    async def send_message(self, client_id: str, message: dict):
        """发送消息到指定客户端，处理连接断开情况"""
        if client_id not in self.active_connections:
            logger.debug(f"Client {client_id} not in active connections, skipping message")
            return
        
        try:
            websocket = self.active_connections[client_id]
            encoded_message = jsonable_encoder(message)
            # 检查连接是否仍然打开
            if websocket.client_state.name == "CONNECTED":
                await websocket.send_json(encoded_message)
            else:
                logger.warning(f"WebSocket for client {client_id} is not connected (state: {websocket.client_state.name})")
                # 清理断开的连接
                self.disconnect(client_id)
        except Exception as e:
            error_msg = str(e)
            # 忽略 "no close frame received or sent" 错误，这是正常的连接关闭
            if "no close frame received or sent" in error_msg:
                logger.info(f"WebSocket connection closed normally for client {client_id}")
            else:
                logger.error(f"Send message failed for client {client_id}: {error_msg}")
            # 发送失败时清理连接
            self.disconnect(client_id)
    
    async def broadcast(self, message: dict):
        """广播消息到所有活跃连接"""
        disconnected_clients = []
        encoded_message = jsonable_encoder(message)
        for client_id, connection in list(self.active_connections.items()):
            try:
                if connection.client_state.name == "CONNECTED":
                    await connection.send_json(encoded_message)
                else:
                    disconnected_clients.append(client_id)
            except Exception as e:
                logger.error(f"Broadcast failed to {client_id}: {str(e)}")
                disconnected_clients.append(client_id)
        
        # 清理断开的连接
        for client_id in disconnected_clients:
            self.disconnect(client_id)

manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket端点 - 支持长链接3600秒"""
    import uuid
    # 云端准入：当 ACCESS_PASSWORD 启用时，WebSocket 也需携带有效准入 Cookie。
    from app.middleware.access_gate import ws_authorized
    if not ws_authorized(websocket):
        await websocket.close(code=1008)
        return
    # 复用前端携带的稳定 client_id：云端网关会在长任务静默期断开 WS，前端自动重连。
    # 只有沿用同一 client_id，正在后台运行的任务推送的进度/完成消息才能到达重连后的连接，
    # 避免 UI 卡在“正在生成”以及后续合成阶段的“未找到项目”。
    requested_client_id = websocket.query_params.get("client_id")
    client_id = requested_client_id.strip() if requested_client_id and requested_client_id.strip() else str(uuid.uuid4())
    await manager.connect(websocket, client_id)
    
    # 初始化确认事件
    step_confirmations[client_id] = asyncio.Event()
    
    try:
        while True:
            # 接收消息 - 设置较长的超时时间
            data = await websocket.receive_json()
            logger.info(f"WebSocket received from {client_id}: {data}")
            
            message_type = data.get("type")
            
            if message_type == "chat":
                # 处理聊天消息
                message = data.get("message", "")
                project_id = data.get("project_id")
                ui_language = data.get("ui_language")
                if project_id:
                    main_agent.set_project_output_language(project_id, ui_language)
                
                response = main_agent.chat_with_user(message, project_id, output_language=ui_language)
                
                await manager.send_message(client_id, {
                    "type": "chat_response",
                    "data": {
                        "message": response,
                        "project_id": project_id
                    }
                })
                
            elif message_type == "execute_step":
                # 执行指定步骤
                project_id = data.get("project_id")
                step = data.get("step")
                ui_language = data.get("ui_language")
                review_mode = data.get("review_mode")
                generation_mode = data.get("generation_mode")
                
                if project_id and step:
                    main_agent.set_project_output_language(project_id, ui_language)
                    main_agent.set_project_video_review_mode(project_id, review_mode)
                    main_agent.set_project_video_generation_mode(project_id, generation_mode)
                    asyncio.create_task(
                        execute_step_with_websocket(client_id, project_id, step, review_mode=review_mode)
                    )
            elif message_type == "set_language":
                project_id = data.get("project_id")
                ui_language = data.get("ui_language")
                if project_id:
                    main_agent.set_project_output_language(project_id, ui_language)
                    
            elif message_type == "confirm_step":
                # 用户确认步骤
                confirmed = data.get("confirmed", True)
                if confirmed:
                    step_confirmations[client_id].set()
                else:
                    # 用户要求重新生成，重置事件
                    step_confirmations[client_id] = asyncio.Event()
                    step_confirmations[client_id].set()
                    
            elif message_type == "confirm_reference_image":
                # 用户确认参考图库
                confirmed = data.get("confirmed", True)
                project_id = data.get("project_id")
                generation_mode = data.get("generation_mode")
                
                if confirmed:
                    # 设置确认事件，继续生成分镜图片
                    step_confirmations[client_id].set()
                    
                    # 异步执行分镜图片生成
                    if project_id:
                        main_agent.set_project_video_generation_mode(project_id, generation_mode)
                        asyncio.create_task(
                            continue_generate_after_reference_confirmation(client_id, project_id)
                        )
                    
                    await manager.send_message(client_id, {
                        "type": "status",
                        "data": {
                            "agent": "image_agent",
                            "message": translate(ui_language, "message.reference.confirmed_start_videos")
                        }
                    })
                else:
                    # 用户要求重新生成参考图库
                    if project_id:
                        # 重新生成参考图库
                        asyncio.create_task(
                            regenerate_reference_image(client_id, project_id)
                        )
                    
            elif message_type == "confirm_reference_stage":
                # 用户确认某个参考图子阶段（category1/category2/category3）
                confirmed = data.get("confirmed", True)
                project_id = data.get("project_id")
                stage = str(data.get("stage") or "").strip()
                generation_mode = data.get("generation_mode")

                if not project_id or stage not in ("category1", "category2", "category3"):
                    await manager.send_message(client_id, {
                        "type": "error",
                        "data": {"message": translate(ui_language, "error.invalid_step", step=stage or "?")}
                    })
                elif confirmed:
                    project = main_agent.get_project(project_id)
                    has_category2 = bool(main_agent._reference_stage_has_category2(project)) if project else False
                    next_stage = _compute_next_reference_stage(stage, has_category2)
                    if next_stage == "videos":
                        # category3 已确认：进入视频生成（复用既有流程）。
                        main_agent.set_project_video_generation_mode(project_id, generation_mode)
                        asyncio.create_task(
                            continue_generate_after_reference_confirmation(client_id, project_id)
                        )
                        await manager.send_message(client_id, {
                            "type": "status",
                            "data": {
                                "agent": "image_agent",
                                "message": translate(ui_language, "message.reference.confirmed_start_videos")
                            }
                        })
                    else:
                        # 推进下一子阶段（category2 或 category3）。
                        asyncio.create_task(
                            execute_reference_stage(client_id, project_id, next_stage)
                        )
                else:
                    # 用户要求重跑当前阶段（不清前阶段）。
                    asyncio.create_task(
                        execute_reference_stage(client_id, project_id, stage)
                    )

            elif message_type == "ping":
                # 心跳响应
                try:
                    await manager.send_message(client_id, {"type": "pong"})
                except Exception as e:
                    logger.warning(f"Failed to send pong to {client_id}: {str(e)}")
                    # 如果发送失败，断开连接
                    break
                
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected normally: {client_id}")
        manager.disconnect(client_id, websocket)
    except Exception as e:
        logger.error(f"WebSocket error for {client_id}: {str(e)}")
        manager.disconnect(client_id, websocket)


async def execute_step_with_websocket(client_id: str, project_id: str, step: str, review_mode: Optional[str] = None):
    """执行步骤并通过WebSocket发送更新"""
    websocket = get_reconnecting_websocket(client_id)
    if not websocket:
        logger.error(f"WebSocket not found for client {client_id}")
        return

    access_error = validate_project_client_access(project_id, client_id)
    if access_error:
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": access_error}
        })
        return
    
    project = main_agent.get_project(project_id)
    if not project:
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": translate("zh-CN", "error.project_not_found")}
        })
        return
    lang = normalize_locale(getattr(project, "output_language", "zh-CN"))
    
    try:
        # 根据步骤执行相应的Agent
        if step == "script":
            await execute_script_step(client_id, project_id)
        elif step == "reference_image":
            # 只执行参考图生成（第一步）
            await execute_reference_image_step(client_id, project_id)
        elif step == "images":
            # 兼容旧逻辑：执行完整的图片生成（参考图+分镜图）
            await execute_images_step(client_id, project_id)
        elif step == "videos":
            await execute_videos_step(client_id, project_id, review_mode=review_mode)
        elif step == "merge":
            await execute_merge_step(client_id, project_id)
        else:
            await manager.send_message(client_id, {
                "type": "error",
                "data": {"message": translate(lang, "error.step_execute_failed", error=f"unknown step: {step}")}
            })
            
    except Exception as e:
        logger.error(f"Step execution failed: {str(e)}")
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": translate(lang, "error.step_execute_failed", error=str(e))}
        })


async def execute_script_step(client_id: str, project_id: str):
    """执行剧本生成步骤"""
    project = main_agent.get_project(project_id)
    lang = normalize_locale(getattr(project, "output_language", "zh-CN"))
    # 阶段开始即置位并持久化：云端多实例下即使本次 status/progress 推送丢失，
    # 其它实例的快照对账也能据此补出「剧本生成中」的底部状态栏。
    project.processing_phase = "script"
    main_agent.save_project_state(project_id)
    await manager.send_message(client_id, {
        "type": "status",
        "data": {"agent": "script_agent", "message": translate(lang, "progress.script.generating")}
    })
    
    await manager.send_message(client_id, {
        "type": "progress",
        "data": {"agent": "script_agent", "progress": 10, "message": translate(lang, "progress.script.generating")}
    })
    
    # 生成剧本
    script = await main_agent._generate_script(main_agent.get_project(project_id))
    project = main_agent.get_project(project_id)
    project.script = script
    project.current_step = "script_generated"
    project.status = "script_generated"
    project.progress = max(project.progress, 25)
    # 剧本已产出：清空生成中标记，交由数据驱动的对账补齐剧本卡片。
    project.processing_phase = ""
    main_agent.save_project_state(project_id)
    
    await manager.send_message(client_id, {
        "type": "progress",
        "data": {"agent": "script_agent", "progress": 100, "message": translate(lang, "progress.script.completed")}
    })
    
    await manager.send_message(client_id, {
        "type": "agent_output",
        "data": {"agent": "script_agent", "output": script.dict()}
    })
    
    # 通知步骤完成，等待用户确认
    await manager.send_message(client_id, {
        "type": "step_complete",
        "data": {
            "step": "script",
            "message": translate(lang, "step.script.complete")
        }
    })


async def execute_images_step(client_id: str, project_id: str):
    """执行图片生成步骤（两步流程：先生成参考图库，用户确认后再生成分镜）"""
    project = main_agent.get_project(project_id)
    lang = normalize_locale(getattr(project, "output_language", "zh-CN"))
    
    # ========== 第一步：生成参考图库==========
    # 阶段开始即置位并持久化，兜底云端多实例下的推送丢失（右侧空白/状态栏空白）。
    project.processing_phase = "reference_category1"
    main_agent.save_project_state(project_id)
    await manager.send_message(client_id, {
        "type": "status",
        "data": {"agent": "image_agent", "message": translate(lang, "progress.reference.generating")}
    })
    
    await manager.send_message(client_id, {
        "type": "progress",
        "data": {"agent": "image_agent", "progress": 30, "message": translate(lang, "progress.reference.generating")}
    })
    
    async def push_reference_library_progress(updated_project):
        await manager.send_message(client_id, {
            "type": "agent_output",
            "data": {
                "agent": "image_agent",
                "output": main_agent._build_reference_output(updated_project)
            }
        })

    # 逐张生成并推送统一参考图库
    reference_image = await main_agent.generate_reference_image_with_retry(
        project,
        progress_callback=push_reference_library_progress,
    )
    project.reference_image = reference_image
    # 参考图库已产出：清空生成中标记，后续由数据驱动的对账补齐右侧参考图库。
    project.processing_phase = ""
    
    await manager.send_message(client_id, {
        "type": "progress",
        "data": {"agent": "image_agent", "progress": 35, "message": translate(lang, "progress.reference.completed_wait")}
    })
    
    await manager.send_message(client_id, {
        "type": "agent_output",
        "data": {
            "agent": "image_agent",
            "output": main_agent._build_reference_output(project, translate(lang, "message.reference.confirm_prompt"))
        }
    })

    main_agent.save_project_state(project_id)
    
    # 等待用户确认参考图库
    logger.info(f"Waiting for user confirmation of reference library for client {client_id}")
    
    # 重置确认事件
    step_confirmations[client_id] = asyncio.Event()
    
    # 通知前端等待用户确认
    await manager.send_message(client_id, {
        "type": "step_complete",
        "data": {
            "step": "reference_image",
            "message": translate(lang, "step.reference.complete"),
            "require_confirmation": True,
            "confirmation_type": "reference_image"
        }
    })
    
    # 等待用户确认（超时3600秒）
    try:
        await asyncio.wait_for(step_confirmations[client_id].wait(), timeout=3600)
    except asyncio.TimeoutError:
        logger.warning(f"Timeout waiting for reference image confirmation for client {client_id}")
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": translate(lang, "error.confirmation_timeout")}
        })
        return
    
    # 检查项目状态
    if project.status == "cancelled":
        logger.info(f"Project {project_id} was cancelled")
        return
    
    # ========== 第二步：直接生成视频（简化流程，跳过分镜图生成）==========
    await execute_videos_step(client_id, project_id)


async def execute_reference_image_step(client_id: str, project_id: str):
    """只执行参考图生成第一子阶段（category1：人物/角色图库 + 布景参考图库）。

    参考图内部改造为严格串行三子阶段：category1 → category2（可选）→ category3。
    每个子阶段完成后等待用户确认（手动）或倒计时（自动）再推进下一阶段。
    """
    await execute_reference_stage(client_id, project_id, "category1")


# 参考图子阶段元信息：progress 文案、完成文案、确认提示、下一确认阶段进度值
_REFERENCE_STAGE_META = {
    "category1": {
        "progress": 30,
        "completed_progress": 33,
        "completed_wait_key": "progress.reference.category1_completed_wait",
        "confirm_prompt_key": "message.reference.category1_confirm_prompt",
        "step_complete_key": "step.reference.category1_complete",
    },
    "category2": {
        "progress": 34,
        "completed_progress": 36,
        "completed_wait_key": "progress.reference.category2_completed_wait",
        "confirm_prompt_key": "message.reference.category2_confirm_prompt",
        "step_complete_key": "step.reference.category2_complete",
    },
    "category3": {
        "progress": 37,
        "completed_progress": 40,
        "completed_wait_key": "progress.reference.category3_completed_wait",
        "confirm_prompt_key": "message.reference.category3_confirm_prompt",
        "step_complete_key": "step.reference.category3_complete",
    },
}


def _compute_next_reference_stage(stage: str, has_category2: bool) -> str:
    """依据当前完成阶段与是否存在分类2，计算下一目标：category2/category3/videos。"""
    if stage == "category1":
        return "category2" if has_category2 else "category3"
    if stage == "category2":
        return "category3"
    return "videos"


def _current_reference_stage(project) -> str:
    """由 project.reference_stage（{stage}_done）推导“当前所处子阶段”，用于单张重生成输出。"""
    done = str(getattr(project, "reference_stage", "none") or "none")
    if done.startswith("category3"):
        return "category3"
    if done.startswith("category2"):
        return "category2"
    return "category1"


async def execute_reference_stage(client_id: str, project_id: str, stage: str):
    """通用分阶段参考图生成执行器（category1/category2/category3）。

    按 stage 调 generate_reference_stage_with_retry，发送 progress/agent_output/step_complete
    （均携带 reference_stage）。完成后 save_project_state 并将 reference_stage 标记为 {stage}_done。
    若为 category2 但实际无装扮/状态差异（has_category2=False），直接跳到 category3。
    """
    project = main_agent.get_project(project_id)
    lang = normalize_locale(getattr(project, "output_language", "zh-CN"))
    meta = _REFERENCE_STAGE_META.get(stage, _REFERENCE_STAGE_META["category1"])

    # category2 跳过保护：若实际无分类2资产，直接推进 category3，避免卡死。
    if stage == "category2" and not main_agent._reference_stage_has_category2(project):
        logger.info(f"[REF-STAGE] project {project_id} has no category2 assets, skipping to category3")
        await execute_reference_stage(client_id, project_id, "category3")
        return

    # 阶段开始即置位并持久化：进入某参考图子阶段（含无数据的生成窗口）时，
    # 云端多实例下 status/agent_output 推送可能丢失导致右侧子模块与状态栏空白，
    # 前端据此标记补出「XXX 生成中」占位与状态栏。
    project.processing_phase = f"reference_{stage}"
    main_agent.save_project_state(project_id)
    await manager.send_message(client_id, {
        "type": "status",
        "data": {"agent": "image_agent", "message": translate(lang, "progress.reference.generating")}
    })
    await manager.send_message(client_id, {
        "type": "progress",
        "data": {"agent": "image_agent", "progress": meta["progress"], "message": translate(lang, "progress.reference.generating")}
    })

    async def push_reference_library_progress(updated_project):
        await manager.send_message(client_id, {
            "type": "agent_output",
            "data": {
                "agent": "image_agent",
                "output": main_agent._build_reference_output(updated_project, stage=stage)
            }
        })

    try:
        await main_agent.generate_reference_stage_with_retry(
            project,
            stage=stage,
            progress_callback=push_reference_library_progress,
        )
    except Exception as e:
        logger.error(f"[REF-STAGE] stage {stage} generation failed for project {project_id}: {str(e)}")
        # 失败清空生成中标记，避免右侧占位/状态栏长期悬挂在「生成中」。
        project.processing_phase = ""
        main_agent.save_project_state(project_id)
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": translate(lang, "error.generation_failed", error=str(e))}
        })
        return

    await manager.send_message(client_id, {
        "type": "progress",
        "data": {"agent": "image_agent", "progress": meta["completed_progress"], "message": translate(lang, meta["completed_wait_key"])}
    })

    await manager.send_message(client_id, {
        "type": "agent_output",
        "data": {
            "agent": "image_agent",
            "output": main_agent._build_reference_output(
                project,
                translate(lang, meta["confirm_prompt_key"]),
                stage=stage,
            )
        }
    })

    # 各子阶段完成后持久化子阶段进度，供跨实例断线恢复。
    project.reference_stage = f"{stage}_done"
    # 子阶段已产出：清空生成中标记，后续由数据驱动的对账补齐右侧子模块。
    project.processing_phase = ""
    main_agent.save_project_state(project_id)

    has_category2 = main_agent._reference_stage_has_category2(project)
    await manager.send_message(client_id, {
        "type": "step_complete",
        "data": {
            "step": "reference_image",
            "reference_stage": stage,
            "has_category2": has_category2,
            "require_confirmation": True,
            "confirmation_type": "reference_stage",
            "message": translate(lang, meta["step_complete_key"])
        }
    })


async def continue_generate_after_reference_confirmation(client_id: str, project_id: str, review_mode: Optional[str] = None):
    """用户确认参考图后执行新流程：首分镜视频 -> 审核 -> 延伸视频 -> 审核 -> 等待进入合成"""
    logger.info(f"[FLOW] Continuing generation after reference image confirmation for project {project_id}")

    access_error = validate_project_client_access(project_id, client_id)
    if access_error:
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": access_error}
        })
        return

    project = main_agent.get_project(project_id)
    if not project:
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": translate("zh-CN", "error.project_not_found")}
        })
        return
    lang = normalize_locale(getattr(project, "output_language", "zh-CN"))

    if not hasattr(project, 'reference_image') or not project.reference_image:
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": translate(lang, "error.reference_missing")}
        })
        return

    # 新流程：使用 MainAgent 的 continue_generate_after_reference_confirmation 方法
    # 该方法会逐个生成分镜视频并审核
    websocket = get_reconnecting_websocket(client_id)

    try:
        logger.info(f"[FLOW] Starting new video generation flow with review for project {project_id}")
        resume = bool(getattr(project, "videos", None)) or getattr(project, "next_scene_index", 0) > 0
        # 视频阶段开始即置位并持久化：即使首个分镜尚无 URL，
        # 云端多实例下前端也能据此立即渲染视频模块与状态栏。
        project.processing_phase = "videos"
        main_agent.save_project_state(project_id)
        await main_agent.continue_generate_after_reference_confirmation(
            project_id=project_id,
            websocket=websocket,
            review_mode=review_mode,
            merge_after_videos=False,
            resume=resume,
        )
        project = main_agent.get_project(project_id)
        # 视频阶段结束：清空生成中标记（分镜数据本身已由数据驱动对账补齐）。
        project.processing_phase = ""
        main_agent.save_project_state(project_id)
        await notify_videos_step_complete_if_ready(client_id, project, lang)
        logger.info(f"[FLOW] Video generation flow completed for project {project_id}")
    except Exception as e:
        logger.error(f"[FLOW] Video generation flow failed: {str(e)}")
        # 失败也清空生成中标记，避免状态栏「生成中」长期悬挂。
        try:
            failed_project = main_agent.get_project(project_id)
            if failed_project:
                failed_project.processing_phase = ""
                main_agent.save_project_state(project_id)
        except Exception:
            pass
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": translate(lang, "error.video_generation_failed", error=str(e))}
        })


async def regenerate_reference_asset_background(
    client_id: str,
    project_id: str,
    ui_language: str,
    reference_type: str,
    reference_name: str,
    reference_slot_index: Optional[int] = None,
):
    """后台重新生成单张参考图/角色装扮图/布景状态图/故事版，并通过 WebSocket 推送结果。

    云端 API 网关存在约 60s 超时，而单张图片重生成耗时可达 40~60s，
    若在 HTTP 请求内同步 await 会触发网关断连，前端 fetch 抛错误报“重新生成失败”。
    因此改为后台任务执行，立即返回，最终结果统一通过 WebSocket 推送。
    """
    ui_language = normalize_locale(ui_language)
    normalized_reference_type = str(reference_type or "").strip().lower()
    normalized_reference_name = str(reference_name or "").strip()
    # 与前端 buildReferenceAssetKey 保持一致（type::name），用于精确解锁对应按钮。
    reference_asset_key = f"{normalized_reference_type}::{normalized_reference_name}"

    project = main_agent.get_project(project_id)
    if not project:
        await manager.send_message(client_id, {
            "type": "reference_asset_regenerated",
            "data": {
                "success": False,
                "reference_asset_key": reference_asset_key,
                "error": translate(ui_language, "error.project_not_found"),
            }
        })
        return

    try:
        if normalized_reference_type == "storyboard":
            try:
                target_scene_number = int(float(normalized_reference_name))
            except (TypeError, ValueError):
                target_scene_number = 0
            new_image = await main_agent.regenerate_storyboard_asset(
                project,
                scene_number=target_scene_number,
                feedback="用户要求重新生成",
            )
            logger.info(f"Regenerated storyboard for scene {target_scene_number}: {new_image.url}")
        elif normalized_reference_type in {"character_outfit", "scene_state", "key_action"}:
            new_image = await main_agent.regenerate_variant_asset(
                project,
                reference_type=normalized_reference_type,
                variant_key=normalized_reference_name,
                feedback="用户要求重新生成",
            )
            logger.info(
                f"Regenerated {normalized_reference_type} variant {normalized_reference_name}: {new_image.url}"
            )
        else:
            new_image = await main_agent.regenerate_reference_asset(
                project,
                reference_type=normalized_reference_type,
                asset_name=normalized_reference_name,
                reference_slot_index=reference_slot_index,
                feedback="用户要求重新生成",
            )
            logger.info(f"Regenerated reference asset {normalized_reference_name}: {new_image.url}")

        project.status = "waiting_reference_confirmation"
        project.current_step = "waiting_reference_confirmation"

        # 携带当前子阶段，使前端 stage_ready/has_category2 正确，保证子阶段倒计时能重启。
        current_stage = _current_reference_stage(project)
        await manager.send_message(client_id, {
            "type": "reference_asset_regenerated",
            "data": {
                "success": True,
                "reference_asset_key": reference_asset_key,
                "reference_type": getattr(new_image, "reference_type", None),
                "reference_name": getattr(new_image, "name", None),
                "variant_key": getattr(new_image, "variant_key", None),
                "reference_output": main_agent._build_reference_output(
                    project, translate(ui_language, "message.reference.regenerated_confirm_prompt"),
                    stage=current_stage,
                ),
            }
        })
    except Exception as e:
        logger.error(f"Background regenerate reference asset failed: {str(e)}")
        await manager.send_message(client_id, {
            "type": "reference_asset_regenerated",
            "data": {
                "success": False,
                "reference_asset_key": reference_asset_key,
                "error": translate(ui_language, "error.generation_failed", error=str(e)),
            }
        })


async def regenerate_reference_image(client_id: str, project_id: str):
    """重新生成参考图库"""
    logger.info(f"Regenerating reference image for project {project_id}")

    access_error = validate_project_client_access(project_id, client_id)
    if access_error:
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": access_error}
        })
        return
    
    project = main_agent.get_project(project_id)
    if not project:
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": translate("zh-CN", "error.project_not_found")}
        })
        return
    lang = normalize_locale(getattr(project, "output_language", "zh-CN"))
    
    try:
        await manager.send_message(client_id, {
            "type": "status",
            "data": {"agent": "image_agent", "message": translate(lang, "progress.reference.regenerating")}
        })
        
        await manager.send_message(client_id, {
            "type": "progress",
            "data": {"agent": "image_agent", "progress": 30, "message": translate(lang, "progress.reference.regenerating")}
        })
        
        # 重新生成参考图库
        reference_image = await main_agent._regenerate_reference_image(
            project,
            feedback="用户要求重新生成"
        )

        replaced = False
        for i, image in enumerate(project.images):
            if image.scene_number == 0:
                project.images[i] = reference_image
                replaced = True
                break
        if not replaced:
            project.images.insert(0, reference_image)

        project.reference_image = reference_image

        logger.info(f"Reference image regenerated: {reference_image.url}")
        logger.info(f"Reference image is_reference: {getattr(reference_image, 'is_reference', False)}")

        await manager.send_message(client_id, {
            "type": "progress",
            "data": {"agent": "image_agent", "progress": 35, "message": translate(lang, "progress.reference.recompleted")}
        })
        
        await manager.send_message(client_id, {
            "type": "agent_output",
            "data": {
                "agent": "image_agent",
                "output": main_agent._build_reference_output(project, translate(lang, "message.reference.regenerated_confirm_prompt"))
            }
        })
        
    except Exception as e:
        logger.error(f"Failed to regenerate reference image: {str(e)}")
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": translate(lang, "error.generation_failed", error=str(e))}
        })


async def execute_videos_step(client_id: str, project_id: str, review_mode: Optional[str] = None):
    """执行视频生成步骤 - 简化版本，直接使用参考图"""
    project = main_agent.get_project(project_id)
    lang = normalize_locale(getattr(project, "output_language", "zh-CN"))

    # 检查是否有参考图
    if not hasattr(project, 'reference_image') or not project.reference_image:
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": translate(lang, "error.reference_missing")}
        })
        return

    websocket = get_reconnecting_websocket(client_id)

    # 复用统一的新流程：分镜视频生成失败与审核失败共享同一套重试次数，
    # 但此入口只执行到“视频完成”，不自动进入合成步骤。
    await main_agent.continue_generate_after_reference_confirmation(
        project_id=project_id,
        websocket=websocket,
        review_mode=review_mode,
        merge_after_videos=False,
        resume=True,
    )

    project = main_agent.get_project(project_id)
    await notify_videos_step_complete_if_ready(client_id, project, lang)


async def execute_merge_step(client_id: str, project_id: str):
    """执行视频合成步骤"""
    project = main_agent.get_project(project_id)
    lang = normalize_locale(getattr(project, "output_language", "zh-CN"))

    total_scenes = len(getattr(getattr(project, "script", None), "scenes", []) or [])
    completed_videos = len(getattr(project, "videos", None) or [])
    next_scene_index = int(getattr(project, "next_scene_index", 0) or 0)
    if total_scenes > 0 and (completed_videos < total_scenes or next_scene_index < total_scenes):
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": translate(lang, "error.merge_before_all_scenes_completed")}
        })
        logger.warning(
            f"Rejected premature merge for project {project_id}: "
            f"videos={completed_videos}/{total_scenes}, next_scene_index={next_scene_index}"
        )
        return

    # 有分镜正在「重新生成」或重新生成后尚未通过审核时，阻塞合成，等其完成并通过审核。
    if _scene_regeneration_blocks_merge(project):
        await manager.send_message(client_id, {
            "type": "error",
            "data": {"message": translate(lang, "error.merge_before_all_scenes_completed")}
        })
        logger.warning(
            f"Rejected merge for project {project_id}: scene regeneration/review still pending "
            f"(regenerating={list(getattr(project, 'regenerating_scene_numbers', []) or [])})"
        )
        return

    await manager.send_message(client_id, {
        "type": "status",
        "data": {"agent": "merge_agent", "message": translate(lang, "progress.merge.generating")}
    })

    # 合成阶段开始即置位并持久化：兜底云端多实例下的推送丢失（状态栏空白）。
    project.processing_phase = "merge"
    main_agent.save_project_state(project_id)

    await manager.send_message(client_id, {
        "type": "progress",
        "data": {"agent": "merge_agent", "progress": 90, "message": translate(lang, "progress.merge.generating")}
    })

    # 合成视频
    final_url = await main_agent._merge_videos(project)
    project.final_video_url = final_url
    project.status = "completed"
    project.processing_phase = ""
    main_agent.save_project_state(project_id)

    await manager.send_message(client_id, {
        "type": "progress",
        "data": {"agent": "merge_agent", "progress": 100, "message": translate(lang, "progress.merge.completed")}
    })

    await manager.send_message(client_id, {
        "type": "agent_output",
        "data": {
            "agent": "merge_agent",
            "output": {"final_video_url": final_url}
        }
    })

    # 通知所有步骤完成
    await manager.send_message(client_id, {
        "type": "step_complete",
        "data": {
            "step": "merge",
            "message": translate(lang, "message.all_videos_completed")
        }
    })

    # 清理TOS中的临时文件（图片和录音）
    await cleanup_project_files(project)


async def cleanup_project_files(
    project,
    *,
    keep_prefixes: Optional[list[str]] = None,
    cleanup_asset_library: bool = False,
):
    """
    清理项目相关的临时文件
    - TOS 中仅保留角色/场景参考图、分镜视频、合成视频
    - 完全删除本地任务 temp 子目录
    """
    logger.info(f"Starting cleanup for project {project.project_id}")
    try:
        tos_service.cleanup_project_directory(
            project.project_id,
            keep_prefixes=keep_prefixes or [
                "references/characters",
                "references/scenes",
                "videos/scenes",
                "videos/final",
                "documents/comics",
            ],
        )
    except Exception as e:
        logger.error(f"TOS cleanup failed for project {project.project_id}: {str(e)}")

    try:
        cleanup_project_temp_dir(project.project_id)
    except Exception as e:
        logger.error(f"Local temp cleanup failed for project {project.project_id}: {str(e)}")

    if cleanup_asset_library and getattr(project, "asset_group_id", None):
        try:
            await run_generation(
                asset_library_service.cleanup_asset_group,
                group_id=project.asset_group_id,
                project_name=getattr(project, "asset_project_name", None),
            )
            project.asset_group_id = None
            project.asset_group_name = None
        except Exception as e:
            logger.error(f"Asset library cleanup failed for project {project.project_id}: {str(e)}")

    logger.info(f"Cleanup completed for project {project.project_id}")


if __name__ == "__main__":
    server_port = int(config.get("server.port", 8888))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=server_port,
        reload=True,
        log_level="info",
        ws_ping_interval=30,  # WebSocket心跳间隔30秒
        ws_ping_timeout=3600  # WebSocket超时3600秒（1小时）
    )
