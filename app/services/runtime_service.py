"""Inspect and smoke-test the shipped media tools without model API calls."""
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import tempfile

from app import __version__
from app.config import config
from app.services.video_skill_service import STAGE_SKILLS, video_skill_service


def capabilities():
    runtime = Path(config.get("video_skills.runtime_dir")).expanduser()
    packages = {}
    for name in ("numpy", "librosa", "matplotlib", "pypinyin", "requests"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    skills = {}
    for stage in STAGE_SKILLS:
        try:
            skills[stage] = video_skill_service.manifest(stage)
        except RuntimeError:
            skills[stage] = []
    tools = {name: bool(shutil.which(name)) for name in ("ffmpeg", "ffprobe", "node")}
    tools["hyperframes"] = (runtime / "node_modules/hyperframes/dist/cli.js").is_file()
    tools["remotion"] = (runtime / "node_modules/@remotion/renderer/package.json").is_file()
    browser = os.getenv("REMOTION_BROWSER_EXECUTABLE", "")
    tools["chromium"] = bool(shutil.which("chromium") or shutil.which("google-chrome")
        or (browser and Path(browser).is_file())
        or Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").is_file())
    from app.services.asr_service import asr_service
    speech = {"provider": "byteplus", "resource_id": asr_service.resource_id,
              "configured": bool(asr_service.api_key and asr_service.appid),
              "connectivity_verified": False}
    return {"version": __version__, "tools": tools, "python": packages,
            "subtitle_renderer": config.get("video_optimization.subtitle_renderer", "ffmpeg"),
            "asr": speech, "stages": skills,
            "ready": all(tools.values()) and all(packages.values()) and speech["configured"]
                     and all(skills.values())}


def smoke():
    """Exercise local media tools; report ASR configuration without a paid API call."""
    import numpy as np
    from app.services.video_optimization_service import video_optimization_service, command, probe
    ready = capabilities()
    if not ready["ready"]:
        raise RuntimeError("Runtime is incomplete: " + json.dumps(ready))
    with tempfile.TemporaryDirectory(prefix="seedance-runtime-") as temporary:
        root = Path(temporary)
        source = root / "source.mp4"
        command(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25",
                 "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "2",
                 "-c:v", "libx264", "-c:a", "aac", "-y", source])
        edited = root / "edited.mp4"
        service = video_optimization_service
        service.edit(source, edited, [(0, .75), (1.25, 2)], True)
        info = probe(edited)
        assert abs(float(info["format"]["duration"]) - 1.5) < .15
        lint = service.export_timeline(source, root, [(0, .75), (1.25, 2)], probe(source), True)
        final = root / "captioned.mp4"
        subtitle_render = service.render_subtitles(edited, final,
            [{"start": .1, "end": .7, "text": "Runtime ready.", "words": [
                {"start": .1, "end": .4, "word": "Runtime"}, {"start": .4, "end": .7, "word": " ready."}]}],
            info, root)
        assert final.stat().st_size > 1000
        final_info = probe(final)
        assert any(s["codec_type"] == "audio" for s in final_info["streams"])
        assert abs(float(final_info["format"]["duration"]) - 1.5) < .15
        import subprocess
        frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.3", "-i", str(final),
            "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
            capture_output=True, check=True).stdout
        assert (np.frombuffer(frame, dtype=np.uint8) > 180).sum() > 5
        def audio(path):
            return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
                "-c", "copy", "-f", "adts", "-"], capture_output=True, check=True).stdout
        assert audio(edited) == audio(final)
        return {"passed": True, "edit_duration": float(info["format"]["duration"]),
                "caption_duration": float(final_info["format"]["duration"]), "timeline_lint": lint,
                "subtitle_render": subtitle_render, "audio_preserved": True, "subtitle_visible": True,
                "asr": ready["asr"]}


if __name__ == "__main__":
    import sys
    print(json.dumps(smoke() if "--smoke" in sys.argv else capabilities()))
