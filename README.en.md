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

This project focuses on AI long-video generation with both automatic and manual operating modes. While maintaining generation quality, it minimizes manual work across the entire pipeline, including script planning, character and backdrop alignment, scene continuity, quality review, regeneration control, and final video merge.

Typical use cases include:

- generating multi-minute social media advertising videos
- generating short dramas or comic-style dramas from a single sentence
- creating continuous long-form videos from text, uploaded references, and voice input
- running multiple long-video tasks in parallel out of the box

The system automatically coordinates script writing, character definitions, backdrop definitions, dialogue constraints, audio constraints, scene-video generation, AI quality review, and smooth transitions across scenes, turning a simple prompt into a full long-video workflow.

Current primary workflow:

```text
User Input -> Script Generation -> Reference Library Confirmation -> Character-Outfit/Backdrop-State Variants -> Scene Video Generation And Review -> Merge -> Video Optimization And Subtitles
```

The fifth step runs locally and in the cloud, reviews the actual merged film and transcribes its soundtrack. `video_optimization.fast_triage` is on by default. One quick review decides whether each faulty fragment is critical to the story, rates its local quality, and judges whether viewers cannot accept it. Noncritical faults are cut. Critical faults are regenerated only when the score is below `critical_regenerate_below` (default 40) AND `viewer_unacceptable` is true. Other imperfections remain as advice. Key speech is protected; disposable speech is cut as complete utterances. Cuts retain the 20% budget and minimum remaining clip length. Remotion renders timed subtitles; original/final films, SRT, reports and the HyperFrames timeline are saved under `VIDEO_OUTPUT_DIR/<project_id>/` (local default `outputs`, cloud `/tmp/seedance-outputs`). Videos, subtitles and reports are uploaded to private TOS.

Fast mode skips extra reasoning, dense-frame independent confirmation and further causal audits. `fast_review_max_tokens` defaults to 1600. Unchanged source reviews are cached. After independently approved local replacements, untouched observations can be reused; the final film still receives one quick review. The normal path uses two reviews, for the source and final film. Patches still verify the requested fix, soundtrack and adjoining state. A noncritical cut blocked by a timing or speech limit is retained with its reason and never becomes an automatic reshoot. The status bar includes story/quality triage, and logs include review mode and elapsed time. Set `fast_triage: false` to restore detailed review; the independent confirmation and causal-audit rules below apply only in that mode.

After merging, the Web UI offers multiple cuts and local repairs, lighting correction, subtitles and automatic repair. Separate cuts with new lines or commas. Each repair row pairs its start and end times with its own instructions; rows can be added or removed. Scene order and whole-scene reshoots are hidden from this form; older API requests remain supported. Automatic reshoots target confirmed faulty spans, such as 5–7 seconds. Patches generate and review independently in parallel, then replace together in the same master, keeping original audio and checking lip sync and boundary continuity. The bottom status bar stays on one line, centered, using the same 13px font as other steps. It shows the project name, review, issue judgment, repair plan, patch preparation, generation, verification, replacement, editing, subtitles and final review, with status and elapsed time. Scroll sideways through parallel tasks or hover for history. Refresh restores progress. The original merged URL remains available for comparison.

The workflow adopts useful methods from an external film program: matching light within the same set, checking anatomy and prop continuity, and repairing scenes from review checkpoints. Small brightness corrections preserve scripted light switches and other motivated changes. Cuts and scene order use actual media durations to map captions and must not split complete dialogue. If review is unavailable, the current footage is kept and the reason is recorded. Films that still fail after the repair budget are marked clearly; model output cannot be guaranteed free of continuity errors.

For requested Chinese or Cantonese speech, Seed Speech ASR preserves actual words and timestamps. Whisper is the documented fallback, with turbo for Cantonese. Empty time spans are removed, and identical audio can reuse its transcript after picture repairs. A Whisper `zh` label cannot prove Mandarin pronunciation. Script text never replaces the transcript. Pre-merge review uses configurable `video_review.pass_threshold`, default 80. Local repair has its own `fragment_max_attempts` budget (2 per span), `fragment_max_seconds` (6 seconds), and `max_repair_fragments_per_round` (8 spans). Set `video_optimization.repair_workers` for concurrency.

