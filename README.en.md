# Seedance Drama Maker · AI Short-Drama Engine

English documentation. Simplified Chinese version: `README.md`

- 🌐 Live demo: <https://sd6npkalgt0sthatn3doa.apigateway-ap-southeast-1.apigw-byteplus.com/>
- Current version: `3.0.0`
- Stack: `FastAPI`, `WebSocket`, multi-agent orchestration, `FFmpeg`
- BytePlus products: `TOS`, `Seed-Speech`, `DeepSeek-V4.1-Flash`, `Seed-2.1-turbo`, `SeeDream-5.0-flash`, `SeeDance-2.5`
- AI development tools: `Trae.ai`, `DeepSeek-V4.1-Flash`, `GPT-5.5`

## ✨ Seedance 2.5 Highlights

Powered by the **SeeDance 2.5** video model — turn one sentence into a cinematic AI short drama:

- 🎬 **Up to 30-second shots**: each scene can run up to 30 seconds (5–30s adjustable) for more continuous long-take storytelling, with a final cut up to `3600` seconds.
- 🖼️ **Up to 50 reference inputs**: a single task can fuse up to 50 character / backdrop reference images for highly consistent characters and scenes across shots.
- 🌍 **14 languages natively supported**: native multilingual dialogue and narration across Chinese, English, Japanese, Spanish, and 14 languages total.
- 🎞️ **Cinematic audiovisual quality**: native audio-video sync, adaptive aspect ratio, and the final cut is remuxed to high-fidelity `MP4` (H.264/AAC, `+faststart`) that streams progressively in the browser.
- 🎭 **Character-outfit / backdrop-state variants**: per scene the system derives character-outfit images and backdrop time/weather-state images, and references them consistently in scene videos.
- 🤖 **Fully automated multi-agent pipeline**: script → reference library → character-outfit/backdrop-state variants → scene generation & review → long-video merge, end to end.

## Overview

A multi-agent AI video system that turns text, images or voice input into short dramas, comic-style videos and ads. It supports automatic and manual workflows, locally and in the cloud.

Workflow: input → script → character and backdrop references → scene generation and review → merge → optimization and subtitles.

video-use and vlog-auto-edit guide editing; media-use handles assets; HyperFrames builds the timeline; BytePlus Seed Speech transcribes audio; remotion-captions groups subtitles, FFmpeg adds them quickly, and remotion-render supports frame rendering. The system supports faulty-fragment cuts, local reshoots, lighting correction and final review, preserving original audio and exporting videos, subtitles and review reports.

### Update on 2026-10-28

- BytePlus ASR now enables automatic language detection (`enable_auto_lang=true`) for Japanese, Korean, Spanish, Portuguese and mixed Chinese/English audio. Subtitles use actual speech, including fillers and word/utterance times. Old recognition caches expire; correcting a language label alone does not repeat ASR.
- Local repairs edit the original scene video with Seedance 2.5. Ranges in one scene share one request; different scenes run in parallel. Only independently approved ranges replace the picture, preserving other frames and original audio. Times refer to the uncut master, with up to 6 seconds per range.
- Reaching the repair limit shows a report for review instead of waiting indefinitely. Pending scene generation or review blocks merge and optimization. Ending a project stops further work and deletes all its resources; download the video, subtitles and report first.
- Removed local Whisper/PyTorch model layers, the old image-based repair path and obsolete tests. Updated all five UI languages and static cache versions.

## Showcase

### UI And Generation Flow

**UI overview and script generation**: from a single-sentence prompt the system auto-generates the title, style, character definitions, backdrop definitions, and scene scripts.

<img src="./demo/gen-story.png" alt="UI overview and script generation" width="100%" />

**Scene videos and merge**: each scene is generated, AI-reviewed, and merged into the final long video.

<img src="./demo/merge-video.png" alt="Scene videos and merge" width="100%" />

**Multilingual support**: the UI supports `zh-CN` / `zh-TW` / `en` / `ja` / `es`, and video dialogue is natively supported in 14 languages.

<img src="./demo/i18n-ui.png" alt="Multilingual support" width="100%" />

### Video Demos

