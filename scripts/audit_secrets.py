"""Local audit: never print credential values or matching source lines."""
import json
import re
import subprocess
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
env = dotenv_values(ROOT / ".env")
secret_values = {
    value.encode()
    for key, value in env.items()
    if value and len(value) >= (4 if "PASSWORD" in key.upper() else 8)
    and re.search(r"key|secret|password|token|app_?id|bucket|^(MAIN_AGENT|SCRIPT|IMAGE|VIDEO|VIDEO_REVIEW)_ENDPOINT$", key, re.I)
}
patterns = {
    "private-key": rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "provider-token": rb"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{60,}|sk-[A-Za-z0-9_-]{32,})",
    "signed-url": rb"(?i)https?://[^\s\"<>]+[?&](?:X-Tos-Signature|X-Amz-Signature)=[a-f0-9]{32,}",
    "credential-literal": rb"""(?im)(?:api[_-]?key|secret[_-]?key|access[_-]?key|password|auth[_-]?token)\s*["']?\s*[:=]\s*["']([A-Za-z0-9_+/=-]{24,})["']""",
}

def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)

findings = []
def scan(data, location):
    if any(value in data for value in secret_values):
        findings.append((location, "local-credential"))
    for kind, expression in patterns.items():
        if re.search(expression, data):
            findings.append((location, kind))

files = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")
for raw in files:
    if raw:
        path = ROOT / raw.decode()
        if path.is_file():
            scan(path.read_bytes(), str(path.relative_to(ROOT)))
objects = git("rev-list", "--objects", "--all").decode().splitlines()
blob_count = 0
batch = subprocess.Popen(["git", "cat-file", "--batch"], cwd=ROOT,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE)
for line in objects:
    oid, _, name = line.partition(" ")
    batch.stdin.write((oid + "\n").encode())
    batch.stdin.flush()
    _, kind, size = batch.stdout.readline().split()
    data = batch.stdout.read(int(size))
    batch.stdout.read(1)
    if kind == b"blob":
        scan(data, "history:" + (name or oid))
        blob_count += 1
batch.stdin.close()
batch.wait()
for raw in git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split(b"\0"):
    if raw:
        name = raw.decode()
        scan(git("show", ":" + name), "staged:" + name)
print(json.dumps({"working_files": len(files)-1, "historical_blobs": blob_count,
                  "findings": sorted(set(findings))}, ensure_ascii=False))
raise SystemExit(bool(findings))
