"""Publish a linux/amd64 OCI archive with resumable, bounded Registry v2 requests."""
import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
import tarfile
import time
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
import requests

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--registry", required=True)
parser.add_argument("--repository", required=True, help="namespace/repository")
parser.add_argument("--tag", required=True)
parser.add_argument("--archive", type=Path, required=True)
parser.add_argument("--profile", default="seedance-v3")
parser.add_argument("--region", default="ap-southeast-1")
parser.add_argument("--chunk-mib", type=int, default=32)
parser.add_argument("--state-file", type=Path,
                    default=Path(__file__).resolve().parents[1] / ".run/oci-upload-checkpoint.json")
args = parser.parse_args()
if not 1 <= args.chunk_mib <= 64:
    parser.error("--chunk-mib must be between 1 and 64")
HOST = f"{args.registry}-{args.region}.cr.bytepluses.com"
BASE = "https://" + HOST
REPO, TAG = args.repository, args.tag
STATE = args.state_file
STATE.parent.mkdir(parents=True, exist_ok=True)
state = json.loads(STATE.read_text()) if STATE.exists() else {}
session = requests.Session()
session.headers["User-Agent"] = "seedance-v3-resumable-release"
stop_requested = False


def request_stop(signum, frame):
    global stop_requested
    stop_requested = True


signal.signal(signal.SIGTERM, request_stop)
signal.signal(signal.SIGINT, request_stop)


def save():
    STATE.write_text(json.dumps(state, indent=2))
    STATE.chmod(0o600)


def authenticate():
    result = subprocess.run(["bp", "cr", "GetAuthorizationToken", "--Registry",
        args.registry, "--profile", args.profile, "--region", args.region],
        capture_output=True, text=True, check=True)
    credentials = json.loads(result.stdout)["Result"]
    token = requests.get(BASE + "/service/token",
        params={"service": "harbor-registry", "scope": f"repository:{REPO}:pull,push"},
        auth=(credentials["Username"], credentials["Token"]), timeout=(15, 45))
    token.raise_for_status()
    data = token.json()
    session.headers["Authorization"] = "Bearer " + (data.get("token") or data["access_token"])


def request(method, url, **kwargs):
    for attempt in range(6):
        try:
            response = session.request(method, url, timeout=(30, 60), **kwargs)
            if response.status_code == 401:
                authenticate()
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                print("Registry temporary HTTP failure:", response.status_code, flush=True)
                time.sleep(min(10, attempt + 1))
                continue
            return response
        except requests.RequestException as error:
            # Requests exceptions contain upload signatures; never print their text.
            print("Registry transport retry:", type(error).__name__, flush=True)
            time.sleep(min(10, attempt + 1))
    raise RuntimeError("Registry request failed after bounded retries")


def location(response, previous):
    uri = urljoin(BASE, response.headers.get("Location") or previous)
    parts = urlsplit(uri)
    if parts.hostname != HOST:
        raise RuntimeError("Unexpected upload location host")
    return urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))


def require(response, allowed, operation):
    if response.status_code not in allowed:
        raise RuntimeError(f"{operation}: HTTP {response.status_code}")


authenticate()
with tarfile.open(args.archive, "r") as archive:
    index = json.load(archive.extractfile("index.json"))
    descriptor = next(d for d in index["manifests"] if d.get("platform", {}).get("architecture") == "amd64")
    def member(digest):
        return archive.getmember("blobs/" + digest.replace(":", "/"))
    manifest_bytes = archive.extractfile(member(descriptor["digest"])).read()
    manifest = json.loads(manifest_bytes)
    blobs = [manifest["config"]] + manifest["layers"]
    print("Publishing", len(blobs), "blobs; bytes:", sum(b["size"] for b in blobs), flush=True)
    for blob in blobs:
        digest, size = blob["digest"], blob["size"]
        exists = request("HEAD", f"{BASE}/v2/{REPO}/blobs/{digest}")
        if exists.status_code == 200:
            print("Already published:", digest[:19], size, flush=True)
            continue
        require(exists, (404,), "Blob lookup")
        checkpoint = state.get(digest)
        # Continue using the last acknowledged signed Location. A status GET
        # with an older offset can be rejected by Harbor as BLOB_UPLOAD_INVALID.
        if not checkpoint:
            created = request("POST", f"{BASE}/v2/{REPO}/blobs/uploads/")
            require(created, (202,), "Start upload")
            checkpoint = {"uri": location(created, ""), "offset": 0}
            state[digest] = checkpoint
            save()
        print("Upload:", digest[:19], "offset:", checkpoint["offset"], "size:", size, flush=True)
        stream = archive.extractfile(member(digest))
        started, initial = time.monotonic(), checkpoint["offset"]
        last_report = checkpoint["offset"]
        chunk_size = args.chunk_mib * 1024 * 1024
        while checkpoint["offset"] < size:
            offset = checkpoint["offset"]
            stream.seek(offset)
            data = stream.read(min(chunk_size, size - offset))
            response = request("PATCH", checkpoint["uri"], data=data, headers={
                "Content-Type": "application/octet-stream",
                "Content-Range": f"{offset}-{offset + len(data) - 1}"})
            if response.status_code == 413 and chunk_size > 1024 * 1024:
                chunk_size //= 2
                print("Reduced upload chunk size:", chunk_size, flush=True)
                continue
            if response.status_code in (404, 410):
                created = request("POST", f"{BASE}/v2/{REPO}/blobs/uploads/")
                require(created, (202,), "Restart expired upload")
                checkpoint = {"uri": location(created, ""), "offset": 0}
                state[digest] = checkpoint
                save()
                started, initial, last_report = time.monotonic(), 0, 0
                print("Upload checkpoint expired; restarting current blob:", digest[:19], flush=True)
                continue
            if response.status_code == 416:
                status = request("GET", checkpoint["uri"])
                require(status, (204,), "Resume upload")
                checkpoint["uri"] = location(status, checkpoint["uri"])
                checkpoint["offset"] = int(status.headers["Range"].split("-")[-1]) + 1
                save()
                continue
            require(response, (202,), "Upload chunk")
            checkpoint["uri"] = location(response, checkpoint["uri"])
            checkpoint["offset"] = int(response.headers["Range"].split("-")[-1]) + 1
            save()
            if stop_requested:
                print("Checkpoint saved; upload paused:", digest[:19], checkpoint["offset"], flush=True)
                raise SystemExit(75)
            if checkpoint["offset"] - last_report >= 32 * 1024 * 1024 or checkpoint["offset"] == size:
                elapsed = max(.01, time.monotonic() - started)
                print("Progress:", digest[:19], f'{checkpoint["offset"]}/{size}',
                      f'{(checkpoint["offset"] - initial)/elapsed/1024/1024:.2f} MiB/s', flush=True)
                last_report = checkpoint["offset"]
        uri = urlsplit(checkpoint["uri"])
        query = dict(parse_qsl(uri.query))
        query["digest"] = digest
        completed = request("PUT", urlunsplit((uri.scheme, uri.netloc, uri.path, urlencode(query), "")),
                            data=b"", headers={"Content-Type": "application/octet-stream"})
        require(completed, (201,), "Commit blob")
        print("Published blob:", digest[:19], flush=True)
        state.pop(digest, None)
        save()
    response = request("PUT", f"{BASE}/v2/{REPO}/manifests/{TAG}", data=manifest_bytes,
                       headers={"Content-Type": manifest["mediaType"]})
    require(response, (201,), "Publish image manifest")
    print("PUBLISHED:", f"{HOST}/{REPO}:{TAG}", response.headers.get("Docker-Content-Digest"), flush=True)