- Fully automated agent demo: [demo/how-to-use-en.mp4](./demo/how-to-use-en.mp4)
- 2-minute live-action drama: [demo/real-drama.mp4](./demo/real-drama.mp4)
- 2-minute comic-style drama: [demo/comics-drama.mp4](./demo/comics-drama.mp4)

## BytePlus Products Used

- `TOS`
  Stores and serves uploaded assets, reference images, scene videos, and final videos.
- `Seed-Speech`
  Converts voice input into text for the creation workflow.
- `DeepSeek-V4.1-Flash`
  Powers the main conversational agent and scene-script generation. Video review has been tested and switched to `deepseek-v4.1-flash-1017` for scene and full-film visual checks.
- `Seed-2.1-turbo`
  The previous review model, configurable through `VIDEO_REVIEW_ENDPOINT` / `VIDEO_REVIEW_API_KEY` in `.env`.
- `SeeDream-5.0-flash`
  Generates and regenerates character and backdrop reference images.
- `SeeDance-2.5`
  Generates scene videos with 5–30s per shot, up to 50 reference inputs, and native dialogue in 14 languages with native audio-video sync.

All of the above are available through [byteplus.com](https://www.byteplus.com/).

## AI Development Tools

- `Trae.ai`: coding, debugging, repository cleanup
- `DeepSeek-V4.1-Flash`: requirement clarification, solution discussion, documentation support
- `GPT-5.5`: code editing, debugging, logic cleanup, documentation rewriting

## Prerequisites

### Environment

- Python `3.11`, Node `22+`, Chromium, and BytePlus Seed Speech for transcription
- `ffmpeg` and `ffprobe` available in the runtime environment, including local runs, Docker, and cloud veFaaS images
- Network access to BytePlus / ModelArk / Seed-Speech / TOS

### Required Service Enablement

Before running the system, prepare the following:

- Enable the required `ModelArk` models and get the corresponding `apikey`
- Enable `Seed-Speech` and get `appid` and `apikey`
- Enable `TOS` and get valid `AK/SK`
- Create your own private `bucket` in `TOS`
- Configure non-sensitive fields such as `endpoint` in `config.yaml`; put real `apikey` / `AK` / `SK` / `appid` / `bucket` values in the project-root `.env`

### Credential Governance Rules

- All model `apikey` values, object storage `AK/SK`, speech credentials, `endpoint` / `bucket`, and other sensitive data must be configured only in `.env`.
- `config.yaml` keeps only `${VAR}` placeholders; at runtime `app/config.py` injects values from `.env`. No real secrets may be hard-coded in any source file.
- `.env` is git-ignored and must never be committed. Copy `.env.example` as a template and fill in real values.
- If different models use different `apikey` values, they must still live in dedicated variables inside `.env`.

### Minimal Configuration Example

Put sensitive credentials into the project-root `.env` (copy from `.env.example`):

```bash
# BytePlus TOS credentials
BYTEPLUS_AK=your-byteplus-ak
BYTEPLUS_SK=your-byteplus-sk
TOS_REGION=ap-southeast-1
TOS_BUCKET=your-private-bucket
TOS_ENDPOINT=https://tos-ap-southeast-1.bytepluses.com
TOS_PRIVATE_BUCKET=true
TOS_PRESIGNED_URL_EXPIRES=604800
TOS_PRESIGNED_URL_REFRESH_MARGIN=3600

# ModelArk
MODELARK_API_KEY=your-modelark-api-key
MODELARK_BASE_URL=https://ark.ap-southeast.bytepluses.com/api/v3

# Seed-Speech ASR
SPEECH_APPID=your-seed-speech-appid
SPEECH_API_KEY=your-seed-speech-api-key

# Model endpoints / API keys
MAIN_AGENT_ENDPOINT=your-main-agent-endpoint
MAIN_AGENT_API_KEY=your-main-agent-api-key
SCRIPT_ENDPOINT=your-script-endpoint
SCRIPT_API_KEY=your-script-api-key
IMAGE_ENDPOINT=your-image-endpoint
IMAGE_API_KEY=your-image-api-key
VIDEO_ENDPOINT=your-video-endpoint
VIDEO_API_KEY=your-video-api-key
VIDEO_REVIEW_ENDPOINT=your-video-review-endpoint
VIDEO_REVIEW_API_KEY=your-video-review-api-key

# Virtual asset library
ASSET_LIBRARY_REGION=ap-southeast-1
ASSET_LIBRARY_API_HOST=ark.ap-southeast-1.byteplusapi.com
ASSET_LIBRARY_PROJECT_NAME=default

```

Keep placeholders in `config.yaml`; values are injected from `.env` at runtime:

```yaml
byteplus:
  ak: "${BYTEPLUS_AK}"
  sk: "${BYTEPLUS_SK}"

modelark:
  api_key: "${MODELARK_API_KEY}"
  base_url: "${MODELARK_BASE_URL:-https://ark.ap-southeast.bytepluses.com/api/v3}"

server:
  port: 8888

models:
  main_agent:
    endpoint: "${MAIN_AGENT_ENDPOINT}"
    api_key: "${MAIN_AGENT_API_KEY}"
  # Other model sections follow the same ${VAR} placeholder pattern
```

## Features And Workflow

### 1. Input Collection

Supported inputs:

- Text prompts
- Uploaded reference images
- Voice recording converted with `Seed-Speech`

The speech-to-text path preserves the browser's actual recording format. Chrome and cloud browsers commonly produce `webm/opus`, which is uploaded as `.webm` and submitted to `Seed-Speech` with `format=webm` and `codec=opus`; local or other browser outputs such as `wav/mp3/m4a/ogg` are adapted by extension. ASR polling is a blocking network operation, so the backend runs it in the dedicated interactive thread pool rather than the generation pool.

### 2. Script Generation

`ScriptAgent` generates:

- Title, style, and background
- Character definitions
- Backdrop definitions
- Scene scripts

Current rules:

- Total video duration limit: `3600` seconds
- Per-scene duration range: `5`–`30` seconds (tuned for `SeeDance-2.5`)
- Scene duration is allocated dynamically from action beats, cast size, information density, spatial blocking, and emotional turns; simple shots stay short, complex scenes receive more time, with at least a `2`-second spread and multiple duration tiers whenever the total allows it
- Each `description` uses continuous executable time segments: at least 2 segments for `5`–`9` seconds, 3 for `10`–`18` seconds, and 4 for `19`–`30` seconds, covering environment, blocking, actions, subtle performance, camera work, lighting, and narrative results
- Scene limit: `200`
- Character definition limit: `30`
- Backdrop definition limit: `30`
- Adjacent scenes use motivated action/eyeline/prop/composition matches, sound bridges, natural occlusion, or lighting echoes; continuous scenes extend the performance directly, avoiding template transitions and forced suspense on every shot
- Narrative hooks must grow from established character choices, consequences, partial reveals, sounds, props, or emotional changes and be paid off by later scenes; video generation must not add unmotivated black screens, white flashes, spins, particles, glitches, repeated whip pans, or frequent fades
- If script quality validation fails, the script model revises the previous complete JSON using the concrete validation findings, preserving unaffected content instead of generating again from the original request
- Generation, rewriting, and targeted fixes share one review flow for locked settings, structure, duration, and story continuity. Semantic findings are checked against script evidence to avoid false failures for inherited camera positions, lighting, or prop inserts
- `script_generation.validation_max_rounds` defaults to `3`, including the first draft and later fixes. Valid director timing plans are kept before any duration adjustment
- When all rounds fail, manual mode pauses and offers a full project restart or continuation with the best draft; the choice survives a page refresh. Auto mode continues with the best candidate, ranked by locked settings, executable structure, duration, and confirmed issue count
- Per-scene special outfits and hairstyle changes are written explicitly to `character_outfits`, while backdrop time/weather state is written explicitly to `scene_state`; both fields appear before the scene description and persist into downstream image/video generation
- Backend logs record model, message count, request length, timing and error summaries without full prompts, request bodies or raw model output. Heartbeats and asset polling use compact `DEBUG` metadata.

### 3. Reference Library Generation

`ImageAgent` builds a unified reference library with:

- Character reference images
- Backdrop reference images

Current upload rules:

- Up to `30` uploaded reference images per request
- Up to `10` character images
- Up to `20` backdrop images
- Every uploaded image must be named

Two post-upload behaviors are supported:

- `Use Original Image` enabled
  The system does not run model-based processing on the uploaded image or its name and places it directly into the reference library by type.
- `Use Original Image` disabled
  The system regenerates the unified reference library from uploaded images plus script character/backdrop definitions.

The workflow does not enter the next countdown or continue step until all reference generation and regeneration jobs are fully completed.

### 3.1 Private Virtual Portrait Library

The system now integrates a private virtual portrait library:

- Every project creates its own dedicated asset group with a one-to-one project mapping
- All character / portrait images in the project, including uploaded originals and generated references, are registered as assets and store their `asset_id`
- Assets enter the generation chain only after polling `GetAsset.Status = Active`

Authentication notes:

- Video inference still uses the `ModelArk` Bearer API key
- Asset-library APIs use `BytePlus AK/SK` signed requests
- Runtime configuration therefore requires both `MODELARK_API_KEY` and `BYTEPLUS_AK/BYTEPLUS_SK`
- The BytePlus account must also have the virtual asset-library subscription enabled; otherwise `CreateAssetGroup` returns `SubscriptionRequired`

### 3.2 Character-Outfit And Backdrop-State Images

After all character main images and scene main images are generated, the system derives variant images from each scene:

- Variants are generated only when `character_outfits` contains an outfit different from the default `clothing` / `hairstyle`, or when `scene_state` / `time_of_day` / `weather` differs from the backdrop default
- Character-outfit image = the scene's outfit description + the corresponding character main image
- Backdrop-state image = the scene's time/weather state + the corresponding scene main image
- Variants across scenes are generated in parallel, bounded by the image concurrency setting (`video_generation.reference_images.max_concurrency`)
- Character outfits are deduplicated by stable visual traits such as clothing, hairstyle, and injuries; backdrop states are normalized by time and weather so minor wording differences do not trigger duplicate generation
- Each variant is bound to one or more scenes through `scene_numbers`, and scene videos prefer variants explicitly matched to the current scene number
- Scene-video reference selection resolves outfit images directly by `scene_numbers`, exact `variant_key`, then visual equivalence, without rebuilding and rescanning the complete scene-variant plan for every character

### 3.3 Scene Video Generation

`VideoAgent` generates each scene video with:

- Character image (preferring the character-outfit image when the scene has one)
- Scene image (preferring the backdrop-state image when the scene has one)
- The current scene script and user style requirements

Character references now use `asset://asset-id` URIs in video generation requests whenever an asset is available, for example:

```json
{
  "type": "image_url",
  "role": "reference_image",
  "image_url": {
    "url": "asset://asset-xxxxxxxx"
  }
}
```

Scene videos only use character and backdrop images matched to the current scene. Automatic review generates scenes concurrently according to the configured limit, while manual review generates one scene at a time and waits for confirmation.

Key rules:

- Automatic failure retry counts are controlled by YAML (`video_review.max_retries`, `video_generation.scene_total_generate_limit`)
- Manual `Regenerate` clicks do not consume the automatic failure budget
- The video prompt appends a no-background-music constraint by default unless the user explicitly requests a music style
- Scene numbers are normalized to `1..N` from storyboard array order, and every generated result stores a scene-content fingerprint to prevent a correctly numbered video from being attached to the wrong script content
- Archived scene-video names use a sortable format such as `scene_012_attempt_03.mov`

### 3.4 Prompt And Skill Management

- Reusable LLM prompts, image/video generation constraints, review rubrics, and script-generation rules live in `app/prompt_skill/*.md`
- Agents load Markdown templates through `app.prompt_skill.load_prompt()` or `app.prompt_skill.render_prompt()`; stable prompt text should be added as `.md` files instead of being hard-coded in Agent classes
- Markdown templates use `$variable` placeholders so JSON examples can keep literal `{}` braces without escaping
- Runtime context such as user input, ASR text, reference image URLs, scene fields, character names, and backdrop names is still assembled in code

### 3.5 Project Ending And Cleanup

The Web UI now includes an `End Project` button. Cleanup can be triggered in three ways:

- the user clicks `End Project`
- the user closes the current project page
- the user closes the browser and the frontend reports cleanup with `sendBeacon` / `keepalive fetch`

Cleanup removes:

- all project files in `TOS`, including the master, final film, subtitles, report and recovery snapshot
- virtual asset-library assets for the project
- the asset group associated with the project

Download the files you need before ending the project. Automatic cleanup after approved completion removes intermediate assets while keeping delivery files and recovery state. Explicit project end deletes all resources. WebSocket disconnection or project failure alone does not delete resources, allowing recovery or retry.

### 4. Video Review And Flow Control

`VideoReviewAgent` uses the visual model configured in `.env`, currently `deepseek-v4.1-flash-1017`. On one shared test film, fast review took 16.20 seconds with the previous model and 5.11 seconds with the candidate; both returned structured reports. This single-film timing does not establish general accuracy.

Review dimensions:

- Character consistency
- Physical world rules
- Semantic consistency with the scene script

For character-consistency review, each character uses the outfit image bound to the current scene when available, falling back to the character main image only when no matching outfit image exists.

Current default review configuration:

- `video_review.pass_threshold = 80`
- `video_review.max_retries = 2`
- `video_review.default_mode = auto`

Flow rules:

- Auto mode: failed reviews regenerate automatically; after the retry limit, the highest-scoring candidate is selected to continue the workflow
- Manual mode: review still runs, but regeneration is not automatic; the user may continue manually
- Review network or transport failures are not treated as video-quality failures: the current video is retained instead of regenerated, and HTTP `400` parameter errors are not retried
- The workflow never moves forward while there are unfinished automatic or manual regeneration jobs
- Cloud long-running tasks route messages through the stable `client_id` to the latest WebSocket connection, so generation and review results still reach the active frontend after gateway or browser reconnects
- In auto mode, every scene is persisted as soon as it finishes. Failed scenes are removed in descending order only after all parallel jobs settle, then all remaining artifacts are renumbered together to prevent script/video mismatches
- When auto mode reaches the retry cap and selects the highest-scoring candidate, the scene is marked as accepted-over-retry and does not block the final merge
- After a project ends, queued parallel scenes stop quietly. In-flight remote generation results recheck the termination state before archive, review, or retry and do not continue unnecessary processing

### 5. Merge

`MergeAgent` and `FFmpegService` handle the final merge.

Current default merge settings:

- `SeeDance-2.5` generation tasks and reference videos all use `MOV`; `FFmpeg` concatenates the segments into a `MOV`
- Before concatenation each scene segment is normalized to uniform parameters (H.264/`yuv420p` + AAC `48kHz` stereo + CFR) to avoid mismatched audio sample rates/codecs causing silent second halves or broken web playback
- After merging, the output is remuxed to `MP4` (stream-copy `-c copy` first, falling back to `H.264/AAC` re-encode) with `-movflags +faststart` moving the `moov atom` to the front for progressive browser playback
- The final uploaded and browser-played cut is `MP4`
- Before merging, the system rejects duplicate, missing, or extra scene numbers and verifies each video's scene-content fingerprint; segments are always merged in physical storyboard-array order
- `merge.temporary_edge_trim = on`
- `trim_previous_end_frames = 2`
- `trim_next_start_frames = 0`

After all videos and reviews finish:

- Auto mode: a countdown is shown first, then merge starts automatically
- Manual mode: a countdown hint is shown first, then the system waits for a chat command before merging

## Prompt Examples

### Auto Mode

`以女生heibi为主角，香港中环写字楼场景，生成美漫风格，都市怪谈悬疑恐怖故事。比例9:16，时长30秒。auto`

### Manual Mode

`以大学校园为场景，Q版动漫风格，学生的励志故事。比例4:3，时长90秒`

### Supported Uploaded Assets

- `Reference images`
- `Voice audio`
- Mixed uploads for characters and backdrops

## System Architecture

```text
Browser UI
  |- static/index.html
  |- static/css/style.css
  |- static/js/app.js
  |- static/i18n/*.json

FastAPI App
  |- app/main.py
  |- REST API
  |- WebSocket manager

MainAgent
  |- ScriptAgent
  |- ImageAgent
  |- VideoAgent
  |- VideoReviewAgent
  |- MergeAgent

Services
  |- LLMService
  |- ASRService
  |- TOSService
  |- FFmpegService
```

### Key Design Points

- All sensitive credentials live in `.env`; `config.yaml` keeps only `${VAR}` placeholders injected by `app/config.py` at runtime
- TOS objects retain private ACLs. Browsers, models, review, and ASR receive automatically refreshed temporary GET URLs, while backend copy, merge, and state persistence use the TOS SDK directly
- The UI supports `zh-CN`, `zh-TW`, `en`, `ja`, and `es`; `SeeDance-2.5` video dialogue is natively supported in 14 languages
- The pipeline generates in `MOV` and remuxes the final cut to `MP4` (`+faststart`), with `TOS` normalizing `Content-Type` for reliable web playback
- Each project is bound to its browser connection to support isolated multi-tab execution
- Long-running background tasks can continue pushing results after WebSocket reconnects through a stable client ID
- Reference generation, video generation, review, and merge are rendered progressively in the UI
- Approved completion keeps the master, final film, subtitles, report and recovery state; explicit project end deletes all resources
- Uploads, ASR, and other real-time interactions use the dedicated `interactive` thread pool; image generation, video generation, review, and merge use the `generation` pool, reducing queue timeout risk on cloud single-instance multi-project workloads

## Usage

### Install Dependencies

```bash
uv venv --python 3.11
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-video-tools.txt
npm ci --prefix runtime
mkdir -p ~/.local/share/seedance-drama-maker/video-tools
cp -R runtime/node_modules ~/.local/share/seedance-drama-maker/video-tools/
```

Install FFmpeg/FFprobe and Chromium on the host. Configure `SPEECH_*`, TOS and `ASR_RESOURCE_ID=volc.seedasr.auc`. By default, leave language unset and enable `enable_auto_lang` for Japanese, Korean, Spanish, Portuguese and mixed Chinese/English/Cantonese speech. Keep fillers and native word/utterance times. ASRService also accepts an explicit `language`; subtitle optimization always uses automatic detection. Recognition failures stop subtitle generation with an error; a real no-speech result can export without captions. No local speech model is needed. Remotion can download its browser on macOS; on Linux set `REMOTION_BROWSER_EXECUTABLE=/usr/bin/chromium`.

### Start The Service

```bash
python3 -m app.main
```

Default URL:

```text
http://localhost:8888
```

### Common Commands

- `开始生成` / `开始` / `生成`
- `继续` / `下一步` / `确认`
- `重新生成` / `重做` / `regen`
- `退回-剧本` / `退回-参考图` / `退回-视频`
- `一键生成` / `autorun` / `auto`

## Latest End-To-End Verification

The latest real local integration run has already verified the full workflow:

- upload a portrait to `TOS`
- upload audio and run `Seed-Speech` `ASR`
- verify browser-recorded `webm/opus` and local `wav` recordings are both submitted correctly to `Seed-Speech`
- create a project-scoped virtual asset group and register the portrait asset
- generate the script with the real script model endpoint
- inject character references into video generation with `asset://asset-id`
- complete multi-scene generation, AI review, and final merge
- trigger `End Project` cleanup to remove temporary `TOS` files, assets, and the asset group

Notes:

- The latest real E2E run successfully produced a final video under `videos/final`
- Scene review, final merge, and project-ending cleanup were all validated in the real cloud workflow
- Approved completion removes intermediate assets and keeps delivery files and recovery state; explicit project end stops processing and deletes all TOS resources, while failed or disconnected projects remain available for retry

## Repository Hygiene And Safe Commits

To prevent secret leakage or accidental publication of test artifacts, this repository follows these rules:

- Real `apikey`, `AK/SK`, `appid`, and private bucket names must stay only in the local `.env`
- `.env`, `/.run/`, `*.log`, cache directories, `/plan.md`, and `/docs/` must not enter Git
- README files, `config.yaml`, and `.env.example` keep placeholders only and never contain real credentials
- Before committing, make sure test audio, test images, service logs, and temporary exports are not staged
- Temporary share packages, archive exports, key files, and local deployment JSON stay outside Git and the image context. Before release, scan staged files and history; report file paths only, without printing secret values
- Remove local monitoring records, debug logs, old test exports and obsolete repair code during release cleanup. Keep valid behavior regressions local. Environments, credentials and deployment recovery state are not temporary files.

### Cloud Release

Release in this order: `git commit → git push → rebuild image → release veFaaS`. `Dockerfile` includes Python 3.11, Node 22, FFmpeg/FFprobe, Chromium, Noto fonts, locked HyperFrames/Remotion packages, prompts and bounded skill references. Audio transcription calls BytePlus Seed Speech without a local model layer. Start with `./run.sh`; one Uvicorn process listens on `_FAAS_RUNTIME_PORT` (default 8000). Background task snapshots are saved to private TOS.

The veFaaS service in BytePlus `ap-southeast-1` keeps at least `1` and at most `5` instances. Once the new revision is ready, move all traffic to it and retain the previous revision for rollback. Check the cloud page, locale files, private TOS signed access, and release status.

Install the official [BytePlus CLI](https://github.com/byteplus-sdk/byteplus-cli), then run `bp configure --profile seedance-v3` and enter AK/SK and region privately. Put service settings and `CLOUD_ACCESS_PASSWORD` in local `.env`. The deployment script maps it to cloud `ACCESS_PASSWORD`, creates a shared `ACCESS_SECRET`, and enables Secure cookies. Leave local `ACCESS_PASSWORD` empty. Secrets enter function environment settings only, never Git, build arguments or image layers.

```bash
.venv/bin/python scripts/audit_secrets.py
.venv/bin/python scripts/deploy_byteplus.py \
  --profile seedance-v3 --region ap-southeast-1 \
  --gateway-id YOUR_GATEWAY_ID --registry YOUR_CR_REGISTRY \
  --namespace YOUR_CR_NAMESPACE --builder dmbuilder
```

The script builds `linux/amd64` from `git archive HEAD`, creates an isolated v3 function, APIG service, VeFaaS upstream and `/` prefix route, and sets 4 vCPU / 8 GiB, a 900-second request timeout, and 1–5 instances. The route API uses version `2022-11-12`. Local state in ignored `.run/deployment-v3.json` allows resuming deployment.

Use an ordinary `native/v1` function without `CpuStrategy=always`, which selects the Sandbox function type. Image transfer can finish while `ImageCacheStatus` is still `Creating`; wait for the cache and overall synchronization to succeed. Release the function and wait for `done` before setting and reading back `MinInstance=1`, `MaxInstance=5` and zero frozen reserved instances. The deployment helper prints both synchronization and cache status.

For app-only changes, `--reuse-runtime-layers` checks that the published commit has the same Dockerfile, Python dependencies and Node runtime, then validates image settings, build instructions and every runtime layer hash. It reuses runtime layers already in the registry and uploads only fresh app, static and settings layers. App layers are built from current source, so deleted files do not remain. Changed runtime inputs require a full build. This mode requires resumable upload, publishes the current commit and cannot be combined with `--reuse-published-image`.

Cloud settings include `BYTEPLUS_AK/SK`, `TOS_*`, `MODELARK_*`, five model `*_ENDPOINT` / `*_API_KEY` pairs, `SPEECH_*`, `ASR_RESOURCE_ID`, `ASSET_LIBRARY_*`, and `ACCESS_PASSWORD` / `ACCESS_SECRET` / `ACCESS_COOKIE_SECURE`. Runtime paths are `VIDEO_SKILLS_ROOT=/app/app/video_skills`, `VIDEO_TOOLS_RUNTIME_DIR=/opt/video-tools`, `VIDEO_OUTPUT_DIR=/tmp/seedance-outputs`, and `REMOTION_BROWSER_EXECUTABLE=/usr/bin/chromium`. Only variables referenced by configuration are copied.

`/healthz` returns health and version only. Authenticated `/api/runtime` reports tools, skills and ASR settings without secrets; configured ASR does not prove connectivity. Run `python -m app.services.runtime_service --smoke` inside the image to check actual editing, captions, timeline lint, audio and duration without paid ASR calls. WebSocket delivery is local to its instance; snapshot polling covers updates made by other instances. Restarting interrupted jobs after instance removal and arbitrating concurrent writes still need a job queue and shared locks; TOS snapshots alone do not provide them.

The deployment script enables `RUNTIME_SMOKE_ENABLED=true`. After login, `POST /api/runtime/smoke` runs the same synthetic media check on the actual cloud instance, cleans its temporary files and makes no paid generation calls. Each instance allows one check at a time. Set the flag to `false` to disable this diagnostic endpoint.

Historical cloud check (2026-10-27, before the ASR migration): media checks took 35.89 seconds and produced a 1.52-second video with visible subtitles, preserved audio and valid timing. The local speech models passed then; they have since been removed. Current runtime checks cover media tools and ASR configuration without a paid ASR call. All five UI languages and authenticated WebSocket connections passed. The application rejects unauthenticated handshakes; a proxy may change the response code, but the client cannot upgrade the connection.

A cloud Chrome run before this speed update generated a 60-second English romantic adventure sample. Its three scenes passed their first reviews with scores of 83/86/88. After fixing the minor-fault retry loop, optimization took about 201 seconds in one round and passed final review. The original and optimized films were 60.171 and 60.200 seconds at 854×480; both played in the browser without page errors. Acceptable minor caption errors remain as advice. This sample does not establish flawless automatic output for every prompt.

Video optimization now defaults to `video_optimization.subtitle_renderer: ffmpeg`. Remotion still groups captions and creates SRT; FFmpeg adds static subtitles without browser decoding and drawing for every frame. Set `remotion` to use the original frame renderer. A local comparison using the same 60-second film and caption input took 112.14 seconds versus 4.48 seconds, with identical SRT text and timestamps. Full local optimization took 79.27 seconds, passed final review, preserved decoded audio, and kept the 60.171-second duration. This includes first-time scene downloads, transcription and upload; results from different machines or cache conditions are not directly comparable.

The latest cloud run reused verified caches for the same 60-second film. Backend optimization took 38.24 seconds, including 14.38 seconds for subtitles, 1.27 seconds for video upload and 5.24 seconds for final review. The browser took 77.10 seconds from submission until controls became available again. The downloaded film kept its 60.171-second duration, 24 fps and identical decoded audio; subtitles and the report downloaded, and final review passed. Five languages, edit validation, refresh recovery, playback and mobile width passed. Backend and browser times are recorded separately. Different cache conditions prevent a strict comparison with the earlier 201-second run.

Optimization reports record processing and upload times. Verified scene timelines persist in project snapshots: renewing signed URLs keeps the cache, while changed scene addresses, numbers, durations or content identifiers invalidate it. Subtitle rendering keeps the input frame rate and original audio. With no caption pages, it copies the video stream. Local media regressions cover landscape and portrait video, five subtitle languages, audio and frame counts.

Local Japanese task checked on 2026-10-28: all 8 scenes passed on their first attempt and final review passed. The film was 156.421 seconds, 854×480 at 24 fps. Desktop and mobile playback worked; all four delivery links returned 200; 24 SRT entries had valid times; decoded master and subtitle audio matched. Minor ASR noise has not been checked line by line, so this does not prove every subtitle is correct.

The Dockerfile and dependencies changed with the Seed Speech migration. The first cloud release must rebuild the full image; runtime layers from before the migration cannot be reused.

The Seed Speech migration changes Dockerfile and dependencies. The next cloud release requires a full image build and cannot reuse runtime layers from before this migration.

By default, builds export an OCI archive and upload in 32 MiB Registry v2 chunks, save acknowledged progress, refresh authentication and skip existing layers. If the server limits chunk size, it is halved automatically. Interrupt signals finish the current chunk before saving; expired upload sessions restart their current layer. Transport errors print their type only, without tokens or upload signatures. The private `.run/oci-upload-checkpoint.json` uses mode 600 and stays outside Git and the image. Use `--upload-mode buildx` for a normal push on a stable network.

## Open Source Rules

- Copyright (c) 2026 Alex Wang
- @author Alex Wang <https://github.com/wanglongxiao>
- @contact <https://www.linkedin.com/in/alexwanglx/>
- Preserve author and open-source notice comments in redistributed or modified copies.
- Do not commit real `apikey`, `AK/SK`, private bucket names, private assets, or generated results to a public repository.

## Version And Change History

- See `CHANGELOG.en.md` for the English changelog
- See `CHANGELOG.md` for the Simplified Chinese changelog
