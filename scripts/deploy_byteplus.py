"""Publish committed v3 source and deploy an isolated veFaaS + APIG service.

Only environment keys referenced by config.yaml are copied; values never print.
Requires official bp CLI, Docker buildx, python-dotenv, and a configured bp profile.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="seedance-v3")
    parser.add_argument("--region", default="ap-southeast-1")
    parser.add_argument("--gateway-id", required=True)
    parser.add_argument("--registry", required=True, help="CR registry name")
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--function-name", default="seedance-drama-maker-v3")
    parser.add_argument("--service-id", help="Reuse a previously created gateway service")
    parser.add_argument("--builder", default="dmbuilder")
    parser.add_argument("--upload-mode", choices=("resumable", "buildx"), default="resumable")
    parser.add_argument("--state-file", type=Path, default=ROOT / ".run/deployment-v3.json")
    args = parser.parse_args()
    state_path = args.state_file
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    def save():
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        state_path.chmod(0o600)

    def api(service, action, body, route_api=False):
        cmd = ["bp", service, action, "--profile", args.profile, "--region", args.region,
               "--body", json.dumps(body)]
        if route_api:
            cmd += ["--force", "--version", "2022-11-12", "--endpoint",
                    "apig.ap-southeast-1.byteplusapi.com", "--method", "POST"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError(f"{action} failed; raw output withheld") from None
        error = data.get("ResponseMetadata", {}).get("Error")
        if result.returncode or error:
            raise RuntimeError(f"{action} failed: {(error or {}).get('Code', 'unknown')}")
        return data["Result"]

    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip():
        raise RuntimeError("Commit release files before building")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    remote = subprocess.check_output(["git", "ls-remote", "origin", "refs/heads/main"], cwd=ROOT).decode()
    if not remote.startswith(sha):
        raise RuntimeError("Push this commit to origin/main before building")
    subprocess.run([os.sys.executable, ROOT / "scripts/audit_secrets.py"], check=True, cwd=ROOT)
    env_path = ROOT / ".env"
    env = dotenv_values(env_path)
    if not env.get("CLOUD_ACCESS_PASSWORD"):
        raise RuntimeError("Set CLOUD_ACCESS_PASSWORD in local .env")
    if not env.get("CLOUD_ACCESS_SECRET"):
        set_key(str(env_path), "CLOUD_ACCESS_SECRET", secrets.token_urlsafe(48))
        env_path.chmod(0o600)
        env = dotenv_values(env_path)
    required = {"BYTEPLUS_AK", "BYTEPLUS_SK", "TOS_BUCKET", "MODELARK_API_KEY"}
    required |= {f"{model}_{field}" for model in ("MAIN_AGENT", "SCRIPT", "IMAGE", "VIDEO", "VIDEO_REVIEW")
                 for field in ("ENDPOINT", "API_KEY")}
    missing = required - {k for k, v in env.items() if v}
    if missing:
        raise RuntimeError("Missing configuration keys: " + ", ".join(sorted(missing)))
    allowed = set(re.findall(r"\$\{([A-Z_0-9]+)", (ROOT / "config.yaml").read_text()))
    cloud_env = {k: v for k, v in env.items() if k in allowed and v}
    cloud_env.update(ACCESS_PASSWORD=env["CLOUD_ACCESS_PASSWORD"],
                     ACCESS_SECRET=env["CLOUD_ACCESS_SECRET"], ACCESS_COOKIE_SECURE="true",
                     VIDEO_SKILLS_ROOT="/app/app/video_skills", VIDEO_TOOLS_RUNTIME_DIR="/opt/video-tools",
                     WHISPER_CACHE_DIR="/opt/whisper-models", VIDEO_OUTPUT_DIR="/tmp/seedance-outputs",
                     REMOTION_BROWSER_EXECUTABLE="/usr/bin/chromium", RUNTIME_SMOKE_ENABLED="true")
    registry_host = f"{args.registry}-{args.region}.cr.bytepluses.com"
    image = f"{registry_host}/{args.namespace}/seedance-drama-maker:git-{sha[:12]}"
    credentials = api("cr", "GetAuthorizationToken", {"Registry": args.registry})
    result = subprocess.run(["docker", "login", registry_host, "--username", credentials["Username"],
                             "--password-stdin"], input=credentials["Token"], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Registry login failed; raw output withheld")
    with tempfile.TemporaryDirectory(prefix="seedance-release-") as temporary, \
         tempfile.TemporaryDirectory(prefix="seedance-model-cache-") as model_context:
        Path(model_context).chmod(0o755)
        archive = subprocess.check_output(["git", "archive", "HEAD"], cwd=ROOT)
        subprocess.run(["tar", "-xf", "-", "-C", temporary], input=archive, check=True)
        local_models = Path(env.get("WHISPER_CACHE_DIR") or "~/.cache/whisper").expanduser()
        for name in ("small.pt", "large-v3-turbo.pt"):
            source = local_models / name
            if source.is_file() and not source.is_symlink():
                target = Path(model_context) / name
                try:
                    os.link(source, target)
                except OSError:
                    shutil.copy2(source, target)
        build = ["docker", "buildx", "build", "--builder", args.builder,
                        "--platform", "linux/amd64", "--tag", image, "--label",
                        f"org.opencontainers.image.revision={sha}", "--build-context",
                        f"whisper-cache={model_context}"]
        if args.upload_mode == "resumable":
            oci_archive = Path(temporary) / "image.oci.tar"
            build += ["--provenance=false", "--output",
                      f"type=oci,dest={oci_archive},oci-mediatypes=false", temporary]
            subprocess.run(build, check=True)
            subprocess.run([os.sys.executable, ROOT / "scripts/publish_oci.py",
                "--registry", args.registry, "--repository", f"{args.namespace}/seedance-drama-maker",
                "--tag", f"git-{sha[:12]}", "--archive", oci_archive,
                "--profile", args.profile, "--region", args.region,
                "--state-file", state_path.parent / "oci-upload-checkpoint.json"], check=True)
        else:
            subprocess.run(build + ["--push", temporary], check=True)
    print("Image published:", image, flush=True)
    state.update(commit=sha, image=image)
    save()
    functions = api("vefaas", "ListFunctions", {})
    items = functions.get("Functions", functions.get("Items", []))
    existing = next((f for f in items if f["Name"] == args.function_name), None)
    function_id = state.get("function_id") or (existing["Id"] if existing else None)
    parameters = dict(SourceType="image", Source=image, Command="./run.sh",
                      CpuMilli=4000, MemoryMB=8192, MaxConcurrency=10,
                      InitializerSec=120, RequestTimeout=900,
                      Envs=[{"Key": k, "Value": v} for k, v in sorted(cloud_env.items())],
                      Description=f"Seedance Drama Maker v3.0 GitHub {sha[:12]}")
    if function_id:
        api("vefaas", "UpdateFunction", dict(Id=function_id,
            SourceAccessConfig={"Username": credentials["Username"], "Password": credentials["Token"]},
            **{k: v for k, v in parameters.items() if k != "Command"}))
    else:
        created = api("vefaas", "CreateFunction",
                      dict(Name=args.function_name, Runtime="native/v1", ProjectName="default",
                           SourceAccessConfig={"Username": credentials["Username"], "Password": credentials["Token"]},
                           **parameters))
        function_id = created["Id"]
    state["function_id"] = function_id
    save()
    api("vefaas", "UpdateFunctionResource", dict(FunctionId=function_id, MinInstance=1, MaxInstance=5))
    for _ in range(120):
        status = api("vefaas", "GetImageSyncStatus", dict(FunctionId=function_id, Source=image))["Status"]
        print("Image synchronization:", status, flush=True)
        if status == "Success":
            break
        if status not in {"Running", "Pending"}:
            raise RuntimeError("Image synchronization failed")
        time.sleep(10)
    else:
        raise RuntimeError("Image synchronization timed out; resume deployment")
    api("vefaas", "Release", dict(FunctionId=function_id, RevisionNumber=0,
        TargetTrafficWeight=100, MaxInstance=5, Description=f"v3.0 {sha[:12]}"))
    service_id = args.service_id or state.get("service_id")
    if not service_id:
        service_id = api("apig", "CreateGatewayService", dict(
            GatewayId=args.gateway_id, ServiceName=args.function_name, Protocol=["HTTP", "HTTPS"],
            AuthSpec={"Enable": False}))["Id"]
    state["service_id"] = service_id
    save()
    upstream_id = state.get("upstream_id")
    if not upstream_id:
        upstream_id = api("apig", "CreateUpstream", dict(
            GatewayId=args.gateway_id, Name=args.function_name, SourceType="VeFaas",
            Protocol="HTTP", UpstreamSpec={"VeFaas": {"FunctionId": function_id}}))["Id"]
        state["upstream_id"] = upstream_id
        save()
    if not state.get("route_id"):
        route = api("apig", "CreateRoute", dict(
            GatewayId=args.gateway_id, ServiceId=service_id, Name="seedance-v3-main",
            Enable=True, Priority=0, Scene="Upstream",
            MatchRule={"Method": ["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"],
                       "Path": {"MatchType": "Prefix", "MatchContent": "/"}},
            UpstreamList=[{"Type": "VeFaas", "UpstreamId": upstream_id, "Weight": 100}],
            AdvancedSetting={"TimeoutSetting": {"Enable": True, "Timeout": 900000}}), route_api=True)
        state["route_id"] = route["Id"]
        save()
    state["url"] = f"https://{service_id}.apigateway-{args.region}.apigw-byteplus.com/"
    save()
    print("Deployed:", state["url"], flush=True)
    print("Function:", function_id, "instances: 1–5; cloud environment count:", len(cloud_env), flush=True)


if __name__ == "__main__":
    main()