Caption corrections only change equally pronounced Chinese characters in matched spoken lines, retaining word timestamps. If a speechless shot reads production instructions aloud, native ASR and an independent Whisper pass must agree before replacing that local audio with nearby speech-free ambience. Picture and duration stay fixed. Set `remove_prompt_narration` to control this repair. Moderate review preserves genre magic and effects, and does not reshoot tiny framing changes or redundant foreshadowing.

The second review receives time windows without the first review's accusations, reducing repeated false reports. `video_optimization.repair_min_severity` defaults to `major`: minor decoration changes stay in report advice; clear anatomy, contact, identity and key causal failures still require repair. Parallel patches use content hashes as object names so uploads in the same second cannot overwrite another patch before review.

`audit_story_impact` is enabled by default. After independent confirmation, a further audit checks the full film and sequential frames for actual identity, contact or causal failures and saves its observations. An unlisted awakening effect or camera change alone is not a failure. If visible motifs match and recognition works, or an awakening and its later battle remain coherent, a different pattern name or effect style is advisory. Less spectacle or less literal execution alone does not block a film; real anatomy, contact, identity and causal failures still do. Low confidence, unavailable review or invalid results cannot release a blocker. Repair references focus on the visible corrected detail instead of an unrelated portrait or reaction.

Final-film findings also receive independent confirmation from timestamped frames of the actual final film. Failed patch history is not sent as visual evidence. A manual rollback starts a fresh fragment budget while retaining audit history. Incorrect props or anatomy in boundary references must not override the requested correction. Local patches clear full-scene motion and sound directions and focus the camera on the repair. Generated media URLs, durations and boundary references remain in the history.

`repair_reference_after_failure` is on by default. After a text-only patch fails, ImageAgent creates a corrected target frame from actual boundary frames and matching character references. Independent verification checks that the repair is visible, contact is correct, and props are not duplicated. Failed targets are redrawn with observed feedback, up to `repair_reference_max_attempts` (default 2). Only verified targets guide video generation; they are not evidence for passing video review. Target generation and verification both appear in the status bar. An explicit optimization retry starts a new local repair budget while preserving accepted patches in the working master; automatic rounds within one run share that budget.

Each patch first turns the observed fault into an affirmative corrected state, necessary local motion and visible checks. The original complaint and repair goal are saved separately, so an unwanted intact blade cannot become the target for a broken-blade repair. A verified target anchors generation with `first_frame`. Output inherits that image's dimensions and does not mix it with ordinary portraits or flawed boundary references. The actual trimmed patch still needs independent review.

`models.video_review.disable_thinking` is enabled by default so visual reviews directly return observed evidence and conclusions. Set it to `false` to enable model reasoning; an explicit per-call `thinking` setting takes priority. This affects only the review model and keeps thresholds and independent confirmation requirements. Changing this option or the review model invalidates the source review cache.

Hand targets first receive an isolated anatomy check based on fingertip contours, palm attachments and joints: one thumb plus four fingers. Only passing targets receive a separate comparison of visible costume, identity and lighting against boundary frames. A still frame need not prove unseen faces, props, motion or audio. `anatomy_review_thinking` enables reasoning by default, with `anatomy_review_max_tokens` set to 3000. These options also apply to digit repairs, full-film window verification and digit impact audits; changing them invalidates the review cache. Failed or unavailable target verification stops patch generation.

An explicit local repair must pass and be applied before that run can complete, even if the full-film viewer later misses the defect. Outstanding ranges are recorded in `unresolved_manual_repairs`.

When the model's minimum output is longer than a silent repair interval, `select_silent_patch_window` selects the actual corrected action rather than always keeping the setup. The selected window still needs independent repair verification. Original audio and master timing remain fixed. Spoken patches retain the specified starting word timestamps.

Review uses fixed identity, anatomy, contact, prop, plot, lighting and dialogue categories, while accepting translated category names. Independent review can narrow an earlier window to the actual defect. Each issue targets a short occurrence, and patches repair only that interval rather than replaying the background action. After Remotion renders captions, the edited soundtrack is restored and the actual timeline duration is enforced to prevent AAC padding from extending the film.

