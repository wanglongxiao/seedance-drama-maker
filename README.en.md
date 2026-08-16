# Seedance Drama Maker · AI Short-Drama Engine

English documentation. Simplified Chinese version: `README.md`

- 🌐 Live demo: <https://s0sij5su2u6qe1sds1et0.apigateway-ap-southeast-1.apigw-byteplus.com/>
- Current version: `2.0.0`
- Stack: `FastAPI`, `WebSocket`, multi-agent orchestration, `FFmpeg`
- BytePlus products: `TOS`, `Seed-Speech`, `Seed-2.1-turbo`, `SeeDream-5.0-pro`, `SeeDance-2.5`
- AI development tools: `Trae.ai`, `DeepSeek-V4-Flash-GA`, `GPT-5.5`

## ✨ Seedance 2.5 Highlights

Powered by the **SeeDance 2.5** video model — turn one sentence into a cinematic AI short drama:

- 🎬 **Up to 30-second shots**: each storyboard scene can run up to 30 seconds (6–30s adjustable) for more continuous long-take storytelling, with a final cut up to `1200` seconds.
- 🖼️ **Up to 50 reference inputs**: a single task can fuse up to 50 character / backdrop reference images for highly consistent characters and scenes across shots.
- 🌍 **14 languages natively supported**: native multilingual dialogue and narration across Chinese, English, Japanese, Spanish, and 14 languages total.
- 🎞️ **Cinematic audiovisual quality**: native audio-video sync, adaptive aspect ratio, and the final cut is remuxed to high-fidelity `MP4` (H.264/AAC, `+faststart`) that streams progressively in the browser.
- 🎭 **Character-outfit / scene-state variants**: per scene the system derives character-outfit images and scene time/weather-state images, and references them consistently in storyboards and scene videos.
- 🧩 **Line-art storyboard + AI review**: line-art multi-panel storyboards are generated first and auto-reviewed (style, no duplicated character and normal limbs, correct gender); failures regenerate automatically.
- 📄 **Comic PDF export**: when scene-video generation starts, the system generates a script-title PDF in parallel, including a cover, character page, and one storyboard page per scene, then exposes it in the Web UI for download.
- 🤖 **Fully automated multi-agent pipeline**: script → reference library → character-outfit/scene-state variants → storyboards → scene generation & review → long-video merge, end to end.

## Overview

This project focuses on AI long-video generation with both automatic and manual operating modes. While maintaining generation quality, it minimizes manual work across the entire pipeline, including script planning, character and backdrop alignment, storyboard continuity, quality review, regeneration control, and final video merge.

Typical use cases include:

- generating multi-minute social media advertising videos
- generating short dramas or comic-style dramas from a single sentence
- creating continuous long-form videos from text, uploaded references, and voice input
- running multiple long-video tasks in parallel out of the box

The system automatically coordinates script writing, character definitions, backdrop definitions, dialogue constraints, audio constraints, scene-video generation, AI quality review, and smooth transitions across scenes, turning a simple prompt into a full long-video workflow.

Current primary workflow:

```text
User Input -> Script Generation -> Reference Library Confirmation -> Character-Outfit/Scene-State Variants -> Storyboard Generation And Review -> Comic PDF Export + Scene Video Generation And Review -> Final Merge
```

The system focuses on:

- Generating script, character definitions, backdrop definitions, and storyboard scenes together
- Coordinating multiple reference images for characters and backdrops
- Keeping auto mode and manual mode consistent at the workflow level
- Combining automatic retry with manual takeover when video review fails
- Supporting multilingual UI and isolated multi-tab execution

## Showcase

### UI And Generation Flow

**UI overview and script generation**: from a single-sentence prompt the system auto-generates the title, style, character definitions, backdrop definitions, and storyboard scripts.

<img src="./demo/gen-story.png" alt="UI overview and script generation" width="100%" />

**Storyboard reference generation**: a line-art multi-panel storyboard is generated per scene and auto-reviewed by `StoryboardReviewAgent` (style / no duplicated character with normal limbs / correct gender).

<img src="./demo/storyboard-image.png" alt="Storyboard reference generation" width="100%" />

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
  Stores and serves uploaded assets, reference images, comic PDFs, scene videos, and final videos.
- `Seed-Speech`
  Converts voice input into text for the creation workflow.
- `Seed-2.1-turbo`
  Reviews generated scene videos for character consistency, physical plausibility, and script alignment.
- `SeeDream-5.0-pro`
  Generates and regenerates character and backdrop reference images.
- `SeeDance-2.5`
  Generates storyboard scene videos with 6–30s per shot, up to 50 reference inputs, and native dialogue in 14 languages with native audio-video sync.

