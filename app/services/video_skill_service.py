"""Load installed video skills as bounded, stage-specific production guidance."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import math

from app.config import config
from app.utils.logger import get_logger

logger = get_logger("video_skills")

def review_video_input(url: str, setting: str = "video_review.sample_fps") -> dict:
    """Request several observations per second, including subsecond patch actions."""
    try:
        fps = float(config.get(setting, 3))
        if not math.isfinite(fps):
            fps = 3.
    except (TypeError, ValueError):
        fps = 3.
    return {"type": "video_url", "video_url": {"url": url, "fps": max(.2, min(5., fps))}}


STAGE_SKILLS = {
    "script": [("hyperframes", "references/story.md"), ("vlog-auto-edit", "templates/edit_plan_prompt.md")],
    "reference": [("media-use", "references/resolve.md"), ("hyperframes", "references/types/character.md")],
    "video": [("hyperframes", "references/types/character.md")],
    "review": [("hyperframes", "references/review.md")],
    "merge": [("video-use", "SKILL.md")],
    "optimize": [("video-use", "SKILL.md"), ("vlog-auto-edit", "SKILL.md"),
                 ("hyperframes", "references/review.md"), ("openai-whisper", "SKILL.md"),
                 ("remotion-captions", "SKILL.md"), ("remotion-render", "SKILL.md")],
}

STAGE_RULES = {
    "script": "Plan a causal story with executable action beats and a clear payoff. Each shot names its visible focal point, sound, and timing. Describe inherited opening and closing states for props, body position and lights in each shot. Lock practical light sources and palette in each scene definition; explain every light-state change through a visible cause. Prefer a small number of executable actions over complex hand choreography. Preserve requested dialogue language/dialect. Do not expand a short drama into a travel montage or add an unrequested title card.",
    "reference": "Create an asset ledger: one stable identity per character, explicit outfit variants, consistent spatial landmarks and lighting. Reuse only assets that match the same identity; preserve the requested style and aspect ratio.",
    "video": "Make actions visible and motivated. Match contact, object permanence, eyelines, inherited prop/light states and motion across shots. Human anatomy remains stable: two arms, two legs, anatomically valid hands; fingers neither fuse nor multiply during contact. Preserve motivated light sources and palette for the same scene/state. Honor the existing shot durations and exact dialogue language/dialect. No added caption, title, transition or unrelated effect: subtitles are added after editing.",
    "review": "Compare actual motion with the script and identity references. Inspect hand anatomy, extra or missing limbs/fingers, object penetration, contact mechanics, costume/prop continuity and motivated light sources. Reject confirmed severe anatomy or causal violations even when the average score is high. Report concrete visible evidence and timecodes; distinguish deliberate holds, genre rules and implied continuity from defects. Do not claim defects where hands are occluded, or infer a physical failure from a single blurred frame.",
    "merge": "Use a deterministic ordered edit decision list. Keep audio and picture together, avoid repeated lossy encodes, apply short audio fades at new cut boundaries, and preserve the unedited master for comparison.",
    "optimize": "Use transcript plus on-demand visuals. Confirm defects against actual footage, protect complete utterances and indispensable story beats, and cut only localized verified faults. Cache verbatim word timestamps; map captions onto the edited timeline and apply captions last. Measure output duration, audio and cut boundaries and re-review the rendered film.",
}


class VideoSkillService:
    def root(self) -> Path:
        return Path(str(config.get("video_skills.root", "~/.trae-cn/skills"))).expanduser()

    def enabled(self) -> bool:
        return bool(config.get("video_skills.enabled", False))

    @lru_cache(maxsize=16)
    def _read(self, skill: str, relative: str) -> tuple[str, str]:
        folder = self.root() / skill
        entry = folder / "SKILL.md"
        source = folder / relative
        if not entry.is_file() or not source.is_file():
            raise RuntimeError(f"Missing installed video skill: {skill}/{relative}")
        text = source.read_text(encoding="utf-8")
        return text, sha256(text.encode()).hexdigest()

    def manifest(self, stage: str) -> list[dict]:
        if not self.enabled():
            return []
        records = []
        for skill, relative in STAGE_SKILLS[stage]:
            text, digest = self._read(skill, relative)
            records.append({"skill": skill, "reference": relative, "sha256": digest})
        logger.info("[SKILLS] stage=%s loaded=%s", stage, ",".join(r["skill"] for r in records))
        return records

    def guidance(self, stage: str) -> str:
        records = self.manifest(stage)
        if not records:
            return ""
        # Third-party CLI, auth and approval instructions are not sent to generation models.
        # Only production guidance is adapted, under this project's locked script/asset rules.
        return ("\n[INSTALLED VIDEO SKILLS — PRODUCTION GUIDANCE]\n"
                + STAGE_RULES[stage] + "\nSources: "
                + ", ".join(record["skill"] for record in records)
                + ". Existing project constraints and user requirements take precedence.\n")


video_skill_service = VideoSkillService()
