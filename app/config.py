# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import os
import re
import yaml
from typing import Dict, Any
from pathlib import Path

# 敏感信息（大模型 endpoint / API Key / AK / SK / appid / bucket 等）一律通过环境变量注入，
# 严禁在 config.yaml 或任何源码文件中硬编码真实值。config.yaml 中仅保留 ${VAR} 占位符。
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _load_dotenv(env_path: Path) -> None:
    """轻量级 .env 加载器：将 .env 中的键值对写入 os.environ（不覆盖已存在的环境变量）。

    优先使用 python-dotenv（若已安装），否则回退到内置解析，避免引入硬依赖。
    """
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(dotenv_path=str(env_path), override=False)
        return
    except Exception:
        pass
    # 内置回退解析
    with open(env_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _interpolate_env(value: Any) -> Any:
    """递归地将配置中的 ${VAR} / ${VAR:-default} 占位符替换为环境变量值。"""
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    if isinstance(value, str):
        def _replace(match: "re.Match") -> str:
            var_name = match.group(1)
            default = match.group(2)
            return os.environ.get(var_name, default if default is not None else "")
        return _ENV_PATTERN.sub(_replace, value)
    return value


class Config:
    """配置管理类"""
    
    _instance = None
    _config = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._load_config()
        return cls._instance
    
    @classmethod
    def _load_config(cls):
        """加载配置文件

        流程：
        1. 从项目根目录加载 .env（若存在）到环境变量；
        2. 读取 config.yaml；
        3. 将其中的 ${VAR} 占位符替换为对应环境变量值，确保真实凭证不落盘到 config.yaml。
        """
        project_root = Path(__file__).parent.parent
        _load_dotenv(project_root / ".env")
        config_path = project_root / "config.yaml"
        with open(config_path, 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)
        cls._config = _interpolate_env(raw_config)
    
    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """获取配置项，支持点号分隔的路径"""
        keys = key.split('.')
        value = cls._config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value
    
    @classmethod
    def get_all(cls) -> Dict[str, Any]:
        """获取所有配置"""
        return cls._config.copy()
    
    # 便捷属性
    @property
    def modelark_api_key(self) -> str:
        return self.get('modelark.api_key')
    
    @property
    def modelark_base_url(self) -> str:
        return self.get('modelark.base_url')
    
    @property
    def speech_appid(self) -> str:
        return self.get('speech.appid')
    
    @property
    def speech_base_url(self) -> str:
        return self.get('speech.base_url')
    
    @property
    def tos_bucket(self) -> str:
        return self.get('tos.bucket')
    
    @property
    def tos_region(self) -> str:
        return self.get('tos.region')
    
    @property
    def tos_endpoint(self) -> str:
        return self.get('tos.endpoint')
    
    @property
    def tos_uploads_dir(self) -> str:
        return self.get('tos.uploads_dir')
    
    @property
    def video_total_duration(self) -> int:
        return self.get('video_generation.total_duration', 30)
    
    @property
    def scene_duration_min(self) -> int:
        return self.get('video_generation.scene_duration.min', 6)
    
    @property
    def scene_duration_max(self) -> int:
        return self.get('video_generation.scene_duration.max', 15)
    
    @property
    def logging_enabled(self) -> bool:
        return self.get('logging.enabled', True)
    
    @property
    def logging_level(self) -> str:
        return self.get('logging.level', 'INFO')
    
    @property
    def log_agent_calls(self) -> bool:
        return self.get('logging.log_agent_calls', True)
    
    @property
    def log_llm_io(self) -> bool:
        return self.get('logging.log_llm_io', True)

# 全局配置实例
config = Config()
