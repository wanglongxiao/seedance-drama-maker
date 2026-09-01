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
    # 本分镜的显著布景状态描述，仅描述时间/天气，如“深夜雷暴，窗外闪电频繁照亮破败卧室”
    scene_state: Optional[str] = None
    description: str
    dialogue: str
    duration: int  # 秒
    character_description: str
    voice_description: str
    mood: str
    time_of_day: str = ""
    weather: str = ""
    camera_angle: Optional[str] = None
    characters_present: Optional[List[str]] = None  # 该分镜中出现的角色列表（可以是多个、单个、空列表）
    # 每个出场角色在本分镜中的装扮描述，如 {"林晚": "舞会盛装", "陈默": "身穿盔甲"}
    # 仅当角色装扮与其默认装扮(Character.clothing)不同时才需要给出对应条目
    character_outfits: Optional[Dict[str, str]] = None


class SceneDefinition(BaseModel):
    """布景设定"""
    name: str
    description: str
    time_of_day: str = ""
    weather: str = ""
    scene_features: List[str] = Field(default_factory=list)


class Character(BaseModel):
    """角色数据模型"""
    name: str
    age: str  # 改为字符串类型，支持如"外表16岁"、"中年"等描述
    gender: str
    nationality: Optional[str] = None
    face_features: str
    hairstyle: Optional[str] = None
    body_features: Optional[str] = None
    skin_tone: str
    clothing: Optional[str] = None
    voice_type: str
    voice_features: str
    voice_style: str
    # 角色性格侧写：简要的性格特征，体现真实性与人性复杂性；主角尤其需要
    personality: Optional[str] = None
    # 角色身份背景：职业、社会身份、来历、关系网络等，用于后续生图/生视频保持角色一致性
    identity_background: Optional[str] = None

class Script(BaseModel):
    """剧本数据模型"""
    title: str
    style: str
    # 剧本所处的时代/年代，如 上古洪荒/古代/民国/现代都市/近未来/架空异世界 等
    era: Optional[str] = None
    background: str
    # 剧本背景基调，如 恐怖/爱情/悬疑/爽剧/历史/情欲 等更细分的基调
    tone: Optional[str] = None
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
    reference_type: Optional[Literal["character", "scene", "character_outfit", "scene_state", "key_action", "storyboard"]] = None
    variant_key: Optional[str] = None  # 装扮图/布景状态图去重键，如 "角色key::装扮key" 或 "场景key::时间key::天气key"
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
    accepted_over_retry: bool = False
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
    comic_pdf_url: Optional[str] = None
    comic_pdf_status: str = "pending"
    comic_pdf_error: Optional[str] = None
    status: str = "pending"
    current_step: str = "init"
    progress: int = 0
    # 单调递增的状态版本号：每次 save_project_state 前自增，用于云端多实例下判断
    # 「哪个副本更新」。只读的 /restore 快照会据此在 TOS 更新时回源，避免命中持有
    # 陈旧内存态的实例导致右侧产出长期不显示（本地单实例不复现）。
    state_version: int = 0
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
    character_outfit_images: List[GeneratedImage] = Field(default_factory=list)
    scene_state_images: List[GeneratedImage] = Field(default_factory=list)
    key_action_reference_images: List[GeneratedImage] = Field(default_factory=list)
    storyboard_images: List[GeneratedImage] = Field(default_factory=list)
    reference_image_library: Dict[str, Any] = Field(default_factory=dict)
    scene_reference_mappings: Dict[int, Dict[str, Any]] = Field(default_factory=dict)
    task_tos_prefix: Optional[str] = None
    task_temp_dir: Optional[str] = None
    asset_group_id: Optional[str] = None
    asset_group_name: Optional[str] = None
    asset_project_name: Optional[str] = None
    is_ended: bool = False
    end_reason: Optional[str] = None
    # 两步图片生成相关字段
    reference_image: Optional[GeneratedImage] = None  # 参考图库主图
    # 参考图分阶段生成进度：none/category1_done/category2_done/category3_done
    reference_stage: str = "none"
    # 当前正在执行（生成中）的阶段标记，供云端多实例下前端权威对账。
    # 云端多实例时，进入某阶段的实时 status/progress/agent_output 推送可能落在其它
    # 实例而丢失，导致「进入某阶段后右侧内容与底部状态栏空白」（本地单实例不复现）。
    # 该字段在每个阶段「开始」时置位、「产出数据或完成」后清空，随项目状态持久化到 TOS，
    # 前端据此即使在「已进入但尚无数据」的生成窗口也能补出状态栏与占位 UI。
    # 取值：""（空闲）/ script / reference_category1 / reference_category2 /
    #      reference_category3 / videos / merge
    processing_phase: str = ""
    video_review_mode: str = "manual"
    # 全自动模式：项目是否处于「一键生成」自动推进流程。
    # 关键：auto 模式的阶段推进此前完全由前端驱动（后端发 step_complete WS -> 浏览器倒计时
    # -> 浏览器发 HTTP 触发下一阶段）。云端多实例下，完成阶段的后台任务可能运行在「不持有
    # 浏览器 WebSocket」的实例上，step_complete 推送丢失 -> 前端永不触发下一阶段 -> 流程卡死
    # （本地单实例不复现）。持久化该标记后，后端可在每个阶段完成时「进程内自链」下一阶段，
    # 使推进不再依赖 WS 消息抵达浏览器。随项目状态持久化到 TOS，支持跨实例接管。
    auto_run: bool = False
    # 视频生成模式：extend=延长（串行，参考前一分镜视频），parallel=并行（默认，各分镜独立并行生成）
    video_generation_mode: str = "parallel"
    next_scene_index: int = 0
    video_scene_states: Dict[int, VideoSceneState] = Field(default_factory=dict)
    generated_video_seeds: List[str] = Field(default_factory=list)
    # 正在「重新生成」（尚未完成生成+审核）的分镜编号集合。
    # 只要非空，就必须阻塞「视频合成(merge)」步骤；用 List 以便随项目状态一并持久化到 TOS。
    regenerating_scene_numbers: List[int] = Field(default_factory=list)

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
