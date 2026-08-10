# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

from pathlib import Path
import shutil

from app.config import config


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_temp_root() -> Path:
    configured_root = str(config.get("tos.temp_dir", "/tmp/aw-videobot-sd2")).strip() or "/tmp/aw-videobot-sd2"
    temp_root = Path(configured_root)
    if not temp_root.is_absolute():
        temp_root = get_project_root() / temp_root
    temp_root.mkdir(parents=True, exist_ok=True)
    return temp_root


def ensure_project_temp_dir(project_id: str) -> Path:
    project_dir = get_temp_root() / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


def ensure_project_temp_subdir(project_id: str, *parts: str) -> Path:
    path = ensure_project_temp_dir(project_id)
    for part in parts:
        if part:
            path = path / part
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_project_temp_dir(project_id: str) -> None:
    project_dir = get_temp_root() / str(project_id)
    if project_dir.exists():
        shutil.rmtree(project_dir, ignore_errors=True)
