# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import hashlib
import json
from typing import Any


_SCENE_CONTENT_FIELDS = (
    "scene_name",
    "scene_state",
    "description",
    "dialogue",
    "duration",
    "character_description",
    "voice_description",
    "mood",
    "time_of_day",
    "weather",
    "camera_angle",
    "characters_present",
    "character_outfits",
)


def scene_content_fingerprint(scene: Any) -> str:
    """Return a stable identity for scene content, independent of its display number."""
    if isinstance(scene, dict):
        payload = {field: scene.get(field) for field in _SCENE_CONTENT_FIELDS}
    else:
        payload = {field: getattr(scene, field, None) for field in _SCENE_CONTENT_FIELDS}
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
