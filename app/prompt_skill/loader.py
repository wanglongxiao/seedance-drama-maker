# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

from functools import lru_cache
from pathlib import Path
import re
from string import Template
from typing import Any


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


def nsfw_enabled() -> bool:
    """Return whether adult nudity generation is enabled."""
    from app.config import config

    return _enabled(config.get("content_policy.nsfw_enabled", "off"))


def is_explicitly_adult(age: Any) -> bool:
    """Accept nudity only when age data unambiguously identifies an adult."""
    age_text = str(age or "").strip().lower()
    if not age_text or any(marker in age_text for marker in ("未成年", "未满18", "minor", "child")):
        return False
    numeric_ages = [int(value) for value in re.findall(r"\d+", age_text)]
    if numeric_ages:
        return min(numeric_ages) >= 18
    return any(marker in age_text for marker in ("成年人", "成人", "adult"))


_ADULT_CONTENT_KEYWORDS = (
    "性爱", "色情", "身体裸露", "成人内容", "情色", "情欲", "裸露", "裸体", "全裸", "半裸",
    "上身赤裸", "下身赤裸", "乳房", "乳头", "生殖器", "阴茎", "外阴", "内衣", "内裤",
    "亲密身体", "性暗示", "床戏", "sex", "sexual", "porn", "porno", "erotic",
    "adult content", "nudity", "nude", "naked", "topless", "bottomless", "explicit",
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
