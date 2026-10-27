"""Inspect and smoke-test the shipped media tools without model API calls."""
import importlib.metadata
import json
from pathlib import Path
import shutil
import tempfile

from app import __version__
from app.config import config
from app.services.video_skill_service import STAGE_SKILLS, video_skill_service


def capabilities():
    runtime = Path(config.get("video_skills.runtime_dir")).expanduser()
    models = Path(config.get("video_optimization.whisper_cache_dir")).expanduser()
    packages = {}
    for name in ("openai-whisper", "torch", "librosa", "matplotlib", "pypinyin"):
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
    tools["chromium"] = bool(shutil.which("chromium") or shutil.which("google-chrome"))
    cached = {name: (models / file).is_file() for name, file in
              (("small", "small.pt"), ("turbo", "large-v3-turbo.pt"))}
    return {"version": __version__, "tools": tools, "python": packages,
            "whisper_models": cached, "stages": skills,
            "ready": all(tools.values()) and all(packages.values()) and all(cached.values())
                     and all(skills.values())}


def smoke():
    """Exercise decode, edit, both Whisper checkpoints, EDL lint, and caption rendering."""
    import numpy as np
    import whisper
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
        service.render_subtitles(edited, final,
            [{"start": .1, "end": .7, "text": "Runtime ready.", "words": [
                {"start": .1, "end": .4, "word": "Runtime"}, {"start": .4, "end": .7, "word": " ready."}]}],
            info, root)
        assert final.stat().st_size > 1000
        final_info = probe(final)
        assert any(s["codec_type"] == "audio" for s in final_info["streams"])
        assert abs(float(final_info["format"]["duration"]) - 1.5) < .15
        for name in ("small", "turbo"):
            model = whisper.load_model(name, device="cpu",
                download_root=str(Path(config.get("video_optimization.whisper_cache_dir")).expanduser()))
            result = model.transcribe(np.zeros(16000, dtype=np.float32), language="en",
                                      fp16=False, verbose=False)
            assert "segments" in result
            del model
        return {"passed": True, "edit_duration": float(info["format"]["duration"]),
                "caption_duration": float(final_info["format"]["duration"]), "timeline_lint": lint,
                "whisper": ["small", "turbo"]}


if __name__ == "__main__":
    import sys
    print(json.dumps(smoke() if "--smoke" in sys.argv else capabilities()))