Overlapping time windows and broad categories alone do not establish independent confirmation. Explicit extra fingers, missing fingers, duplicated limbs, fusion and penetration must agree between the two reports. A different defect discovered in the same window needs its own confirmation before repair.

Set `video_review.sample_fps` and `video_optimization.review_fps` to control sampling, default 3 frames per second, range 0.2–5. Short patches also need several observations to reduce missed contact actions and brief effects. Higher density increases review time and model use. Changing it invalidates cached review evidence.

Independent confirmation also receives sequential frames from the same master, with 0.3 seconds of context on each side, up to 24 frames per window and master timestamps. Footage frames are kept separate from character references. This helps prevent missed short actions. A hidden engraving is not proof of a wrong engraving; the presence of an action and a decorative difference must be judged separately.

A local patch must pass the score gate and provide explicit timed evidence that its required defect is fixed. Matching characters, style or a setup reaction is insufficient. Patch names and timers advance through preparation, generation and review in the status bar. Rolling back optimization starts from the original merge and retains review history.

Skills use the bundled `app/video_skills` references and hashes by default; override with `VIDEO_SKILLS_ROOT`. Node tools use `runtime/package-lock.json`, live at `/opt/video-tools` in the cloud and default to `~/.local/share/seedance-drama-maker/video-tools` locally. The skill from `znyupup/ai-video-editing-skill` is named `vlog-auto-edit`. Install Python tools with `uv pip install --python .venv/bin/python -r requirements-video-tools.txt`. Remotion captions provides subtitles. The v3.0 cloud image includes the full optimization runtime.

The system focuses on:

- Generating script, character definitions, backdrop definitions, and scene scripts together
- Coordinating multiple reference images for characters and backdrops
- Keeping auto mode and manual mode consistent at the workflow level
- Combining automatic retry with manual takeover when video review fails
- Supporting multilingual UI and isolated multi-tab execution
- Isolating cloud interactive requests: uploads and speech-to-text use a dedicated thread pool so long image/video generation jobs cannot starve them and trigger gateway timeouts

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
  Powers the main conversational agent and scene-script generation.
- `Seed-2.1-turbo`
  Reviews generated scene videos for character consistency, physical plausibility, and script alignment.
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

- Python `3.11`, Node `22+`, Chromium, and CPU PyTorch for Whisper
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

