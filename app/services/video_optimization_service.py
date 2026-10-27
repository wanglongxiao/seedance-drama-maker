"""Evidence-based local editing and verbatim subtitles for the merged master."""
import hashlib
import os
import json
import math
import shutil
import subprocess
import statistics
import re
import copy
import time
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime

from app.config import config
from app.services.llm_service import llm_service
from app.services.tos_service import tos_service
from app.services.video_skill_service import video_skill_service, review_video_input
from app.utils.logger import get_logger
from app.utils.task_paths import ensure_project_temp_subdir
from app.utils.dialogue_language import requests_cantonese
from app.utils.visual_review import DIGIT_EVIDENCE, concerns_digits, anatomy_review_options

logger = get_logger("video_optimization")

MODERATE_CAUSAL_STANDARD = (
    "\nSTRICT BLOCKING DECISION TEST: First state what actually happens in the complete film, "
    "separately from the requested choreography. A blocker requires an observable contradictory "
    "event or invalid anatomy/contact, NOT reduced spectacle, reduced upgrade excitement, a "
    "weaker ritual feeling, or a less literal execution. If a weapon demonstrably awakens/powers "
    "up and its later action and outcome are coherent, glow versus shattering is an alternative "
    "effect treatment, not a missing indispensable cause. If a token and matching sword engravings "
    "consistently use the SAME decorative motif and the character recognizes/connects them without "
    "contradictory outcome, variation of motif from the text is advisory. Do not claim recognition "
    "lacks its visual cause when your own observations say the two visible motifs MATCH. Look for "
    "an actual mismatch between the objects or a visibly failed/contradictory outcome. Exact "
    "word-to-image fidelity may be advisory when the film remains coherent. Keep every real third "
    "hand, identity substitution, object penetration, impossible grip, contradictory line or "
    "impossible cause blocking.\n"
)


def command(args, cwd=None):
    env = dict(os.environ, HYPERFRAMES_SKIP_SKILLS="1", HYPERFRAMES_NO_TELEMETRY="1")
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True, cwd=cwd, env=env)
    if result.returncode:
        # Commands can contain signed URLs; never include arguments in errors.
        raise RuntimeError("Media command failed: " + result.stderr[-1200:])
    return result.stdout


def probe(path):
    return json.loads(command(["ffprobe", "-v", "error", "-show_format",
                               "-show_streams", "-of", "json", path]))


def fast_triage_enabled():
    return bool(config.get("video_optimization.fast_triage", True))


def scene_sources_key(project):
    """Signed URL refreshes keep identity; scene replacements invalidate it."""
    return hashlib.sha256(json.dumps([
        (v.scene_number, urlparse(v.url).netloc.lower(), urlparse(v.url).path,
         getattr(v, "duration", None), getattr(v, "scene_content_hash", None))
        for v in sorted(getattr(project, "videos", []), key=lambda v: v.scene_number)
    ]).encode()).hexdigest()


def triage_report(report):
    """One observed fault gets one cut/retain/repair decision without another LLM call."""
    result = copy.deepcopy(report)
    result["issues"] = []
    result.setdefault("advisories", [])
    result["triage_decisions"] = []
    threshold = float(config.get("video_optimization.critical_regenerate_below", 40))
    confidence = float(config.get("video_optimization.confidence_threshold", .85))
    for original in report.get("issues", []):
        issue = dict(original)
        score = issue.get("quality_score")
        score_valid = (isinstance(score, (int, float)) and not isinstance(score, bool)
                       and math.isfinite(score) and 0 <= score <= 100)
        credible = (bool(issue.get("evidence"))
                    and isinstance(issue.get("confidence"), (int, float))
                    and math.isfinite(issue["confidence"]) and issue["confidence"] >= confidence)
        critical = issue.get("essential_story_beat")
        if issue.get("severity") == "minor" and issue.get("viewer_unacceptable") is False:
            action, reason = "retain", "acceptable_minor_advisory"
        elif credible and critical is False:
            action, reason = "cut", "noncritical_story_fragment"
        elif (credible and critical is True and score_valid and score < threshold
              and issue.get("viewer_unacceptable") is True):
            action, reason = "regenerate", "unacceptable_low_score_critical_fragment"
        else:
            action, reason = "retain", "acceptable_or_insufficient_evidence_for_critical_repair"
        issue.update(action=action, decision_reason=reason, fast_triage_evaluated=True,
                     confirmed=credible)
        result["triage_decisions"].append(issue)
        if action == "retain":
            result["advisories"].append(issue)
        else:
            result["issues"].append(issue)
    result["approved"] = not result["issues"]
    result["review_mode"] = "fast_triage"
    return result


def reuse_verified_patch_review(previous, current, history):
    """Reuse only unchanged footage observations after independently accepted local patches."""
    if (previous.get("review_mode") != "fast_triage"
            or previous.get("review_status") != "available"
            or not previous.get("verified_review")
            or any(previous.get(k) != current.get(k) for k in
                   ("review_context_sha256", "transcript_sha256", "pending_faults_sha256"))
            or abs(previous.get("input_duration", -1) - current["input_duration"]) > .05):
        return None
    old_timeline, new_timeline = previous.get("scene_timeline", []), current["scene_timeline"]
    if (len(old_timeline) != len(new_timeline) or any(
            before["scene_number"] != after["scene_number"]
            or any(abs(before[key] - after[key]) > .05 for key in ("start", "end"))
            for before, after in zip(old_timeline, new_timeline))):
        return None
    position = previous.get("reviewed_history_length")
    if not isinstance(position, int) or not 0 <= position <= len(history):
        return None
    changes = history[position:]
    if any(r.get("kind") in {"restore_original", "fragment_reverted", "scene_repair"} for r in changes):
        return None
    patches = [r for r in changes if r.get("kind") == "fragment_applied"]
    if not patches:
        return None
    def covered(issue):
        cursor = float(issue["start"])
        for patch in sorted(patches, key=lambda r: r["start"]):
            if patch["start"] <= cursor + .001:
                cursor = max(cursor, patch["end"])
        return cursor >= float(issue["end"]) - .001
    review = copy.deepcopy(previous["verified_review"])
    review["issues"] = [i for i in review["issues"] if not covered(i)]
    review["triage_decisions"] = [dict(i, action="retain", decision_reason="resolved_by_verified_patch")
                                if covered(i) else i for i in review.get("triage_decisions", [])]
    review["approved"] = not review["issues"]
    return review


def safe_cut_plan(issues, segments, duration, *, max_seconds=3, allow_disposable_dialogue=False):
    """Only confirmed localized faults outside entire spoken utterances are cut."""
    cuts, rejected = [], []
    minimum = float(config.get("video_optimization.min_kept_segment_seconds", .5))
    budget = duration * float(config.get("video_optimization.max_removed_fraction", .2))
    def confidence(issue):
        try:
            value = float(issue.get("confidence", 0))
            return value if math.isfinite(value) else 0.
        except (AttributeError, TypeError, ValueError):
            return 0.
    for issue in sorted(issues, key=confidence, reverse=True):
        try:
            start, end = float(issue["start"]), float(issue["end"])
            disposable = (allow_disposable_dialogue and issue.get("dialogue_disposable") is True
                          and issue.get("essential_story_beat") is False)
            if disposable:
                # Remove a complete disposable utterance, never its middle words.
                for _ in range(len(segments) + 1):
                    before = (start, end)
                    for segment in segments:
                        if start < float(segment["end"]) + .15 and end > float(segment["start"]) - .15:
                            start = max(0., min(start, float(segment["start"]) - .15))
                            end = min(duration, max(end, float(segment["end"]) + .15))
                    if before == (start, end):
                        break
            valid = (issue.get("confirmed") is True and issue.get("safe_to_remove") is True
                     and issue.get("essential_story_beat") is False
                     and bool(issue.get("evidence"))
                     and confidence(issue) >= float(config.get("video_optimization.confidence_threshold", .85))
                     and 0 <= start < end <= duration and end - start <= max_seconds
                     and (disposable or not any(start < float(s["end"]) + .15
                                               and end > float(s["start"]) - .15 for s in segments)))
            candidate = sorted(cuts + [(start, end)])
            valid = valid and sum(b - a for a, b in candidate) <= budget
            valid = valid and all(candidate[i][0] - candidate[i - 1][1] >= minimum for i in range(1, len(candidate)))
            valid = valid and (start == 0 or start >= minimum) and (end == duration or duration - end >= minimum)
        except (KeyError, TypeError, ValueError):
            valid = False
        if valid:
            cuts = candidate
        else:
            rejected.append(issue)
    kept, cursor = [], 0.
    for start, end in cuts:
        if start > cursor:
            kept.append((cursor, start))
        cursor = end
    if cursor < duration:
        kept.append((cursor, duration))
    return cuts, kept, rejected


def map_segments(segments, kept):
    continuous = []
    for start, end in kept:
        if continuous and abs(continuous[-1][1]-start) < .00001:
            continuous[-1] = (continuous[-1][0], end)
        else:
            continuous.append((start, end))
    mapped, offset = [], 0.
    for start, end in continuous:
        for segment in segments:
            if float(segment["start"]) >= start and float(segment["end"]) <= end:
                item = dict(segment)
                item["start"] = offset + float(segment["start"]) - start
                item["end"] = offset + float(segment["end"]) - start
                item["words"] = [
                    dict(w, start=offset + float(w["start"]) - start, end=offset + float(w["end"]) - start)
                    for w in segment.get("words", [])
                ]
                mapped.append(item)
        offset += end - start
    return mapped


