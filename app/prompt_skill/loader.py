# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any, Iterable, List


PROMPT_SKILL_DIR = Path(__file__).resolve().parent


def _enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


@lru_cache(maxsize=128)
def load_prompt(name: str) -> str:
    """Load a markdown prompt/skill template from app/prompt_skill."""
    safe_name = str(name or "").strip().replace("\\", "/")
    if not safe_name or safe_name.startswith("/") or ".." in safe_name.split("/"):
        raise ValueError(f"Invalid prompt template name: {name!r}")

    path = (PROMPT_SKILL_DIR / safe_name).resolve()
    if PROMPT_SKILL_DIR not in path.parents and path != PROMPT_SKILL_DIR:
        raise ValueError(f"Prompt template escapes prompt_skill directory: {name!r}")
    if path.suffix.lower() != ".md":
        raise ValueError(f"Prompt template must be a .md file: {name!r}")
    return path.read_text(encoding="utf-8").strip()


def render_prompt(name: str, **kwargs: Any) -> str:
    """Render a markdown prompt/skill template with $variable placeholders."""
    values = {
        key: "" if value is None else str(value)
        for key, value in kwargs.items()
    }
    return Template(load_prompt(name)).safe_substitute(values).strip()


def private_nsfw_enabled() -> bool:
    """Return whether local private NSFW prompt extensions are enabled."""
    from app.config import config

    return _enabled(config.get("prompt_skill.nsfw.enabled", "off"))


_ADULT_CONTENT_KEYWORDS = (
    "性爱", "色情", "身体裸露", "成人内容", "情色", "情欲", "裸露", "裸体", "全裸", "半裸",
    "内衣", "内裤", "亲密身体", "性暗示", "床戏", "sex", "sexual", "porn", "porno",
    "erotic", "adult content", "nudity", "nude", "naked", "explicit",
)


def nsfw_content_requested(*values: Any) -> bool:
    """Return whether the supplied user/project text indicates adult content."""
    haystack = " ".join(
        str(value or "")
        for value in values
        if value is not None
    ).lower()
    if not haystack:
        return False
    return any(keyword.lower() in haystack for keyword in _ADULT_CONTENT_KEYWORDS)


def load_optional_nsfw_prompt(name: str) -> str:
    """Load a local private NSFW markdown template when enabled and present.

    The nsfw directory is intentionally git-ignored. Missing files are treated as
    empty extensions so public checkouts run without private prompt files.
    """
    if not private_nsfw_enabled():
        return ""

    safe_name = str(name or "").strip().replace("\\", "/")
    if not safe_name or safe_name.startswith("/") or ".." in safe_name.split("/"):
        raise ValueError(f"Invalid private prompt template name: {name!r}")
    if Path(safe_name).suffix.lower() != ".md":
        raise ValueError(f"Private prompt template must be a .md file: {name!r}")

    from app.config import config

    private_dir = str(config.get("prompt_skill.nsfw.directory", "nsfw") or "nsfw").strip() or "nsfw"
    private_parts = private_dir.replace("\\", "/").split("/")
    if private_dir.startswith("/") or ".." in private_parts:
        raise ValueError(f"Invalid private prompt directory: {private_dir!r}")
    path = (PROMPT_SKILL_DIR / private_dir / safe_name).resolve()
    allowed_root = (PROMPT_SKILL_DIR / private_dir).resolve()
    if PROMPT_SKILL_DIR not in allowed_root.parents:
        raise ValueError(f"Private prompt directory escapes prompt_skill directory: {private_dir!r}")
    if allowed_root not in path.parents:
        raise ValueError(f"Private prompt template escapes nsfw directory: {name!r}")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def append_optional_nsfw_prompts(
    prompt_parts: List[str],
    names: Iterable[str],
    *trigger_texts: Any,
) -> None:
    """Append private NSFW prompt extensions only when enabled and requested."""
    if not nsfw_content_requested(*trigger_texts):
        return
    for name in names:
        text = load_optional_nsfw_prompt(name)
        if text:
            prompt_parts.extend(["", text])
