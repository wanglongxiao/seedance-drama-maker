"""Focused digit evidence and configurable reasoning for difficult anatomy checks."""
import re

from app.config import config


DIGIT_EVIDENCE = (
    "\nDIGIT EVIDENCE: count distinct fingertip contours and trace each digit to its "
    "own palm attachment in the same timed frame. Identify the thumb by its lateral "
    "attachment near the palm base, thumb-index web and two phalanges; the other "
    "four fingers normally have three phalanges. Projected length alone does not "
    "identify a thumb. Occlusion, overlapping fingers, a sleeve, reflections and "
    "motion blur do not establish an extra or missing digit. An allegation of six "
    "digits needs SIX separate attachments/contours, not merely a wide silhouette. "
    "A clearly exposed repair hand must positively show one thumb plus four fingers."
)


def concerns_digits(text):
    return bool(re.search(
        r"finger|thumb|digit|手指|五指|六指|拇指|指头|指尖", str(text or ""), re.I))


def anatomy_review_options():
    return {
        "thinking": {"type": "enabled" if config.get(
            "video_optimization.anatomy_review_thinking", True) else "disabled"},
        "max_tokens": int(config.get("video_optimization.anatomy_review_max_tokens", 3000)),
    }