def review_context_key(script, references):
    value = {"script": script, "references": [urlparse(u).path for u in references],
             "review_policy_revision": 13,
             "fast_triage": fast_triage_enabled(),
             "critical_regenerate_below": config.get("video_optimization.critical_regenerate_below", 40),
             "fast_review_max_tokens": config.get("video_optimization.fast_review_max_tokens", 1600),
             "fast_max_cut_seconds": config.get("video_optimization.fast_max_cut_seconds", 6),
             "review_model": config.get("models.video_review.endpoint"),
             "review_thinking_disabled": bool(config.get("models.video_review.disable_thinking", False)),
             "anatomy_review_options": anatomy_review_options(),
             "impact_audit_enabled": bool(config.get("video_optimization.audit_story_impact", True)),
             "video_sampling_fps": review_video_input("", "video_optimization.review_fps")["video_url"]["fps"],
             "repair_min_severity": config.get("video_optimization.repair_min_severity", "major")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def independent_review_windows(issues):
    """A second viewer gets locations, without being primed by a previous accusation."""
    return {"verification_windows": [{"start": i["start"], "end": i["end"]} for i in issues],
            "subtitles": False}

def fragment_attempts(history, scene_number, start, end):
    """An explicit optimization run starts a budget; automatic rounds share it."""
    restored = max((i for i, record in enumerate(history)
                    if record.get("kind") in {"restore_original", "optimization_run"}), default=-1)
    return sum(record.get("kind") == "fragment_repair"
               and record.get("scene_number") == scene_number
               and abs(record.get("start", -100)-start) < .25
               and abs(record.get("end", -100)-end) < .25
               for record in history[restored+1:])

def validate_ranges(ranges, segments, duration, max_fraction=None):
    """User edits must also preserve complete spoken utterances and valid media."""
    normalized = sorted((float(r["start"]), float(r["end"])) for r in ranges)
    if any(not (math.isfinite(a) and math.isfinite(b) and 0 <= a < b <= duration) for a, b in normalized):
        raise ValueError("Edit range must satisfy 0 <= start < end <= master duration")
    if any(normalized[i][0] < normalized[i-1][1] for i in range(1, len(normalized))):
        raise ValueError("Edit ranges overlap")
    max_fraction = float(config.get("video_optimization.max_removed_fraction", .2)) if max_fraction is None else max_fraction
    if sum(b-a for a, b in normalized) > duration * max_fraction:
        raise ValueError("Edit ranges exceed the configured duration budget")
    if max_fraction < 1 and normalized:
        minimum = float(config.get("video_optimization.min_kept_segment_seconds", .5))
        gaps = [normalized[0][0], duration-normalized[-1][1]] + [
            normalized[i][0]-normalized[i-1][1] for i in range(1, len(normalized))]
        if any(0 < gap < minimum for gap in gaps):
            raise ValueError("Edit would leave a fragment shorter than the minimum kept duration")
    if any(a < float(s["end"])+.15 and b > float(s["start"])-.15
           for a, b in normalized for s in segments):
        raise ValueError("Cut intersects a spoken utterance; use a local picture repair instead")
    return normalized


def fragment_plan(issues, timeline, duration):
    """Only independently confirmed faults become local, non-overlapping repairs."""
    intervals = []
    threshold = float(config.get("video_optimization.confidence_threshold", .85))
    for issue in issues:
        try:
            a, b, certainty = float(issue["start"]), float(issue["end"]), float(issue["confidence"])
            if (issue.get("confirmed") is not True or not issue.get("evidence")
                    or not all(math.isfinite(n) for n in (a, b, certainty))
                    or certainty < threshold or not 0 <= a < b <= duration):
                continue
        except (TypeError, ValueError, KeyError):
            continue
        for scene in timeline:
            start, end = max(a, scene["start"]), min(b, scene["end"])
            if start < end:
                intervals.append(dict(start=start, end=end, scene_number=scene["scene_number"],
                                      evidence=str(issue["evidence"])))
    merged = []
    for item in sorted(intervals, key=lambda r: r["start"]):
        if merged and item["scene_number"] == merged[-1]["scene_number"] and item["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], item["end"])
            merged[-1]["evidence"] += "\n" + item["evidence"]
        else:
            merged.append(dict(item))
    maximum = max(.5, float(config.get("video_optimization.fragment_max_seconds", 6)))
    result = []
    for item in merged:
        start = item["start"]
        while start < item["end"]-.001:
            end = min(item["end"], start+maximum)
            result.append(dict(item, start=start, end=end))
            start = end
    return result


def localized_scene_description(description, start, end):
    """Give the patch model only storyboard beats intersecting its local time span."""
    matches = list(re.finditer(r"(\d+(?:\.\d+)?)\s*[-–—~至]\s*(\d+(?:\.\d+)?)\s*秒\s*[:：]?", description))
    if not matches:
        return description
    parts = []
    for index, match in enumerate(matches):
        if float(match[1]) < end and float(match[2]) > start:
            limit = matches[index+1].start() if index+1 < len(matches) else len(description)
            parts.append(description[match.end():limit].strip())
    return "\n".join(parts) or description


def review_category(value):
    """Models may translate category labels even when output fields are stable."""
    text = str(value or "").strip().casefold().replace("_", " ")
    aliases = {
        "anatomy": ("anatomy", "limb", "finger", "肢体", "肢體", "手指", "畸变", "畸變"),
        "contact": ("contact", "physical", "penetration", "physics", "物理", "穿透", "接触", "接觸"),
        "prop": ("prop", "道具", "铭文", "銘文", "纹路", "紋路"),
        "identity": ("identity", "costume", "身份", "服装", "服裝"),
        "lighting": ("light", "palette", "光影", "光照", "色调", "色調"),
        "dialogue": ("dialogue", "speech", "台词", "臺詞", "对白", "對白"),
        "plot": ("plot", "storyboard", "story beat", "剧情", "劇情", "情节", "情節"),
    }
    return next((key for key, values in aliases.items() if any(v in text for v in values)), text)


def explicit_defect_kind(issue):
    """Recognize incompatible observed faults without treating a time overlap as proof."""
    text = str(issue.get("evidence") or "").casefold()
    if re.search(r"穿模|穿透|穿过|穿過|penetrat|passes through|pass through|clips? through", text):
        return "penetration"
    digits = bool(re.search(r"手指|指头|指頭|finger|digit", text))
    extra = bool(re.search(r"多出|多余|多餘|额外|額外|重复|重複|extra|additional|duplicat|six|六|6\s*根", text))
    missing = bool(re.search(r"缺失|缺少|少了|missing|absent|only\s*four|仅.*4|僅.*4|只有四", text))
    if digits and extra:
        return "extra_digit"
    if digits and missing:
        return "missing_digit"
    limbs = bool(re.search(r"手(?!指)|手臂|胳膊|腿|肢|hand|arm|leg|limb", text))
    if limbs and (extra or re.search(r"两只.*手|兩隻.*手|两只.*腿|two.*hands|third.*hand", text)):
        return "extra_limb"
    if limbs and missing:
        return "missing_limb"
    if re.search(r"融合|粘连|黏連|fused|fusion", text):
        return "fused_part"
    return None


def matching_review_issue(issue, candidate):
    if review_category(issue.get("category")) != review_category(candidate.get("category")):
        return False
    observed, alleged = explicit_defect_kind(issue), explicit_defect_kind(candidate)
    if observed and alleged and observed != alleged:
        return False
    a, b, c, d = (float(item[key]) for item, key in (
        (issue, "start"), (issue, "end"), (candidate, "start"), (candidate, "end")))
    overlap, shorter = max(0, min(b, d)-max(a, c)), min(b-a, d-c)
    return (abs(a-c) <= .25 and abs(b-d) <= .25) or (shorter > 0 and overlap/shorter >= .5)


def reorder_ranges(kept, timeline, order):
    numbers = [item["scene_number"] for item in timeline]
    if not order:
        return kept
    if len(order) != len(numbers) or set(order) != set(numbers):
        raise ValueError("Scene order must include each storyboard scene exactly once")
    ordered = []
    for number in order:
        scene = next(item for item in timeline if item["scene_number"] == number)
        ordered += [(max(a, scene["start"]), min(b, scene["end"])) for a, b in kept
                    if max(a, scene["start"]) < min(b, scene["end"])]
    return ordered


def timed_asr_segments(result, duration):
    """Keep native audio words and millisecond timings, including Cantonese particles."""
    segments = []
    for utterance in result.get("utterances", []):
        words = []
        for item in utterance.get("words", []):
            text = str(item.get("text", ""))
            if not text.strip():
                continue
            start, end = float(item.get("start_time", -1))/1000, float(item.get("end_time", -1))/1000
            if start < 0 or end <= start:
                if words and re.fullmatch(r"[，。！？、；：,.!?;:]+", text):
                    words[-1]["word"] += text
                    continue
                raise ValueError("ASR word has no usable audio timestamps")
            if not (math.isfinite(start) and math.isfinite(end) and end <= duration+.25):
                raise ValueError("ASR timestamp falls outside the soundtrack")
            words.append({"word": text, "start": start, "end": min(end, duration),
                          "probability": float(item.get("confidence", 0))})
        if not words:
            start, end = float(utterance.get("start_time", -1))/1000, float(utterance.get("end_time", -1))/1000
            if utterance.get("text") and 0 <= start < end <= duration+.25:
                segments.append({"start": start, "end": min(end, duration), "text": utterance["text"], "words": []})
            continue
        group = []
        for word in words:
            if group and (word["start"]-group[-1]["end"] > .5 or
                          group[-1]["word"].endswith(("。", "！", "？", ".", "!", "?"))):
                segments.append({"start": group[0]["start"], "end": group[-1]["end"],
                                 "text": "".join(w["word"] for w in group), "words": group})
                group = []
            group.append(word)
        if group:
            segments.append({"start": group[0]["start"], "end": group[-1]["end"],
                             "text": "".join(w["word"] for w in group), "words": group})
    if result.get("text", "").strip() and not segments:
        raise ValueError("ASR speech has no usable utterance timestamps")
    return sorted(segments, key=lambda item: item["start"])


def subtitle_homophones(segments, script, timeline, language):
    """Correct only equally pronounced Chinese characters in a matched spoken line."""
    if language != "zh":
        return segments, []
    try:
        from pypinyin import lazy_pinyin
    except ImportError:
        return segments, []
    corrected, changes = copy.deepcopy(segments), []
    han = lambda text: "".join(re.findall(r"[\u4e00-\u9fff]", text))
    for segment in corrected:
        scene_range = next((r for r in timeline if r["start"] <= segment["start"] < r["end"]), None)
        if not scene_range or not segment.get("words"):
            continue
        scene = script.scenes[scene_range["scene_number"]-1]
        lines = [han(re.sub(r"^\s*\[[^\]]+\]\s*[:：]?", "", line))
                 for line in (scene.dialogue or "").splitlines()]
        original = han("".join(w["word"] for w in segment["words"]))
        if len(original) < 4:
            continue
        candidates = set()
        for line in lines:
            for offset in range(max(0, len(line)-len(original)+1)):
                candidate = line[offset:offset+len(original)]
                differences = sum(a != b for a, b in zip(original, candidate))
                if (0 < differences <= max(1, len(original)//4)
                        and lazy_pinyin(original) == lazy_pinyin(candidate)):
                    candidates.add(candidate)
        if len(candidates) != 1:
            continue
        replacement = candidates.pop()
        cursor = 0
        for word in segment["words"]:
            chars = []
            for char in word["word"]:
                if re.fullmatch(r"[\u4e00-\u9fff]", char):
                    chars.append(replacement[cursor])
                    cursor += 1
                else:
                    chars.append(char)
            word["word"] = "".join(chars)
        segment["text"] = "".join(w["word"] for w in segment["words"])
        changes.append(dict(start=segment["start"], end=segment["end"],
                            original=original, corrected=replacement, reason="same_pronunciation"))
    return corrected, changes


def narration_leak_candidates(segments, script, timeline):
    """Propose only long spoken production instructions in explicitly speechless shots."""
    han = lambda text: "".join(re.findall(r"[\u4e00-\u9fff]", text))
    candidates = []
    for scene_range in timeline:
        scene = script.scenes[scene_range["scene_number"]-1]
        if (scene.dialogue or "").strip():
            continue
        utterances = [s for s in segments if scene_range["start"] <= s["start"]
                      and s["end"] <= scene_range["end"]+.4 and len(han(s["text"])) >= 5]
        description = han(scene.description)
        anchor = any(SequenceMatcher(None, han(s["text"]), description, autojunk=False)
                     .find_longest_match().size >= 8 for s in utterances)
        if anchor:
            candidates.append(dict(scene_number=scene_range["scene_number"],
                start=max(scene_range["start"], min(s["start"] for s in utterances)-.15),
                end=max(s["end"] for s in utterances)+.15,
                utterances=utterances))
    return candidates


class VideoOptimizationService:
    def download(self, url, target, check_active):
        import requests
        if target.exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".part.mp4")
        with requests.get(tos_service.ensure_accessible_url(url), stream=True, timeout=300) as response:
            response.raise_for_status()
            with partial.open("wb") as output:
                for chunk in response.iter_content(1024*1024):
                    check_active()
                    output.write(chunk)
        probe(partial)
        partial.replace(target)
        return target

    def scene_timeline(self, project, duration, check_active):
        check_active()
        previous = getattr(project, "optimization_report", None) or {}
        old_timeline = previous.get("scene_timeline", [])
        old_duration = previous.get("input_duration")
        numbers = sorted(v.scene_number for v in project.videos)
        try:
            valid = (previous.get("scene_sources_sha256") == scene_sources_key(project)
                     and old_duration > 0 and math.isfinite(old_duration)
                     and bool(old_timeline) and len(set(numbers)) == len(numbers)
                     and [r["scene_number"] for r in old_timeline] == numbers
                     and abs(old_timeline[0]["start"]) < .001
                     and abs(old_timeline[-1]["end"] - old_duration) < .001
                     and all(math.isfinite(r["start"]) and math.isfinite(r["end"])
                             and 0 <= r["start"] < r["end"] <= old_duration + .001
                             for r in old_timeline)
                     and all(abs(before["end"] - after["start"]) < .001
                             for before, after in zip(old_timeline, old_timeline[1:])))
        except (KeyError, TypeError, ValueError):
            valid = False
        if valid:
            logger.info("[OPTIMIZE] Reused verified scene timeline: scenes=%s", len(old_timeline))
            return [dict(scene_number=r["scene_number"],
                         start=r["start"] * duration / old_duration,
                         end=r["end"] * duration / old_duration) for r in old_timeline]
        cache = ensure_project_temp_subdir(project.project_id, "optimization", "sources")
        clips = []
        for video in sorted(project.videos, key=lambda item: item.scene_number):
            key = hashlib.sha256(urlparse(video.url).path.encode()).hexdigest()[:16]
            path = self.download(video.url, cache/f"{video.scene_number}-{key}.mp4", check_active)
            info = probe(path)
            stream = next(s for s in info["streams"] if s["codec_type"] == "video")
            clips.append((video.scene_number, float(stream.get("duration", info["format"]["duration"]))))
        total = sum(length for _, length in clips)
        offset, timeline = 0., []
        for number, length in clips:
            end = offset + length * duration / total
            timeline.append({"scene_number": number, "start": offset, "end": end})
            offset = end
        return timeline

    def lighting_plan(self, source, timeline, script):
        """Small corrections within the same set/state; preserve motivated light changes."""
        records = []
        for item in timeline:
            scene = script.scenes[item["scene_number"]-1]
            start, end = item["start"], item["end"]
            frames = []
            for fraction in (.2, .5, .8):
                data = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start+(end-start)*fraction),
                    "-i", str(source), "-frames:v", "1", "-vf", "scale=64:36,format=gray",
                    "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
                if data:
                    frames.append(statistics.mean(data)/255)
            level = statistics.median(frames) if frames else .3
            motivated = any(word in scene.description.lower() for word in
                ("开灯", "關燈", "关灯", "熄", "点亮", "閃電", "闪电", "switch", "flash", "sunrise"))
            records.append(dict(item, luminance=level, motivated=motivated,
                group=(scene.scene_name, scene.scene_state or "", scene.time_of_day)))
        for record in records:
            peers = [r["luminance"] for r in records if r["group"] == record["group"] and not r["motivated"]]
            target = statistics.median(peers) if peers else record["luminance"]
            record["brightness"] = 0. if record["motivated"] else max(-.025, min(.025, target-record["luminance"]))
        for record in records:
            record.pop("group")
        return records

    def select_fragment_offset(self, patch_url, generated_duration, required_duration,
                               requirement, boundaries, check_active):
        """Skip an unwanted setup when a silent patch's correction occurs later."""
        check_active()
        content = [{"type": "text", "text": (
            "Select ONE continuous interval from this actual generated repair video. "
            f"The interval must be exactly {required_duration:.3f} seconds long within "
            f"the {generated_duration:.3f}-second generated clip. "
            "It replaces a silent picture interval, keeping the original soundtrack. "
            "Select the interval that visibly corrects the specified defect and best "
            "matches the incoming and outgoing boundary poses, lighting and action. "
            "Skip an unnecessary setup if the corrected action occurs later. "
            "Do not select an unrelated reaction, or claim a repair that never appears. "
            "Return ONLY JSON: {\"start\":seconds,\"usable\":boolean,\"confidence\":0..1,"
            "\"evidence\":string}. Evidence must name the actual visible correction and timestamps. "
            "No interval is usable if the entire clip lacks the requested fix.\n"
            f"Required correction: {requirement}")},
            review_video_input(patch_url, "video_optimization.review_fps")]
        content += [{"type": "image_url", "image_url": {"url": url}} for url in boundaries]
        response = llm_service.chat_completion(model=config.get("models.video_review.endpoint"),
            messages=[{"role": "user", "content": content}], temperature=.1, max_tokens=1000)
        text = response["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        result = json.loads(text)
        start, confidence = float(result["start"]), float(result["confidence"])
        if not (result.get("usable") is True and result.get("evidence")
                and math.isfinite(start) and math.isfinite(confidence) and .85 <= confidence <= 1
                and 0 <= start <= generated_duration-required_duration+.001):
            raise ValueError("Generated clip contains no verified usable repair window")
        check_active()
        return min(start, generated_duration-required_duration)

    def prepare_fragment(self, project, source, patch_url, start, end, check_active, generated_start=0):
        """Trim minimum-length model output to the fault span, keeping original audio."""
        cache = ensure_project_temp_subdir(project.project_id, "optimization", "repair",
            hashlib.sha256(f"{patch_url.split('?')[0]}:{start}:{end}:{generated_start}".encode()).hexdigest()[:16])
        patch = self.download(patch_url, cache/"generated.mp4", check_active)
        available = float(probe(patch)["format"]["duration"])
        if not math.isfinite(generated_start) or generated_start < 0 or available < generated_start+end-start:
            raise ValueError("Replacement footage is shorter than its required interval")
        info = probe(source)
        stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        target = cache/"patch.mp4"
        args = ["ffmpeg", "-v", "error", "-y", "-ss", generated_start, "-i", patch, "-ss", start, "-i", source,
                "-map", "0:v:0", "-vf", f"scale={stream['width']}:{stream['height']},setsar=1,fps=24",
                "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p"]
        if any(s["codec_type"] == "audio" for s in info["streams"]):
            args += ["-map", "1:a:0", "-c:a", "aac", "-ar", "48000"]
        check_active()
        command(args+["-t", end-start, "-movflags", "+faststart", target])
        # Parallel workers often finish in the same second; timestamp-only patch
        # names can overwrite another worker's media before its review reads it.
        content_id = hashlib.sha256(target.read_bytes()).hexdigest()[:20]
        url = tos_service.upload_file(str(target), project_id=project.project_id,
                                      custom_filename=f"patch-{content_id}.mp4",
                                      category="videos/optimization/patches")
        return {"start": start, "end": end, "path": str(target), "url": url, "generated_start": generated_start}

    def replace_fragments(self, project, source, replacements, check_active):
        """Apply a verified batch to one immutable master; never race on shared files."""
        cache = ensure_project_temp_subdir(project.project_id, "optimization", "repair",
            "batch-"+datetime.now().strftime("%Y%m%d%H%M%S%f"))
        info = probe(source)
        duration = float(info["format"]["duration"])
        replacements = sorted(replacements, key=lambda r: r["start"])
        cursor, ranges = 0., []
        for patch in replacements:
            a, b = float(patch["start"]), float(patch["end"])
            if not (math.isfinite(a) and math.isfinite(b) and cursor <= a < b <= duration):
                raise ValueError("Replacement intervals overlap or fall outside the master")
            if float(probe(patch["path"])["format"]["duration"]) < b-a-.05:
                raise ValueError("Replacement footage is too short")
            if a > cursor:
                ranges.append((source, cursor, a))
            ranges.append((patch["path"], 0, b-a))
            cursor = b
        if cursor < duration:
            ranges.append((source, cursor, duration))
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        parts = []
        for index, (path, a, b) in enumerate(ranges):
            if b <= a:
                continue
            check_active()
            part = cache/f"repair-part-{index}.mp4"
            command(["ffmpeg", "-v", "error", "-y", "-ss", a, "-i", path, "-t", b-a,
                "-an", "-vf", f"scale={video['width']}:{video['height']},setsar=1,fps=24",
                "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p", part])
            parts.append(part)
        listing = cache/"repair-concat.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
        silent = cache/"repair-picture.mp4"
        command(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", silent])
        repaired = cache/"repaired-master.mp4"
        args = ["ffmpeg", "-v", "error", "-y", "-i", silent, "-i", source, "-map", "0:v:0"]
        if any(s["codec_type"] == "audio" for s in info["streams"]):
            args += ["-map", "1:a:0", "-c:a", "copy"]
        command(args+["-c:v", "copy", "-t", duration, "-movflags", "+faststart", repaired])
        actual = probe(repaired)
        if abs(float(actual["format"]["duration"])-duration) > .10:
            raise RuntimeError("Fragment replacement changed master duration")
        check_active()
        return tos_service.upload_file(str(repaired), custom_filename=f"repaired-{hashlib.sha256(repaired.read_bytes()).hexdigest()[:12]}.mp4",
            project_id=project.project_id, category="videos/optimization")

    def export_timeline(self, source, directory, kept, info, has_audio):
        """Keep an editable HyperFrames timeline alongside the original master."""
        runtime = Path(str(config.get("video_skills.runtime_dir"))).expanduser()
        cli = runtime / "node_modules/hyperframes/dist/cli.js"
        timeline = directory / "hyperframes"
        if not timeline.exists():
            command(["node", cli, "init", timeline, "--non-interactive", "--skip-skills"])
        assets = timeline / "assets"
        assets.mkdir(exist_ok=True)
        master = assets / "master.mp4"
        if not master.exists():
            master.symlink_to(source)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        clips, offset = [], 0.
        for index, (start, end) in enumerate(kept):
            audio = 'data-has-audio="true"' if has_audio else "muted"
            clips.append(f'<video id="shot-{index}" class="clip" src="assets/master.mp4" '
                         f'data-start="{offset}" data-duration="{end-start}" data-media-start="{start}" '
                         f'data-track-index="0" {audio}></video>')
            offset += end-start
        (timeline / "index.html").write_text(
            '<!doctype html><html><head><meta charset="UTF-8"><style>'
            '*{margin:0;padding:0}html,body,#root{width:100%;height:100%;overflow:hidden;background:#000}'
            'video{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}'
            '</style></head><body>'
            f'<div id="root" data-composition-id="main" data-start="0" data-duration="{offset}" '
            f'data-width="{video["width"]}" data-height="{video["height"]}" data-no-timeline>'
            + "".join(clips) + "</div></body></html>")
        return command(["node", cli, "lint", "--json"], cwd=timeline)

    def window_evidence(self, source, directory, windows, check_active):
        """Show short actions explicitly when a whole-film viewer may miss them."""
        import io
        from PIL import Image, ImageDraw
        duration = float(probe(source)["format"]["duration"])
        identity = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        records = []
        for window in windows[:8]:
            a, b = float(window["start"]), float(window["end"])
            if not (math.isfinite(a) and math.isfinite(b) and 0 <= a < b <= duration):
                raise ValueError("Verification window falls outside its master")
            # Include context on both sides; every label is a master timestamp.
            left, right = max(0., a-.3), min(duration-.001, b+.3)
            count = max(2, min(24, math.ceil((right-left)*6)+1))
            times = [left+(right-left)*i/(count-1) for i in range(count)]
            for page in range(0, len(times), 12):
                page_times = times[page:page+12]
                sheet = Image.new("RGB", (1284, math.ceil(len(page_times)/3)*264), "#151515")
                draw = ImageDraw.Draw(sheet)
                for index, second in enumerate(page_times):
                    check_active()
                    frame = subprocess.run([
                        "ffmpeg", "-v", "error", "-ss", str(second), "-i", str(source),
                        "-frames:v", "1", "-vf", "scale=428:240:force_original_aspect_ratio=decrease,"
                        "pad=428:240:(ow-iw)/2:(oh-ih)/2",
                        "-f", "image2pipe", "-vcodec", "mjpeg", "-",
                    ], capture_output=True, check=True).stdout
                    x, y = index % 3 * 428, index // 3 * 264
                    with Image.open(io.BytesIO(frame)) as image:
                        sheet.paste(image.convert("RGB"), (x, y))
                    draw.text((x+8, y+244), f"Master {second:.3f}s", fill="white")
                key = hashlib.sha256(f"{identity}:{a}:{b}:{page}".encode()).hexdigest()[:16]
                folder = directory / "review-evidence"
                folder.mkdir(exist_ok=True)
                target = folder / f"{key}.jpg"
                sheet.save(target, quality=92)
                check_active()
                url = tos_service.upload_file(
                    str(target), project_id=directory.name,
                    custom_filename=f"evidence-{key}.jpg", category="videos/optimization/evidence")
                records.append(dict(start=a, end=b, master_timestamps=page_times, url=url))
        logger.info("[OPTIMIZE] timestamped evidence sheets=%s windows=%s", len(records), min(8, len(windows)))
        return records

    def repair_goal(self, fault, local_action, seconds):
        """Translate an unwanted observed state into an affirmative repair contract."""
        prompt = (
            "Plan a localized picture repair. The ORIGINAL FAULT below describes the UNWANTED "
            "state, never a state to recreate or approve. Derive the desired corrected state "
            "from the storyboard. Explicitly reverse the fault when necessary: if an intact "
            "blade should be broken, require a visibly broken blade, not the intact blade. "
            "Do not copy the complaint as the goal. State what must be visibly present, not just "
            "'fix consistency'. The background action locates the interval; do not require "
            "replaying every background action. Limit the correction to this short interval, "
            "keeping identity, other objects and original audio. "
            "Checks must test ONLY the actual defect and coherent boundaries. Do not add exact "
            "camera travel, sparks, light sweeps, grips or poses from the background unless their "
            "absence is the observed defect or an indispensable cause. For a penetration repair, "
            "require distinct coherent body surfaces and valid contact; a limb being behind "
            "another limb is normal depth ordering, not penetration. Motion must describe the "
            "small local continuation actually needed, not a replay of the storyboard checklist. "
            "Return ONLY JSON {\"goal\":string,\"checks\":[string],\"motion\":string}. "
            "Checks must establish the corrected state rather than the original faulty state.\n"
            f"Interval duration: {seconds:.3f}s\nORIGINAL FAULT: {fault}\n"
            f"Desired storyboard context: {local_action}"
        )
        response = llm_service.chat_completion(
            model=config.get("models.video_review.endpoint"),
            messages=[{"role": "user", "content": prompt}], temperature=.1, max_tokens=1200)
        text = response["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        result = json.loads(text)
        if (not isinstance(result.get("goal"), str) or not result["goal"].strip()
                or not isinstance(result.get("checks"), list) or not 1 <= len(result["checks"]) <= 6
                or any(not isinstance(c, str) or not c.strip() for c in result["checks"])
                or not isinstance(result.get("motion"), str) or not result["motion"].strip()):
            raise ValueError("Repair goal must specify a visible corrected state and checks")
        return result

    def audit_story_impact(self, url, script, report, frame_evidence=None):
        """Require a visible causal failure before treating stylistic differences as blockers."""
        allegations = report["issues"]
        instructions = (
            "Perform a MODERATE finished-film continuity audit. Earlier issue labels are "
            "allegations, not established facts. The goal is a coherent film under its declared "
            "genre rules. Independently inspect the relevant preceding and subsequent shots. "
            "Preserve real anatomy/contact/identity failures, contradictory dialogue and loss "
            "of indispensable causality. Distinguish normal fantasy awakening or sword-power "
            "effects from malformation or replacement of a character's identity; an unlisted "
            "power is not forbidden. Distinguish a recognizable decorative-token variation "
            "from an actual prop-identity or causal contradiction; inspect recognition and final "
            "connection in the actual footage, including later shots. Never infer that a "
            "connection is absent from an unrelated earlier close-up. A different camera "
            "choreography or effect treatment is not by itself a causal failure. "
            "Do not approve merely to save work. Each decision must identify actual observations, "
            "not a hypothetical viewer interpretation or just a difference from the script. "
            "Return ONLY JSON {\"decisions\":[{\"index\":integer,\"blocking\":boolean,"
            "\"confidence\":0..1,\"observations\":string,\"story_consequence\":string}],"
            "\"summary\":string}. Give exactly one decision for each allegation in order.\n"
            "Story context:\n" + json.dumps(script, ensure_ascii=False, default=str)
            + "\nAllegations:\n" + json.dumps(allegations, ensure_ascii=False)
            + MODERATE_CAUSAL_STANDARD
            + DIGIT_EVIDENCE
        )
        content = [{"type": "text", "text": instructions},
                   review_video_input(url, "video_optimization.review_fps")]
        content += [{"type": "image_url", "image_url": {"url": item["url"]}}
                    for item in (frame_evidence or [])]
        result = llm_service.chat_completion(
            model=config.get("models.video_review.endpoint"),
            messages=[{"role": "user", "content": content}], temperature=.1,
            **(anatomy_review_options() if any(concerns_digits(i.get("evidence"))
               for i in allegations) else {"max_tokens": 2500}))
        text = result["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        audit = json.loads(text)
        decisions = audit.get("decisions", [])
        if (len(decisions) != len(allegations)
                or any(not isinstance(d, dict) for d in decisions)
                or [d.get("index") for d in decisions] != list(range(len(allegations)))):
            raise ValueError("Impact audit must cover every allegation exactly once")
        for decision in decisions:
            confidence = float(decision.get("confidence", -1))
            if (not isinstance(decision.get("blocking"), bool)
                    or not decision.get("observations") or not decision.get("story_consequence")
                    or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                raise ValueError("Impact audit returned invalid observations")
        audited = copy.deepcopy(report)
        audited["impact_audit"] = dict(audit, reviewed_issues=allegations)
        audited["summary"] = audit.get("summary", report.get("summary", ""))
        audited["issues"] = []
        threshold = float(config.get("video_optimization.confidence_threshold", .85))
        for issue, decision in zip(allegations, decisions):
            if decision["blocking"] or float(decision["confidence"]) < threshold:
                audited["issues"].append(issue)
            else:
                audited.setdefault("advisories", []).append(dict(
                    issue, severity="minor", impact_observations=decision["observations"],
                    story_impact=decision["story_consequence"]))
        if not audited["issues"]:
            audited["approved"] = True
        logger.info("[OPTIMIZE] impact audit allegations=%s blocking=%s",
                    len(allegations), len(audited["issues"]))
        return audited

    def review(self, url, script, transcript, references, candidates=None):
        started = time.monotonic()
        fast = fast_triage_enabled()
        evidence = candidates.get("frame_evidence", []) if isinstance(candidates, dict) else []
        candidate_context = {k: v for k, v in candidates.items() if k != "frame_evidence"} if isinstance(candidates, dict) else candidates
        instructions = (
            "Review the ACTUAL complete film against the storyboard and character references. "
            "Return ONLY JSON: {\"approved\":boolean,\"summary\":string,\"issues\":["
            "{\"start\":seconds,\"end\":seconds,"
            "\"category\":\"identity|anatomy|contact|prop|plot|lighting|dialogue\",\"evidence\":string,"
            "\"confidence\":0..1,\"safe_to_remove\":boolean,\"essential_story_beat\":boolean,"
            "\"confirmed\":boolean,\"severity\":\"minor|major|critical\","
            "\"story_impact\":string}]}. Issues must be localized character identity, plot, "
            "storyboard or physical-law violations with concrete observable temporal evidence. "
            "Use the fixed English category values even when writing Chinese evidence. "
            "Each issue must identify ONE specific visible occurrence lasting at most 6 seconds. "
            "Never label an entire film or a 20-second span because a prop recurs. "
            "For a recurring prop fault, identify its decisive close-up only. For a missing "
            "causal beat, identify the short shot where it should be repaired. "
            "Do not combine several distinct actions into one long issue. "
            "Inspect anatomy in motion: extra limbs/fingers, fused hands, face deformation, "
            "impossible grips, penetration through objects, contact and object permanence. "
            "Inspect continuity of identity, costume, prop positions, screen direction and "
            "motivated light sources across adjacent shots. Flag unexplained lighting/palette changes "
            "while preserving scripted switches, lightning and supernatural genre rules. "
            "Do not report low resolution, deliberate holds or genre conventions as faults. "
            "Judge locked story/action semantics and causal continuity, rather than literal "
            "camera choreography. Harmless framing or tiny decorative prop-position differences "
            "are not plot failures. Only causally indispensable actions are essential beats. "
            "Apply a moderate finishing standard: if a character's identity, motive, central "
            "action and outcome remain coherent, missing a redundant foreshadowing detail, "
            "reaction gesture or a minor action-order variation is not a repair-worthy failure. "
            "Do not turn every scripted decorative visual or camera beat into an essential "
            "story event. Keep deliberate magic and supernatural effects valid under their "
            "declared genre rules. In fantasy, awakening glows and a demon's transforming claws "
            "are allowed unless they contradict an explicit prohibition or create a visible "
            "causal failure. The absence of a listed ability is not a prohibition. "
            "A decorative pattern variation on a consistently identifiable token is minor; "
            "do not claim it breaks recognition without visible confusion or contradiction. "
            "Severity: minor means the viewer still understands who acts, what happens and why; "
            "major means a clearly observable defect disrupts identity, anatomy, contact or an "
            "indispensable cause/outcome; critical means the core story cannot be understood. "
            "Explain the actual consequence in story_impact, not just 'differs from the script'. "
            "Still reject clear anatomy defects, material identity/prop "
            "changes, impossible unintended contact and contradictions of indispensable events. "
            "Track each distinctive story prop's visible location, owner and material state "
            "across the complete film, including brief inserts between dialogue scenes. "
            "A prop visibly inside a fire, destroyed or abandoned cannot later reappear intact "
            "in a character's pocket without an observable retrieval or a justified time jump. "
            "Check the actual insert before calling its introduction consistent; ordinary "
            "firelight on a held prop and a different background object are not contradictions. "
            "Do not recommend removing dialogue or essential causality. Maximum cut length 3s. "
            "Use the provided actual ASR transcript for dialogue evidence: check repeated lines, "
            "reversed question/answer order and core meaning that contradicts the storyboard, "
            "with concrete utterance timestamps. Minor ASR spelling ambiguities are not confirmed "
            "content failures. ASR can normalize "
            "Cantonese into written Chinese; a zh label or Standard Chinese spelling alone "
            "does not prove Mandarin pronunciation. Do not invent an audio-language failure "
            "from spelling or from dialogue that is not accessible to this visual review. "
            "If verification_windows are provided, independently inspect them and the adjoining "
            "action without inferring an accusation. Write fresh observed evidence. "
            "Timestamped master-frame sheets, when attached, are sequential observations of the "
            "ACTUAL same film, not character reference images. Inspect every labeled frame before "
            "claiming a short action is absent. Distinguish an action that is absent from an action "
            "that is present with a different decorative pattern or grip position. An obscured "
            "engraving is not proof of a different engraving. Do not infer a missing contact "
            "from the character leaving after making contact. "
            "Rewatch the complete relevant sequence before claiming an action, effect or outcome "
            "is missing; a narrow time window or sparse frames cannot establish its absence. "
            "If reviewing an edited film, allow documented safe omissions and evaluate remaining story coherence.\n"
            + video_skill_service.guidance("optimize")
            + "\nStoryboard:\n" + json.dumps(script, ensure_ascii=False, default=str)
            + "\nActual transcript:\n" + json.dumps(transcript, ensure_ascii=False)
            + "\nCandidate evidence / edit history:\n" + json.dumps(candidate_context, ensure_ascii=False)
            + MODERATE_CAUSAL_STANDARD
            + DIGIT_EVIDENCE
        )
        if fast:
            compact_script = {key: script.get(key) for key in
                              ("title", "style", "background", "production_requirements")}
            compact_script["scenes"] = [{
                key: scene.get(key) for key in
                ("scene_number", "description", "dialogue", "characters_present", "character_outfits")
            } for scene in script.get("scenes", [])]
            instructions = (
                "Perform ONE QUICK editorial review of the actual film. In this same pass, "
                "first decide whether each faulty short fragment is indispensable to the "
                "story, then rate its actual local quality from 0 to 100. Key plot means a "
                "cause, reveal, essential dialogue or outcome whose removal makes the story "
                "incomprehensible. A decorative pattern, exact camera move, redundant reaction, "
                "pose or genre effect is not indispensable. Do not report harmless variations. "
                "Noncritical faulty fragments will be DELETED; consider their incoming/outgoing "
                "story state and all touched spoken utterances before marking them noncritical. "
                "Set dialogue_disposable=true ONLY if every complete utterance touched by the "
                "cut is redundant and can be removed without losing key information. Key dialogue "
                "makes the fragment critical. Never recommend half-utterance cuts. "
                "Critical fragments are retained unless quality_score is BELOW "
                f"{config.get('video_optimization.critical_regenerate_below', 40)} AND "
                "viewer_unacceptable=true: unmistakable gross anatomy, unrecognizable identity, "
                "impossible main action, or contradictory essential causality that a viewer "
                "cannot accept. Ordinary imperfect hands, minor continuity, weaker spectacle "
                "and nonliteral choreography are not that level. Preserve the declared fantasy "
                "rules. Rate the actual fragment, not the whole film; do not lower a score just "
                "to cause regeneration. Confirm only clear timed observations with confidence, "
                "not speculative risk. Count digits on the SAME palm by distinct contours and "
                "attachments; occlusion or an ordinary thumb is not an extra/missing finger. "
                "An obscured engraving is not proof of a different engraving. "
                "Inspect adjacent shots for motivated lighting and object/identity continuity. "
                "Track distinctive story props through brief inserts too: their location, "
                "owner and material state must agree with later shots. A prop visibly inside "
                "a fire or destroyed cannot reappear intact in a pocket without retrieval "
                "or a motivated time jump. This differs from ordinary firelight reflected "
                "on a held prop or an unrelated background object. A clearly contradictory "
                "decorative insert can be noncritical and removable; harmless variations remain valid. "
                "Use ACTUAL ASR for dialogue, not invented sound or script substitutions. "
                "ASR is fallible: absence or odd spelling alone does not establish a spoken "
                "contradiction. Missing redundant greeting lines are advisory when the visible "
                "relationship and core later dialogue remain coherent. Caption-only text errors "
                "must be corrected by transcription and subtitle rendering, never by picture "
                "regeneration that preserves the same soundtrack. Do not request a picture "
                "reshoot solely from an ASR or caption error. "
                "Existing cut/edit context is authorized. Character images are identity references. "
                "Return concise JSON ONLY: {\"approved\":boolean,\"summary\":string,\"issues\":["
                "{\"start\":seconds,\"end\":seconds,\"category\":\"identity|anatomy|contact|prop|plot|lighting|dialogue\","
                "\"evidence\":string,\"confidence\":0..1,\"essential_story_beat\":boolean,"
                "\"quality_score\":0..100,\"viewer_unacceptable\":boolean,\"dialogue_disposable\":boolean,"
                "\"safe_to_remove\":boolean,\"confirmed\":boolean,\"severity\":\"minor|major|critical\","
                "\"story_impact\":string}]}. Each occurrence lasts at most 6 seconds. "
                "Keep observations concise. No extra audit, exhaustive checklist or chain of thought.\n"
                "Story:\n" + json.dumps(compact_script, ensure_ascii=False, default=str)
                + "\nActual speech:\n" + json.dumps(transcript, ensure_ascii=False)
                + "\nEdits/windows:\n" + json.dumps(candidate_context, ensure_ascii=False)
            )
        if isinstance(candidates, dict) and candidates.get("subtitles", True):
            instructions += (
                "\nAUTHORIZED FINAL FINISHING REQUIREMENT: synchronized transcription subtitles "
                "in the actual soundtrack language are REQUIRED by the user. Storyboard instructions "
                "'no embedded subtitles' apply to raw model-generated shots only. Do not reject "
                "the final film for these approved post-production captions; evaluate their timing "
                "and actual audio text. Do not confuse captions with an unrequested title card."
            )
        content = [{"type": "text", "text": instructions},
                   review_video_input(url, "video_optimization.review_fps")]
        if evidence:
            content.append({"type": "text", "text": "ACTUAL MASTER: timestamped sequential frames for independent verification."})
            content += [{"type": "image_url", "image_url": {"url": item["url"]}} for item in evidence]
        if references:
            content.append({"type": "text", "text": "CHARACTER IDENTITY REFERENCES (not footage from the master):"})
        content += [{"type": "image_url", "image_url": {"url": u}} for u in references]
        result = llm_service.chat_completion(
            model=config.get("models.video_review.endpoint"), messages=[{"role": "user", "content": content}],
            temperature=.1,
            **({"thinking": {"type": "disabled"},
                "max_tokens": int(config.get("video_optimization.fast_review_max_tokens", 1600))}
               if fast else
               ({"max_tokens": int(config.get("video_optimization.review_max_tokens", 4000))}
                | (anatomy_review_options() if isinstance(candidates, dict)
                   and candidates.get("verification_windows") else {}))))
        text = result["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        report = json.loads(text)
        if not isinstance(report.get("issues"), list) or not isinstance(report.get("approved"), bool):
            raise ValueError("Optimization review returned invalid schema")
        for issue in report["issues"]:
            if not isinstance(issue, dict):
                raise ValueError("Optimization issue must contain timestamped evidence")
            try:
                a, b, certainty = float(issue["start"]), float(issue["end"]), float(issue["confidence"])
                valid = all(math.isfinite(n) for n in (a, b, certainty)) and 0 <= a < b and 0 <= certainty <= 1
            except (TypeError, ValueError, KeyError):
                valid = False
            if not valid:
                raise ValueError("Optimization issue has invalid timecodes or confidence")
        logger.info("[OPTIMIZE] review mode=%s elapsed=%.2fs issues=%s",
                    "fast_triage" if fast else "detailed", time.monotonic() - started, len(report["issues"]))
        if fast:
            return triage_report(report)
        rank = {"minor": 0, "major": 1, "critical": 2}
        minimum = rank.get(str(config.get("video_optimization.repair_min_severity", "major")), 1)
        advisories = [i for i in report["issues"] if rank.get(i.get("severity", "major"), 1) < minimum]
        if advisories:
            report["issues"] = [i for i in report["issues"] if i not in advisories]
            report["advisories"] = advisories
            # An explicit minor-only report has no blocking production defects.
            if not report["issues"]:
                report["approved"] = True
        if (report["issues"] and isinstance(candidates, dict)
                and candidates.get("verification_windows")
                and config.get("video_optimization.audit_story_impact", True)):
            try:
                report = self.audit_story_impact(url, script, report, evidence)
            except Exception as error:
                # An unavailable or malformed audit cannot release blocking footage.
                report["impact_audit_error"] = type(error).__name__
                logger.warning("Story-impact audit unavailable: %s", type(error).__name__)
        return report

    def transcribe(self, source, directory, requirement_text="", timeline=None, check_active=None):
        import whisper
        import numpy as np
        active = check_active or (lambda: None)
        active()
        key = hashlib.sha256(source.read_bytes()).hexdigest()
        model_name = str(config.get("video_optimization.whisper_model", "small"))
        cantonese = requests_cantonese(requirement_text)
        native = cantonese or (bool(config.get("video_optimization.native_chinese_asr", True)) and
            bool(re.search(r"中文(?:对白|對白|旁白|对话|對話)|普通话|普通話|国语|國語|mandarin|chinese\s+(?:dialogue|speech|voice)",
                           requirement_text, re.IGNORECASE)))
        if cantonese:
            model_name = str(config.get("video_optimization.whisper_cantonese_model", "turbo"))
        cache = directory / "transcript.json"
        cached_audio = None
        if cache.exists():
            data = json.loads(cache.read_text())
            same_settings = (data.get("whisper_model") == model_name
                    and data.get("transcription_revision") == 5
                    and data.get("cantonese_required") == cantonese
                    and data.get("native_asr_required") == native
                    and (not native or data.get("subtitle_source") == "seed_speech"))
            if data.get("source_sha256") == key and same_settings:
                return data
            if same_settings:
                cached_audio = data
        audio = whisper.load_audio(str(source))
        audio_key = hashlib.sha256(audio.tobytes()).hexdigest()
        if cached_audio and cached_audio.get("audio_sha256") == audio_key:
            cached_audio["source_sha256"] = key
            cache.write_text(json.dumps(cached_audio, ensure_ascii=False, indent=2))
            logger.info("[OPTIMIZE] Reused exact unchanged audio transcript after picture repair")
            return cached_audio
        if len(audio) == 0 or np.sqrt(np.mean(audio ** 2)) < .0005:
            data = {"segments": [], "language": None}
        else:
            data, cloud_error = None, None
            if native:
                try:
                    from app.services.asr_service import asr_service
                    wav = subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-vn",
                        "-ar", "16000", "-ac", "1", "-f", "wav", "-"], capture_output=True, check=True).stdout
                    active()
                    url = tos_service.upload_bytes(wav, filename=f"soundtrack-{key[:12]}.wav",
                        content_type="audio/wav", project_id=directory.name, category="videos/optimization")
                    result = asr_service.recognize_result(url, format="wav",
                        request_params={"enable_itn": False, "enable_punc": True}, check_active=active)
                    active()
                    segments = timed_asr_segments(result, len(audio)/16000)
                    records = []
                    for scene in (timeline or []) if cantonese else []:
                        text = "".join(s["text"] for s in segments
                            if s["start"] < scene["end"] and s["end"] > scene["start"])
                        if text:
                            markers = sorted(set(re.findall(r"[啲嘅喺嚟咗哋嗰㗎唔冇咁]", text)))
                            records.append(dict(scene_number=scene["scene_number"], evidence_words=markers,
                                status="cantonese_evidence" if markers else "uncertain"))
                    data = {"segments": segments, "language": "yue" if any(
                        r["status"] == "cantonese_evidence" for r in records) else "zh",
                        "subtitle_source": "seed_speech", "dialect_audit": records}
                    logger.info("[OPTIMIZE] Native timed ASR: segments=%s dialect=%s",
                        len(segments), [(r["scene_number"], r["status"]) for r in records])
                except Exception as error:
                    active()
                    cloud_error = type(error).__name__
                    logger.warning("Native timed transcription unavailable: %s", cloud_error)
            if data is None:
                logger.info("Whisper transcribing actual soundtrack, samples=%s", len(audio))
                model = whisper.load_model(model_name,
                    device=str(config.get("video_optimization.whisper_device", "cpu")),
                    download_root=str(Path(config.get("video_optimization.whisper_cache_dir")).expanduser()))
                data = model.transcribe(audio, word_timestamps=True, fp16=False, verbose=False,
                    condition_on_previous_text=False)
                minimum_logprob = float(config.get(
                    "video_optimization.whisper_min_avg_logprob", -1.0))
                original_segments = data["segments"]
                data["segments"] = [s for s in data["segments"]
                    if float(s.get("no_speech_prob", 0)) < .7 and str(s.get("text", "")).strip()
                    and math.isfinite(float(s.get("avg_logprob", 0)))
                    and float(s.get("avg_logprob", 0)) >= minimum_logprob
                    and float(s["end"]) > float(s["start"])]
                logger.info("[OPTIMIZE] Whisper retained=%s discarded_unreliable=%s",
                            len(data["segments"]), len(original_segments) - len(data["segments"]))
                for segment in data["segments"]:
                    segment["words"] = [w for w in segment.get("words", [])
                        if str(w.get("word", "")).strip() and float(w["end"]) > float(w["start"])]
                data["text"] = "".join(s["text"] for s in data["segments"])
                data["subtitle_source"] = "whisper"
                if cloud_error:
                    data["cloud_asr_error"] = cloud_error
                    data["dialect_review"] = "uncertain"
        data["source_sha256"] = key
        data["audio_sha256"] = audio_key
        data["whisper_model"] = model_name
        data["transcription_revision"] = 5
        data["cantonese_required"] = cantonese
        data["native_asr_required"] = native
        cache.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return data

    def verify_narration_leaks(self, source, directory, segments, script, timeline, check_active):
        """An independent ASR must agree before any unrequested speech is removed."""
        import whisper
        candidates = narration_leak_candidates(segments, script, timeline)
        if not candidates:
            return []
        cache = directory/"audio-audit.json"
        key = hashlib.sha256(source.read_bytes()).hexdigest()
        context = hashlib.sha256(json.dumps(candidates, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if cache.exists():
            data = json.loads(cache.read_text())
            if data.get("source_sha256") == key and data.get("context_sha256") == context:
                return data["verified_ranges"]
        audio = whisper.load_audio(str(source))
        model = whisper.load_model("turbo", device=str(config.get("video_optimization.whisper_device", "cpu")),
            download_root=str(Path(config.get("video_optimization.whisper_cache_dir")).expanduser()))
        verified, audits = [], []
        han = lambda text: "".join(re.findall(r"[\u4e00-\u9fff]", text))
        for candidate in candidates:
            check_active()
            a, b = candidate["start"], candidate["end"]
            result = model.transcribe(audio[int(a*16000):int(b*16000)],
                language="zh", word_timestamps=True, fp16=False, verbose=False,
                condition_on_previous_text=False)
            primary = han("".join(s["text"] for s in candidate["utterances"]))
            independent = han(result["text"])
            agreed = SequenceMatcher(None, primary, independent, autojunk=False).find_longest_match().size
            audits.append(dict(start=a, end=b, independent_text=result["text"], agreed_characters=agreed))
            if agreed >= 8:
                verified.append(dict(start=a, end=b, scene_number=candidate["scene_number"],
                    evidence="Two ASRs agree on spoken production description in a shot with no dialogue",
                    actual_text="".join(s["text"] for s in candidate["utterances"])))
        cache.write_text(json.dumps(dict(source_sha256=key, context_sha256=context,
            verified_ranges=verified, audits=audits), ensure_ascii=False, indent=2))
        return verified

    def repair_narration_audio(self, source, target, ranges, segments, duration):
        """Replace leaked speech with verified nearby room tone; picture and timing stay fixed."""
        import numpy as np
        sample_rate = 48000
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-vn",
            "-ar", str(sample_rate), "-ac", "2", "-f", "f32le", "-"],
            capture_output=True, check=True).stdout
        audio = np.frombuffer(raw, dtype="<f4").reshape(-1, 2).copy()
        forbidden = [(float(s["start"])-.2, float(s["end"])+.2) for s in segments]
        forbidden += [(r["start"]-.2, r["end"]+.2) for r in ranges]
        for interval in ranges:
            a, b = interval["start"], interval["end"]
            # Choose a complete, quiet window near this scene, never another spoken line.
            windows = [(t, t+.8) for t in np.arange(max(0, a-10), min(duration-.8, b+10), .4)
                       if not any(t < end and t+.8 > start for start, end in forbidden)]
            if not windows:
                raise ValueError("No speech-free room tone available for local audio repair")
            t, u = min(windows, key=lambda w: abs((w[0]+w[1])/2-(a+b)/2)+
                20*float(np.sqrt(np.mean(audio[int(w[0]*sample_rate):int(w[1]*sample_rate)]**2))))
            tone = audio[int(t*sample_rate):int(u*sample_rate)].copy()
            # Ping-pong a low-level ambience bed without a hard waveform jump on loop boundaries.
            bed = np.concatenate((tone, tone[::-1]))*.7
            left, right = int(a*sample_rate), min(len(audio), int(b*sample_rate))
            replacement = np.tile(bed, (math.ceil((right-left)/len(bed)), 1))[:right-left]
            fade = min(int(.05*sample_rate), (right-left)//2)
            weight = np.ones(right-left, dtype=np.float32)
            weight[:fade] = np.linspace(0, 1, fade)
            weight[-fade:] = np.linspace(1, 0, fade)
            audio[left:right] = audio[left:right]*(1-weight[:, None])+replacement*weight[:, None]
            interval["room_tone_start"], interval["room_tone_end"] = float(t), float(u)
        pcm = target.with_suffix(".f32")
        pcm.write_bytes(audio.astype("<f4").tobytes())
        try:
            command(["ffmpeg", "-v", "error", "-y", "-i", source, "-f", "f32le",
                "-ar", sample_rate, "-ac", "2", "-i", pcm, "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", duration,
                "-movflags", "+faststart", target])
        finally:
            pcm.unlink(missing_ok=True)

    def edit(self, source, target, kept, has_audio, lighting=None):
        parts = []
        for index, (start, end) in enumerate(kept):
            part = target.parent / f"part-{index}.mp4"
            args = ["ffmpeg", "-v", "error", "-y", "-ss", start, "-reinit_filter", "0",
                    "-i", source, "-t", end-start,
                    "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-pix_fmt", "yuv420p"]
            if lighting:
                entry = next((r for r in lighting if r["start"] <= (start+end)/2 <= r["end"]), {})
                args += ["-vf", f"setsar=1,fps=24,eq=contrast=1.015:saturation=0.98:brightness={entry.get('brightness', 0):.5f}"]
            command(args + [part])
            parts.append(part)
        listing = target.parent / "concat.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
        picture = target.parent / "edited-picture.mp4"
        command(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing,
                 "-c", "copy", picture])
        expected = sum(end-start for start, end in kept)
        args = ["ffmpeg", "-v", "error", "-y", "-i", picture]
        try:
            if has_audio:
                args += ["-i", source]
                continuous = (abs(kept[0][0]) < .00001
                    and abs(kept[-1][1]-float(probe(source)["format"]["duration"])) < .00001
                    and all(abs(kept[i-1][1]-kept[i][0]) < .00001 for i in range(1, len(kept))))
                if continuous:
                    # Light corrections do not change the soundtrack's timeline.
                    args += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy"]
                else:
                    filters = []
                    for index, (start, end) in enumerate(kept):
                        chain = f"[1:a:0]atrim=start={start}:end={end},asetpts=PTS-STARTPTS"
                        if index == 0 or abs(kept[index-1][1]-start) > .00001:
                            chain += ",afade=t=in:d=0.03"
                        if index == len(kept)-1 or abs(end-kept[index+1][0]) > .00001:
                            chain += f",afade=t=out:st={max(0,end-start-.03)}:d=0.03"
                        filters.append(chain+f"[a{index}]")
                    filters.append("".join(f"[a{i}]" for i in range(len(kept)))
                                   +f"concat=n={len(kept)}:v=0:a=1[audio]")
                    args += ["-filter_complex", ";".join(filters), "-map", "0:v:0",
                             "-map", "[audio]", "-c:a", "aac", "-ar", "48000"]
            else:
                args += ["-map", "0:v:0", "-an"]
            command(args+["-c:v", "copy", "-t", expected, "-movflags", "+faststart", target])
            actual = float(probe(target)["format"]["duration"])
            if abs(actual - expected) > .25:
                raise RuntimeError("Edited video duration does not match the retained timeline")
        finally:
            for part in parts:
                part.unlink(missing_ok=True)
            listing.unlink(missing_ok=True)
            picture.unlink(missing_ok=True)

    def render_subtitles(self, source, target, segments, info, directory):
        started = time.monotonic()
        runtime = Path(str(config.get("video_skills.runtime_dir"))).expanduser()
        renderer = runtime / "subtitle-renderer"
        renderer.mkdir(exist_ok=True)
        template = Path(__file__).parents[1] / "renderers"
        for name in ("captions.tsx", "render.mjs"):
            shutil.copyfile(template / name, renderer / name)
        public = renderer / "public"
        public.mkdir(exist_ok=True)
        # Unique names allow independent projects to render concurrently.
        name = directory.name + "-edited.mp4"
        shutil.copyfile(source, public / name)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        duration = float(probe(source)["format"]["duration"])
        captions = []
        max_line_chars = max(6, min(28, int(int(video["width"]) * .85 / (int(video["height"]) * .05))))
        for segment in segments:
            words = segment.get("words", [])
            if not words:
                words = [{"word": segment["text"], "start": segment["start"], "end": segment["end"]}]
            line_start, line_length = float(words[0]["start"]), 0
            for index, w in enumerate(words):
                line_length += len(w["word"])
                next_word = words[index+1] if index+1 < len(words) else None
                page_break = (next_word is None or float(w["end"])-line_start >= 4
                              or line_length >= max_line_chars or w["word"].strip().endswith(("。", "！", "？", ".", "!", "?"))
                              or float(next_word["start"])-float(w["end"]) > .4)
                captions.append({"text": w["word"], "startMs": round(w["start"]*1000),
                                 "endMs": round(w["end"]*1000), "timestampMs": round(w["start"]*1000),
                                 "confidence": float(w.get("probability", 1)), "pageBreakAfter": page_break})
                if page_break and next_word:
                    line_start, line_length = float(next_word["start"]), 0
        props = {"source": name, "width": int(video["width"]), "height": int(video["height"]),
                 "fps": 25, "durationInFrames": math.ceil(duration*25), "captions": captions,
                 "subtitleRenderer": config.get("video_optimization.subtitle_renderer", "ffmpeg")}
        props_file = directory / "caption-props.json"
        props_file.write_text(json.dumps(props, ensure_ascii=False))
        rendered = directory / "caption-rendered.mp4"
        try:
            command(["node", renderer / "render.mjs", props_file, rendered, directory / "subtitles.srt"], cwd=renderer)
            # Remotion's audio encoder may pad its AAC output beyond the last
            # picture frame. Keep the edited soundtrack and enforce the actual
            # timeline duration instead of publishing that padded render.
            args = ["ffmpeg", "-v", "error", "-y", "-i", rendered, "-i", source,
                    "-map", "0:v:0"]
            if any(s["codec_type"] == "audio" for s in info["streams"]):
                args += ["-map", "1:a:0", "-c:a", "copy"]
            else:
                args += ["-an"]
            command(args + ["-c:v", "copy", "-t", duration, "-movflags", "+faststart", target])
        finally:
            (public / name).unlink(missing_ok=True)
            rendered.unlink(missing_ok=True)
        (directory / "captions.json").write_text(json.dumps(captions, ensure_ascii=False, indent=2))
        metrics = {"renderer": props["subtitleRenderer"],
                   "elapsed_seconds": round(time.monotonic() - started, 3),
                   "caption_tokens": len(captions)}
        logger.info("[OPTIMIZE] subtitles renderer=%s elapsed=%.3fs tokens=%s",
                    metrics["renderer"], metrics["elapsed_seconds"], len(captions))
        return metrics

    def optimize(self, project, check_active, review_only=False, report_progress=None):
        started = time.monotonic()
        stage_starts, stage_seconds = {}, {}
        def notify(stage, status):
            now = time.monotonic()
            if status == "running":
                stage_starts.setdefault(stage, now)
            elif stage in stage_starts:
                elapsed = now - stage_starts.pop(stage)
                stage_seconds[stage] = round(stage_seconds.get(stage, 0) + elapsed, 3)
                logger.info("[OPTIMIZE] stage=%s status=%s elapsed=%.3fs", stage, status, elapsed)
            if report_progress:
                report_progress(stage, status)
        fast = fast_triage_enabled()
        check_active()
        notify("source", "running")
        output_root = Path(str(config.get("video_optimization.output_dir", "outputs"))).resolve()
        directory = (output_root / project.project_id).resolve()
        if directory.parent != output_root:
            raise ValueError("Invalid local output project identifier")
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / "merged-original.mp4"
        working_url = project.optimization_source_url or project.merged_video_url
        master_id = hashlib.sha256(urlparse(working_url).path.encode()).hexdigest()
        identity = directory / "master-id.txt"
        if source.exists() and (not identity.exists() or identity.read_text() != master_id):
            source.rename(directory / f"merged-original-{datetime.now():%Y%m%d%H%M%S}.mp4")
        if not source.exists():
            self.download(working_url, source, check_active)
            identity.write_text(master_id)
        info = probe(source)
        duration = float(info["format"]["duration"])
        has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
        timeline = self.scene_timeline(project, duration, check_active)
        notify("source", "completed")
        notify("transcribe", "running")
        transcript = self.transcribe(source, directory, project.combined_input or project.user_input,
            timeline, check_active) if has_audio else {"segments": [], "language": None}
        notify("transcribe", "completed")
        raw_segments = transcript["segments"]
        transcript = dict(transcript)
        transcript["segments"], subtitle_corrections = subtitle_homophones(
            raw_segments, project.script, timeline, transcript.get("language"))
        audio_repairs = []
        if (has_audio and transcript.get("subtitle_source") == "seed_speech"
                and transcript.get("language") == "zh"
                and config.get("video_optimization.remove_prompt_narration", True)
                and narration_leak_candidates(raw_segments, project.script, timeline)):
            notify("audio_review", "running")
            try:
                audio_repairs = self.verify_narration_leaks(
                    source, directory, raw_segments, project.script, timeline, check_active)
            except Exception as error:
                check_active()
                logger.warning("Independent audio audit unavailable: %s", type(error).__name__)
            notify("audio_review", "completed" if audio_repairs else "needs_review")
        check_active()
        script = project.script.model_dump(mode="json")
        script["production_requirements"] = project.combined_input or project.user_input
        (directory / "storyboard.json").write_text(json.dumps(script, ensure_ascii=False, indent=2))
        references = [im.url for im in project.character_reference_images]
        settings = project.optimization_settings
        pending_faults = (project.optimization_report or {}).get("pending_faults", [])
        manual_cuts = validate_ranges([r.model_dump() for r in settings.cuts], transcript["segments"], duration)
        validate_ranges([r.model_dump() for r in settings.repair_ranges], [], duration, 1)
        reorder_ranges([(0, duration)], timeline, settings.scene_order)
        report = {"skills": video_skill_service.manifest("optimize"), "input_duration": duration,
                  "review_mode": "fast_triage" if fast else "detailed",
                  "reviewed_history_length": len(getattr(project, "optimization_history", [])),
                  "audio_language": transcript.get("language"), "review_status": "available",
                  "source_sha256": transcript.get("source_sha256", hashlib.sha256(source.read_bytes()).hexdigest()),
                  "review_context_sha256": review_context_key(script, references),
                  "transcript_sha256": hashlib.sha256(json.dumps(
                      transcript["segments"], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                  "pending_faults_sha256": hashlib.sha256(json.dumps(
                      pending_faults, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                  "pending_faults": pending_faults,
                  "scene_sources_sha256": scene_sources_key(project),
                  "scene_timeline": timeline, "edit_settings": settings.model_dump(),
                  "dialect_audit": transcript.get("dialect_audit", []),
                  "stage_seconds": stage_seconds,
                  "subtitle_source": transcript.get("subtitle_source", "whisper"),
                  "subtitle_corrections": subtitle_corrections, "audio_repairs": audio_repairs,
                  "transcription_warning": transcript.get("cloud_asr_error"),
                  "local_source": str(source),
                  "comparison_video_url": project.merged_video_url}
        notify("review", "running")
        try:
            previous = project.optimization_report or {}
            cache_keys = ("source_sha256", "review_context_sha256", "transcript_sha256", "pending_faults_sha256")
            checkpoint = directory / "optimization-checkpoint.json"
            if checkpoint.exists():
                try:
                    cached = json.loads(checkpoint.read_text())
                except (OSError, ValueError):
                    cached = {}
                if (isinstance(cached, dict) and cached.get("verified_review")
                        and all(cached.get(k) == report[k] for k in cache_keys)):
                    previous = cached
            if (all(previous.get(k) == report[k] for k in cache_keys)
                    and previous.get("verified_review")):
                initial, verified = previous["initial_review"], previous["verified_review"]
                report["original_review_cached"] = True
            elif fast and (reused := reuse_verified_patch_review(
                    previous, report, getattr(project, "optimization_history", []))) is not None:
                initial = verified = reused
                report["source_review_reused_after_patches"] = True
            elif (fast and pending_faults
                    and previous.get("review_context_sha256") == report["review_context_sha256"]
                    and all(i.get("fast_triage_evaluated") is True for i in pending_faults)):
                # These are already observed final-film findings mapped back to this
                # unchanged source timeline; another full-film call adds no evidence.
                initial = verified = triage_report({"approved": False, "issues": pending_faults})
                report["source_review_reused_final_findings"] = True
            else:
                initial = self.review(working_url, script, transcript["segments"], references, pending_faults or None)
                notify("review", "completed")
                if not fast and initial["issues"]:
                    notify("confirm", "running")
                verification = independent_review_windows(initial["issues"])
                if not fast and initial["issues"]:
                    verification["frame_evidence"] = self.window_evidence(
                        source, directory, verification["verification_windows"], check_active)
                    report["verification_frame_evidence"] = verification["frame_evidence"]
                verified = self.review(working_url, script, transcript["segments"], references,
                                       verification) if not fast and initial["issues"] else initial
            if fast:
                if verified.get("review_mode") != "fast_triage":
                    verified = triage_report(verified)
                notify("triage", "running")
                report["triage_decisions"] = verified.get("triage_decisions", [])
                notify("triage", "completed")
            report.update(initial_review=initial, verified_review=verified)
            # Verification may discover new issues; those need their own confirmation.
            issues = (verified["issues"] if fast else [i for i in verified["issues"] if any(
                matching_review_issue(i, c) for c in initial["issues"])])
        except Exception as error:
            logger.warning("Optimization review unavailable: %s", type(error).__name__)
            report.update(review_status="unavailable", review_error=type(error).__name__)
            issues = []
        notify("review", "completed" if report["review_status"] == "available" else "unavailable")
        if not fast and (report.get("initial_review", {}).get("issues") or
                (report["review_status"] == "unavailable" and 'initial' in locals() and initial.get("issues"))):
            notify("confirm", "completed" if report["review_status"] == "available" else "unavailable")
        check_active()
        if fast:
            removable = [dict(i, safe_to_remove=True) for i in issues if i.get("action") == "cut"]
            cuts, kept, rejected = safe_cut_plan(
                removable, transcript["segments"], duration,
                max_seconds=float(config.get("video_optimization.fast_max_cut_seconds", 6)),
                allow_disposable_dialogue=True)
            # A cut blocked by a duration/utterance guard must never turn into
            # automatic generation of a noncritical fragment.
            report["retained_cut_limits"] = [dict(i, action="retain", decision_reason="cut_continuity_limit")
                                            for i in rejected]
            issues = [i for i in issues if i.get("action") == "regenerate"]
        else:
            cuts, kept, rejected = safe_cut_plan(issues, transcript["segments"], duration)
        if manual_cuts:
            cuts = validate_ranges([{"start": a, "end": b} for a, b in manual_cuts + [
                r for r in cuts if not any(r[0] < b and r[1] > a for a, b in manual_cuts)]],
                transcript["segments"], duration)
            kept, cursor = [], 0.
            for a, b in cuts:
                if a > cursor:
                    kept.append((cursor, a))
                cursor = b
            if cursor < duration:
                kept.append((cursor, duration))
        report.update(cuts=cuts, kept_ranges=kept, rejected_cuts=rejected, confirmed_issues=issues)
        logger.info("[OPTIMIZE] source review: available=%s issues=%s cuts=%s review_only=%s",
                    report["review_status"], len(issues), len(cuts), review_only)
        (directory/"optimization-checkpoint.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        if review_only:
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            return None, report
        notify("edit", "running")
        if settings.scene_order:
            boundaries = [r["start"] for r in timeline[1:]]
            if any(float(s["start"]) < boundary < float(s["end"]) for s in transcript["segments"] for boundary in boundaries):
                raise ValueError("Reordering would split an utterance across storyboard scenes")
        lighting = self.lighting_plan(source, timeline, project.script) if settings.match_lighting else []
        kept = reorder_ranges(kept, timeline, settings.scene_order or (
            [r["scene_number"] for r in timeline] if lighting else []))
        report["lighting_corrections"] = lighting
        report["hyperframes_lint"] = self.export_timeline(source, directory, kept, info, has_audio)
        edited = directory / "edited.mp4"
        edit_source = source
        if audio_repairs:
            notify("audio_edit", "running")
            edit_source = directory/"audio-repaired.mp4"
            self.repair_narration_audio(source, edit_source, audio_repairs, raw_segments, duration)
            transcript["segments"] = [s for s in transcript["segments"] if not any(
                s["start"] < r["end"] and s["end"] > r["start"] for r in audio_repairs)]
            notify("audio_edit", "completed")
        if cuts or settings.scene_order or lighting:
            self.edit(edit_source, edited, kept, has_audio, lighting=lighting)
        else:
            shutil.copyfile(edit_source, edited)
        notify("edit", "completed")
        mapped = map_segments(transcript["segments"], kept)
        final = directory / "optimized-subtitled.mp4"
        if final.exists():
            suffix = datetime.now().strftime("%Y%m%d%H%M%S")
            for previous in (final, directory/"subtitles.srt", directory/"optimization-report.json"):
                if previous.exists():
                    shutil.copyfile(previous, previous.with_name(f"{previous.stem}-{suffix}{previous.suffix}"))
        if settings.subtitles:
            notify("subtitles", "running")
            report["subtitle_render"] = self.render_subtitles(edited, final, mapped, info, directory)
            notify("subtitles", "completed")
        else:
            shutil.copyfile(edited, final)
            (directory/"subtitles.srt").write_text("")
        check_active()
        output_info = probe(final)
        if has_audio and not any(s["codec_type"] == "audio" for s in output_info["streams"]):
            raise RuntimeError("Final render lost the soundtrack")
        expected = sum(b-a for a,b in kept)
        if abs(float(output_info["format"]["duration"]) - expected) > .25:
            raise RuntimeError("Rendered timeline duration does not match edit plan")
        content_id = hashlib.sha256(final.read_bytes()).hexdigest()[:12]
        upload_started = time.monotonic()
        url = tos_service.upload_file(str(final), custom_filename=f"optimized-{content_id}.mp4",
                                      project_id=project.project_id, category="videos/final")
        stage_seconds["video_upload"] = round(time.monotonic() - upload_started, 3)
        check_active()
        notify("final_review", "running")
        try:
            final_context = {
                "cuts": cuts, "scene_order": settings.scene_order, "subtitles": settings.subtitles,
                "audio_repairs": audio_repairs}
            initial_final = self.review(url, script, mapped, references, final_context)
            if fast and initial_final.get("review_mode") != "fast_triage":
                initial_final = triage_report(initial_final)
            report["initial_final_review"] = initial_final
            if not fast and initial_final["issues"]:
                notify("final_confirm", "running")
                verification = independent_review_windows(initial_final["issues"])
                verification.update(final_context)
                verification["frame_evidence"] = self.window_evidence(
                    final, directory, verification["verification_windows"], check_active)
                report["final_verification_frame_evidence"] = verification["frame_evidence"]
                report["final_review"] = self.review(url, script, mapped, references, verification)
                notify("final_confirm", "completed")
            else:
                report["final_review"] = initial_final
        except Exception as error:
            report["final_review"] = {"approved": False, "unavailable": True, "error": type(error).__name__}
        notify("final_review", "completed" if report["final_review"].get("approved") else
               ("unavailable" if report["final_review"].get("unavailable") else "needs_review"))
        check_active()
        report.update(cuts=cuts, kept_ranges=kept, rejected_cuts=rejected,
                      removed_seconds=sum(b-a for a,b in cuts), output_duration=float(output_info["format"]["duration"]),
                      subtitle_count=sum("-->" in line for line in (directory/"subtitles.srt").read_text().splitlines()),
                      local_video=str(final), local_subtitles=str(directory/"subtitles.srt"))
        report["repair_history"] = project.optimization_history
        upload_started = time.monotonic()
        report["subtitle_url"] = tos_service.upload_file(str(directory/"subtitles.srt"),
            custom_filename="subtitles.srt", project_id=project.project_id, category="videos/final")
        stage_seconds["subtitle_upload"] = round(time.monotonic() - upload_started, 3)
        check_active()
        report["elapsed_before_report_upload_seconds"] = round(time.monotonic() - started, 3)
        public_report = {k: v for k, v in report.items() if not k.startswith("local_") and not k.endswith("_url")}
        upload_started = time.monotonic()
        report["report_url"] = tos_service.upload_bytes(json.dumps(public_report, ensure_ascii=False, indent=2).encode(),
            filename="optimization-report.json", content_type="application/json",
            project_id=project.project_id, category="videos/final")
        stage_seconds["report_upload"] = round(time.monotonic() - upload_started, 3)
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        logger.info("[OPTIMIZE] completed elapsed=%.3fs stages=%s",
                    report["elapsed_seconds"], stage_seconds)
        check_active()
        (directory / "optimization-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        return url, report


video_optimization_service = VideoOptimizationService()