All of the above are available through [byteplus.com](https://www.byteplus.com/).

## AI Development Tools

- `Trae.ai`: coding, debugging, repository cleanup
- `DeepSeek-V4-Flash-GA`: requirement clarification, solution discussion, documentation support
- `GPT-5.5`: code editing, debugging, logic cleanup, documentation rewriting

## Prerequisites

### Environment

- Python `3.9+`
- `ffmpeg` and `ffprobe` available on the local machine
- Network access to BytePlus / ModelArk / Seed-Speech / TOS

### Required Service Enablement

Before running the system, prepare the following:

- Enable the required `ModelArk` models and get the corresponding `apikey`
- Enable `Seed-Speech` and get `appid` and `apikey`
- Enable `TOS` and get valid `AK/SK`
- Create your own public-readable `bucket` in `TOS`
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
TOS_BUCKET=your-public-bucket
TOS_ENDPOINT=https://tos-ap-southeast-1.bytepluses.com

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

### 2. Script Generation

`ScriptAgent` generates:

- Title, style, and background
- Character definitions
- Backdrop definitions
- Storyboard scene scripts

Current rules:

- Total video duration limit: `1200` seconds
- Per-scene duration range: `6`–`30` seconds (tuned for `SeeDance-2.5`)
- Storyboard scene limit: `50`
- Character definition limit: `30`
- Backdrop definition limit: `30`
- Adjacent scenes must stay continuous without repeating the same narrative beat
- Per-scene special character outfits go into the character-action field, and scene time/weather state goes into the scene-description field; both are persisted for downstream image/video generation
- Raw LLM responses are written to backend logs

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
- NSFW asset uploads are supported by creating assets with `Moderation.Strategy = Skip`, which turns off Content pre-filter
- Assets enter the generation chain only after polling `GetAsset.Status = Active`

Authentication notes:

- Video inference still uses the `ModelArk` Bearer API key
- Asset-library APIs use `BytePlus AK/SK` signed requests
- Runtime configuration therefore requires both `MODELARK_API_KEY` and `BYTEPLUS_AK/BYTEPLUS_SK`
- The BytePlus account must also have the virtual asset-library subscription enabled; otherwise `CreateAssetGroup` returns `SubscriptionRequired`

### 3.2 Character-Outfit And Scene-State Images

After all character main images and scene main images are generated, the system derives variant images from each scene:

- Variants are generated only when a scene's character-action field contains an outfit different from the default, or its scene-description field contains a time/weather state different from the backdrop default
- Character-outfit image = the scene's outfit description + the corresponding character main image
- Scene-state image = the scene's time/weather state + the corresponding scene main image
- Variants across scenes are generated in parallel, bounded by the image concurrency setting (`video_generation.reference_images.max_concurrency`)
- Variants are deduplicated (each `character::outfit` or `scene::time::weather` is generated once) and preferred in storyboards and scene videos

### 3.3 Storyboard Generation And Review

`ImageAgent` generates a line-art multi-panel storyboard per scene, and `StoryboardReviewAgent` uses a multimodal vision model to check 3 hard conditions (any failure triggers auto-regeneration):

1. Line-art (black-and-white sketch) style with `4`–`7` panels (no fixed 2x3 layout required)
2. The same character does not repeat within a panel, and no character shows more than 2 arms or more than 2 legs
3. Character gender matches the cast list

Storyboard `Regenerate` logic, UI, and retry cap mirror scene videos, controlled by `storyboard_review.max_retries` (default `2`, total generations = first + retries).

### 4. Scene Video Generation

`VideoAgent` generates each scene video with:

- Character image (preferring the character-outfit image when the scene has one)
- Scene image (preferring the scene-state image when the scene has one)
- The scene's line-art 6-panel storyboard
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

Reference selection differs by video mode (switchable in the UI):

- **Parallel mode (default)**: multiple scenes are generated at the same time for faster throughput; each scene references only the character image / scene image / storyboard, without the previous scene's video
- **Extend mode**: scenes are generated serially, which is slower but optimizes scene-to-scene transitions — every scene except scene 1 additionally references the previous scene's generated video as `reference_video`, used only to keep character appearance/outfit/scene/lighting consistent while advancing the new scene from a fresh camera angle, avoiding near-identical adjacent shots

Key rules:

- Automatic failure retry counts are controlled by YAML (`video_review.max_retries`, `video_generation.scene_total_generate_limit`)
- Manual `Regenerate` clicks do not consume the automatic failure budget
- The video prompt appends a no-background-music constraint by default unless the user explicitly requests a music style

### 4.1 Project Ending And Cleanup

When the workflow enters scene-video generation, the system starts comic PDF generation in parallel:

- The PDF is named after the script title and uploaded to the `documents/comics` path in `TOS`
- Page 1 shows the script title, era, and background; page 2 shows main character names and character images
- Each following page maps to one scene in order, with the storyboard image on top and the scene description plus dialogue/narration below
- The Web UI shows the PDF download entry between the storyboards section and the scene-videos section
- Project cleanup preserves the comic PDF and does not treat it as a temporary file

The Web UI now includes an `End Project` button. Cleanup can be triggered in three ways:

- the user clicks `End Project`
- the user closes the current project page
- the user closes the browser and the frontend reports cleanup with `sendBeacon` / `keepalive fetch`

Cleanup removes:

- temporary files and directories in `TOS`
- virtual asset-library assets for the project
- the asset group associated with the project

If the project has already produced a final video, the final export under `videos/final` is preserved.

### 5. Video Review And Flow Control

`VideoReviewAgent` uses `Seed-2.1-turbo` to review each scene video.

Review dimensions:

- Character consistency
- Physical world rules
- Semantic consistency with the storyboard script

Current default review configuration:

- `video_review.pass_threshold = 60`
- `video_review.max_retries = 2`
- `video_review.default_mode = auto`

Flow rules:

- Auto mode: failed reviews regenerate automatically; after the retry limit, the highest-scoring candidate is selected to continue the workflow
- Manual mode: review still runs, but regeneration is not automatic; the user may continue manually
- The workflow never moves forward while there are unfinished automatic or manual regeneration jobs
- Cloud long-running tasks route messages through the stable `client_id` to the latest WebSocket connection, so generation and review results still reach the active frontend after gateway or browser reconnects
- In parallel mode, each scene is persisted as soon as it finishes, preventing completed late-index scenes from being lost when another scene hits a 504 timeout
- When auto mode reaches the retry cap and selects the highest-scoring candidate, the scene is marked as accepted-over-retry and does not block the final merge

### 6. Merge

`MergeAgent` and `FFmpegService` handle the final merge.

Current default merge settings:

- `SeeDance-2.5` generation tasks and reference videos all use `MOV`; `FFmpeg` concatenates the segments into a `MOV`
- Before concatenation each scene segment is normalized to uniform parameters (H.264/`yuv420p` + AAC `48kHz` stereo + CFR) to avoid mismatched audio sample rates/codecs causing silent second halves or broken web playback
- After merging, the output is remuxed to `MP4` (stream-copy `-c copy` first, falling back to `H.264/AAC` re-encode) with `-movflags +faststart` moving the `moov atom` to the front for progressive browser playback
- The final uploaded and browser-played cut is `MP4`
- `merge.temporary_edge_trim = off`
- `trim_previous_end_frames = 0`
- `trim_next_start_frames = 1`

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
  |- StoryboardReviewAgent
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
- The UI supports `zh-CN`, `zh-TW`, `en`, `ja`, and `es`; `SeeDance-2.5` video dialogue is natively supported in 14 languages
- The pipeline generates in `MOV` and remuxes the final cut to `MP4` (`+faststart`), with `TOS` normalizing `Content-Type` for reliable web playback
- Each project is bound to its browser connection to support isolated multi-tab execution
- Long-running background tasks can continue pushing results after WebSocket reconnects through a stable client ID
- Reference generation, video generation, review, and merge are rendered progressively in the UI
- Exported artifacts such as comic PDFs and final videos are preserved during project-ending cleanup

## Usage

### Install Dependencies

```bash
pip install -r requirements.txt
```

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
- create a project-scoped virtual asset group and register the portrait asset
- generate the script with the real script model endpoint
- inject character references into video generation with `asset://asset-id`
- complete multi-scene generation, AI review, and final merge
- trigger `End Project` cleanup to remove temporary `TOS` files, assets, and the asset group

Notes:

- The latest real E2E run successfully produced a final video under `videos/final`
- Scene review, final merge, and project-ending cleanup were all validated in the real cloud workflow
- Cleanup removes temporary materials and intermediate outputs while preserving the final export

## Repository Hygiene And Safe Commits

To prevent secret leakage or accidental publication of test artifacts, this repository follows these rules:

- Real `apikey`, `AK/SK`, `appid`, and private bucket names must stay only in the local `.env`
- `.env`, `/.run/`, `*.log`, cache directories, `/plan.md`, and `/docs/` must not enter Git
- README files, `config.yaml`, and `.env.example` keep placeholders only and never contain real credentials
- Before committing, make sure test audio, test images, service logs, and temporary exports are not staged

## Open Source Rules

- Copyright (c) 2026 Alex Wang
- @author Alex Wang <https://github.com/wanglongxiao>
- @contact <https://www.linkedin.com/in/alexwanglx/>
- Preserve author and open-source notice comments in redistributed or modified copies.
- Do not commit real `apikey`, `AK/SK`, private bucket names, private assets, or generated results to a public repository.

## Version And Change History

- See `CHANGELOG.en.md` for the English changelog
- See `CHANGELOG.md` for the Simplified Chinese changelog
