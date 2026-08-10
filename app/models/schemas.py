# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime
from enum import Enum

class MessageType(str, Enum):
    """消息类型"""
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"

class AgentType(str, Enum):
    """Agent类型"""
    MAIN = "main_agent"
    SCRIPT = "script_agent"
    IMAGE = "image_agent"
    VIDEO = "video_agent"
    VERIFY = "verify_agent"
    MERGE = "merge_agent"

class AgentStatus(str, Enum):
    """Agent状态"""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"

class Scene(BaseModel):
    """分镜数据模型"""
    scene_number: int
    scene_name: str
    description: str
    dialogue: str
    duration: int  # 秒
    character_description: str
    voice_description: str
    mood: str
    camera_angle: Optional[str] = None
    characters_present: Optional[List[str]] = None  # 该分镜中出现的角色列表（可以是多个、单个、空列表）


class SceneDefinition(BaseModel):
    """布景设定"""
    name: str
    description: str


class Character(BaseModel):
    """角色数据模型"""
    name: str
    age: str  # 改为字符串类型，支持如"外表16岁"、"中年"等描述
    gender: str
    face_features: str
    skin_tone: str
    clothing: Optional[str] = None
    voice_type: str
    voice_features: str
    voice_style: str

class Script(BaseModel):
    """剧本数据模型"""
    title: str
    style: str
    background: str
    characters: List[Character]
    scene_definitions: List[SceneDefinition] = Field(default_factory=list)
    scenes: List[Scene]
    total_duration: int
    
class GeneratedImage(BaseModel):
    """生成的图片数据模型"""
    scene_number: int  # 0表示参考图库图片，1-N表示分镜，999表示结尾帧
    url: str
    prompt: str
    name: Optional[str] = None
    reference_type: Optional[Literal["character", "scene", "first_frame"]] = None
    source: str = "generated"
    used_original: bool = False
    is_end_frame: bool = False
    is_reference: bool = False  # 是否是参考图库图片
    regenerate_locked: bool = False
    asset_id: Optional[str] = None
    asset_status: Optional[str] = None
    created_at: datetime = datetime.now()


class UploadedReferenceImage(BaseModel):
    """用户上传的参考图元信息"""
    url: str
    name: str
    reference_type: Literal["character", "scene"]
    asset_id: Optional[str] = None
    asset_status: Optional[str] = None
    created_at: datetime = datetime.now()

class GeneratedVideo(BaseModel):
    """生成的视频片段数据模型"""
    scene_number: int
    url: str
    first_frame_url: str
    last_frame_url: Optional[str] = None  # 只有最后一个分镜使用尾帧
    duration: int
    prompt: str
    seed: Optional[str] = None
    status: str = "completed"
    created_at: datetime = datetime.now()


class VideoSceneState(BaseModel):
    """单个分镜的视频生成与审核状态"""
    scene_number: int
    total_generation_count: int = 0
    auto_retry_count: int = 0
    manual_regeneration_count: int = 0
    archive_generation_count: int = 0
    generation_failure_count: int = 0
    best_video: Optional[GeneratedVideo] = None
    best_score: int = -1
    best_feedback: str = ""
    last_video: Optional[GeneratedVideo] = None
    last_score: int = 0
    last_feedback: str = ""
    approved: bool = False
    completed: bool = False

class VideoProject(BaseModel):
    """视频项目数据模型"""
    project_id: str
    user_input: str
    reference_images: List[str] = Field(default_factory=list)
    audio_url: Optional[str] = None
    script: Optional[Script] = None
    images: List[GeneratedImage] = Field(default_factory=list)
    videos: List[GeneratedVideo] = Field(default_factory=list)
    final_video_url: Optional[str] = None
    status: str = "pending"
    current_step: str = "init"
    progress: int = 0
    created_at: datetime = datetime.now()
    updated_at: datetime = datetime.now()
    combined_input: Optional[str] = None
    aspect_ratio: Optional[str] = None  # 用户指定的图片/视频比例，如 "16:9", "4:3" 等
    video_resolution: Optional[str] = None  # 用户指定或回落后的统一视频分辨率，如 "480p", "720p"
    output_language: str = "zh-CN"
    use_original_reference: bool = False
    uploaded_reference_images: List[UploadedReferenceImage] = Field(default_factory=list)
    character_reference_images: List[GeneratedImage] = Field(default_factory=list)
    scene_reference_images: List[GeneratedImage] = Field(default_factory=list)
    reference_image_library: Dict[str, List[GeneratedImage]] = Field(default_factory=dict)
    task_tos_prefix: Optional[str] = None
    task_temp_dir: Optional[str] = None
    asset_group_id: Optional[str] = None
    asset_group_name: Optional[str] = None
    asset_project_name: Optional[str] = None
    is_ended: bool = False
    end_reason: Optional[str] = None
    # 两步图片生成相关字段
    reference_image: Optional[GeneratedImage] = None  # 参考图库主图
    video_review_mode: str = "manual"
    next_scene_index: int = 0
    video_scene_states: Dict[int, VideoSceneState] = Field(default_factory=dict)
    generated_video_seeds: List[str] = Field(default_factory=list)

class ChatMessage(BaseModel):
    """聊天消息数据模型"""
    type: Literal["user", "agent"]
    content: str
    images: List[str] = []
    audio_url: Optional[str] = None
    timestamp: datetime = datetime.now()

class WebSocketMessage(BaseModel):
    """WebSocket消息数据模型"""
    type: str  # chat_message, agent_status, agent_output, progress, error
    data: Dict[str, Any]
    timestamp: datetime = datetime.now()

class AgentOutput(BaseModel):
    """Agent输出数据模型"""
    agent: AgentType
    output_type: str  # script, image, video, final_video
    data: Dict[str, Any]
    status: AgentStatus
    message: Optional[str] = None

class UploadResponse(BaseModel):
    """文件上传响应"""
    success: bool
    url: Optional[str] = None
    filename: Optional[str] = None
    error: Optional[str] = None

class ASRResponse(BaseModel):
    """ASR识别响应"""
    success: bool
    text: Optional[str] = None
    error: Optional[str] = None
