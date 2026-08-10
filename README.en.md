# Seedance Drama Maker · AI Short-Drama Engine

English documentation. Simplified Chinese version: `README.md`

- Current version: `2.0.0`
- Stack: `FastAPI`, `WebSocket`, multi-agent orchestration, `FFmpeg`
- BytePlus products: `TOS`, `Seed-Speech`, `Seed-2.1-turbo`, `SeeDream-5.0-pro`, `SeeDance-2.5`
- AI development tools: `Trae.ai`, `DeepSeek-V4-Flash-GA`, `GPT-5.5`

## ✨ Seedance 2.5 Highlights

Powered by the **SeeDance 2.5** video model — turn one sentence into a cinematic AI short drama:

- 🎬 **Up to 30-second shots**: each storyboard scene can run up to 30 seconds (6–30s adjustable) for more continuous long-take storytelling, with a final cut up to `600` seconds.
- 🖼️ **Up to 50 reference inputs**: a single task can fuse up to 50 character / backdrop reference images for highly consistent characters and scenes across shots.
- 🌍 **14 languages natively supported**: native multilingual dialogue and narration across Chinese, English, Japanese, Spanish, and 14 languages total.
- 🎞️ **Cinematic audiovisual quality**: native audio-video sync, adaptive aspect ratio, and high-fidelity `MOV` (H.264/AAC) output that streams progressively in the browser.
- 🤖 **Fully automated multi-agent pipeline**: script → reference library → scene generation → AI review → long-video merge, end to end.

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
User Input -> Script Generation -> Reference Library Confirmation -> Scene Video Generation And Review -> Final Merge
```

The system focuses on:

- Generating script, character definitions, backdrop definitions, and storyboard scenes together
- Coordinating multiple reference images for characters and backdrops
- Keeping auto mode and manual mode consistent at the workflow level
- Combining automatic retry with manual takeover when video review fails
- Supporting multilingual UI and isolated multi-tab execution

## Showcase

### Generation Process

#### ScreenRecord-0

<img src="./demo/ScreenRecord-0.png" alt="ScreenRecord-0" width="100%" />

#### ScreenRecord-1

Please play: [demo/ScreenRecord-1.mp4](./demo/ScreenRecord-1.mp4)

#### ScreenRecord-2

Please play: [demo/ScreenRecord-2.mp4](./demo/ScreenRecord-2.mp4)

#### ScreenRecord-3

Please play: [demo/ScreenRecord-3.mp4](./demo/ScreenRecord-3.mp4)

### Generated Results

#### DemoVideo-1

Please play: [demo/DemoVideo-1.mp4](./demo/DemoVideo-1.mp4)

#### DemoVideo-2

Please play: [demo/DemoVideo-2.mp4](./demo/DemoVideo-2.mp4)

#### DemoVideo-3

Please play: [demo/DemoVideo-3.mp4](./demo/DemoVideo-3.mp4)

## BytePlus Products Used

- `TOS`
  Stores and serves uploaded assets, reference images, scene videos, masked first frames, and final videos.
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

- Total video duration limit: `600` seconds
- Per-scene duration range: `6`–`30` seconds (tuned for `SeeDance-2.5`)
- Storyboard scene limit: `50`
- Character definition limit: `30`
- Backdrop definition limit: `40`
- Adjacent scenes must stay continuous without repeating the same narrative beat
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

### 4. Scene Video Generation

`VideoAgent` generates each scene video with:

- Character reference images
- Backdrop reference images
- The current storyboard scene script
- User style requirements

Key rules:

- Automatic failure retry counts are controlled by YAML
- Manual `Regenerate` clicks do not consume the automatic failure budget
- The last frame of the previous scene is used as the first-frame reference only when backdrop overlap and script continuity both hold
- Detected faces in that first frame are masked in pure black before uploading to `TOS`
- The video prompt appends a no-background-music constraint by default unless the user explicitly requests a music style

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

### 6. Merge

`MergeAgent` and `FFmpegService` handle the final merge.

Current default merge settings:

- Video output format: `MOV` (`video_generation.output_format = mov`)
- `SeeDance-2.5` generation tasks and reference videos all use `MOV`; `FFmpeg` processes `MOV` directly and outputs a merged `MOV`
- Merge enables `-movflags +faststart` so the `moov atom` moves to the front for progressive browser playback
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
- The video pipeline outputs `MOV` end to end, with `TOS` normalizing `Content-Type` for reliable web playback
- Each project is bound to its browser connection to support isolated multi-tab execution
- Reference generation, video generation, review, and merge are rendered progressively in the UI

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

## Open Source Rules

- Copyright (c) 2026 Alex Wang
- @author Alex Wang <https://github.com/wanglongxiao>
- @contact <https://www.linkedin.com/in/alexwanglx/>
- Preserve author and open-source notice comments in redistributed or modified copies.
- Do not commit real `apikey`, `AK/SK`, private bucket names, private assets, or generated results to a public repository.

## Version And Change History

- See `CHANGELOG.en.md` for the English changelog
- See `CHANGELOG.md` for the Simplified Chinese changelog
