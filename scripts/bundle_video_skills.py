"""Copy only the references actually used by the application, with provenance."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]

def bundle(source: Path, destination: Path):
    # Read the declaration without importing service clients or local credentials.
    import ast
    module = ast.parse((ROOT / "app/services/video_skill_service.py").read_text())
    stages = next(ast.literal_eval(node.value) for node in module.body
                  if isinstance(node, ast.Assign) and any(
                      isinstance(t, ast.Name) and t.id == "STAGE_SKILLS" for t in node.targets))
    files = {(skill, reference) for references in stages.values() for skill, reference in references}
    files |= {(skill, "SKILL.md") for skill, _ in files}
    for skill, _ in tuple(files):
        files |= {(skill, p.name) for p in (source / skill).glob("LICENSE*") if p.is_file()}
    manifest = []
    for skill, relative in sorted(files):
        origin = source / skill / relative
        if not origin.is_file() or origin.is_symlink():
            raise RuntimeError(f"Missing regular source file: {skill}/{relative}")
        target = destination / skill / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
        manifest.append({"skill": skill, "file": relative,
                         "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Bundled {len(manifest)} approved files from {len({s for s, _ in files})} skills.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path.home() / ".trae-cn/skills")
    parser.add_argument("--destination", type=Path, default=ROOT / "app/video_skills")
    args = parser.parse_args()
    bundle(args.source, args.destination)