# Allow partial/full nudity only for characters explicitly identified as age 18+
NSFW_ENABLED=off
```

Keep placeholders in `config.yaml`; values are injected from `.env` at runtime:

When `NSFW_ENABLED=on`, the script, character reference, outfit reference, and scene-video pipelines consistently enable the adult-nudity policy. When disabled, those pipelines explicitly request non-explicit clothed output. The policy is implemented by shared code and public prompts, with no private prompt directory dependency.

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
- Per-scene special outfits and hairstyle changes are written explicitly to `character_outfits`, while backdrop time/weather state is written explicitly to `scene_state`; both fields appear before the scene description and are persisted for downstream image/video generation
- Backend `INFO` logs contain only compact metadata such as model, message count, and prompt/payload length; full prompts, request bodies, asset polling results, and heartbeats are limited to `DEBUG` to prevent oversized long-running logs

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

### 3.2 Character-Outfit And Backdrop-State Images

After all character main images and scene main images are generated, the system derives variant images from each scene:

- Variants are generated only when `character_outfits` contains an outfit different from the default `clothing` / `hairstyle`, or when `scene_state` / `time_of_day` / `weather` differs from the backdrop default
- Character-outfit image = the scene's outfit description + the corresponding character main image
- Backdrop-state image = the scene's time/weather state + the corresponding scene main image
- Variants across scenes are generated in parallel, bounded by the image concurrency setting (`video_generation.reference_images.max_concurrency`)
- Character outfits are deduplicated by stable visual traits such as clothing, nudity level, hairstyle, and injuries; backdrop states are normalized by time and weather so minor wording differences do not trigger duplicate generation
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
- When `NSFW_ENABLED=on` and a character is explicitly age 18+, a scene whose text indicates ongoing intimacy inherits the previous scene's partial/full-nudity outfit state; another character image is used only after the script clearly ends that state
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

- temporary files and directories in `TOS`
- virtual asset-library assets for the project
- the asset group associated with the project

If the project has already produced a final video, the final export under `videos/final` is preserved.

### 4. Video Review And Flow Control

`VideoReviewAgent` uses `Seed-2.1-turbo` to review each scene video.

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
- Final videos are preserved during project-ending cleanup
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

Install FFmpeg/FFprobe and Chromium on the host. Local Whisper downloads models on first use; the cloud image caches `small` and `turbo`. Remotion can download its browser on macOS; on Linux set `REMOTION_BROWSER_EXECUTABLE=/usr/bin/chromium`.

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
- Cleanup removes temporary materials and intermediate outputs while preserving the final export

## Repository Hygiene And Safe Commits

To prevent secret leakage or accidental publication of test artifacts, this repository follows these rules:

- Real `apikey`, `AK/SK`, `appid`, and private bucket names must stay only in the local `.env`
- `.env`, `/.run/`, `*.log`, cache directories, `/plan.md`, and `/docs/` must not enter Git
- README files, `config.yaml`, and `.env.example` keep placeholders only and never contain real credentials
- Before committing, make sure test audio, test images, service logs, and temporary exports are not staged
- Temporary share packages, archive exports, key files, and local deployment JSON stay outside Git and the image context. Before release, scan staged files and history; report file paths only, without printing secret values

### Cloud Release

Release in this order: `git commit → git push → rebuild image → release veFaaS`. `Dockerfile` includes Python 3.11, Node 22, FFmpeg/FFprobe, Chromium, Noto fonts, CPU PyTorch, cached Whisper `small` / `turbo`, locked HyperFrames/Remotion packages, prompts and bounded skill references. Start with `./run.sh`; one Uvicorn process listens on `_FAAS_RUNTIME_PORT` (default 8000). Background task snapshots are saved to private TOS.

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

Cloud settings include `BYTEPLUS_AK/SK`, `TOS_*`, `MODELARK_*`, five model `*_ENDPOINT` / `*_API_KEY` pairs, `SPEECH_*`, `ASR_RESOURCE_ID`, `ASSET_LIBRARY_*`, `NSFW_ENABLED`, and `ACCESS_PASSWORD` / `ACCESS_SECRET` / `ACCESS_COOKIE_SECURE`. Runtime paths are `VIDEO_SKILLS_ROOT=/app/app/video_skills`, `VIDEO_TOOLS_RUNTIME_DIR=/opt/video-tools`, `WHISPER_CACHE_DIR=/opt/whisper-models`, `VIDEO_OUTPUT_DIR=/tmp/seedance-outputs`, and `REMOTION_BROWSER_EXECUTABLE=/usr/bin/chromium`. Only variables referenced by configuration are copied.

`/healthz` returns health and version only. Authenticated `/api/runtime` reports tools and skills without secrets. Run `python -m app.services.runtime_service --smoke` inside the image to check actual editing, captions, timeline lint, both Whisper models, audio and duration. WebSocket delivery is local to its instance; snapshot polling covers updates made by other instances. Restarting interrupted jobs after instance removal and arbitrating concurrent writes still need a job queue and shared locks; TOS snapshots alone do not provide them.

The deployment script enables `RUNTIME_SMOKE_ENABLED=true`. After login, `POST /api/runtime/smoke` runs the same synthetic media check on the actual cloud instance, cleans its temporary files and makes no paid generation calls. Each instance allows one check at a time. Set the flag to `false` to disable this diagnostic endpoint.

## Open Source Rules

- Copyright (c) 2026 Alex Wang
- @author Alex Wang <https://github.com/wanglongxiao>
- @contact <https://www.linkedin.com/in/alexwanglx/>
- Preserve author and open-source notice comments in redistributed or modified copies.
- Do not commit real `apikey`, `AK/SK`, private bucket names, private assets, or generated results to a public repository.

## Version And Change History

- See `CHANGELOG.en.md` for the English changelog
- See `CHANGELOG.md` for the Simplified Chinese changelog
