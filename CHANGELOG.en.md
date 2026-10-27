# Changelog

## [3.0.1] - 2026-10-28

- Enabled automatic multilingual detection in BytePlus Seed Speech ASR for Japanese, Korean, Spanish, Portuguese and mixed audio. Updated caches and subtitle language labels so brief foreign fillers do not override the main language. Preserve real speech, word/utterance times and spaces between Latin words; failed recognition never writes a successful cache.
- Local repairs now edit original scene videos with Seedance 2.5: group ranges per scene, run scenes in parallel, validate video assets, review each interval independently, align duration and preserve original audio. Removed unused corrected-reference and first-frame repair code.
- Fixed indefinite waiting after repair budgets run out. Pending scene generation or review blocks merge and optimization. Ended projects stop polling, archiving, reviewing and retrying. Model errors keep bounded response details without full request payloads.
- Approved completion keeps the master, final film, subtitles, report and recovery state. Failed or disconnected projects remain available for retry; explicit project end deletes all resources. Restored pages skip reference images and scenes already cleaned.
- Removed local Whisper/PyTorch models and image cache layers. Runtime checks now cover media tools and BytePlus ASR configuration. Changed dependencies require a full image rebuild.
- Updated editing, repair-limit and project-end text in all five locales, refreshed static caches and both README files. Removed local monitoring, logs, temporary test output and obsolete unit tests while keeping valid behavior regressions.
- Local Japanese delivery checks passed for desktop/mobile playback, four downloads, 24 subtitle time ranges and unchanged audio. Minor ASR noise has not been checked line by line.

## [3.0.0] - 2026-10-27

- Default subtitles now use Remotion grouping/SRT and FFmpeg rendering. The same 60-second sample rendered locally in 4.48 seconds versus 112.14 seconds. Keep original audio, input frame rate, final review and the optional Remotion frame renderer.
- Reuse verified scene timelines and invalidate them when footage changes. Reports include processing and upload times. Added local landscape/portrait, five-language subtitle, audio/frame-count and cache-invalidation checks; refreshed five locale labels and cache versions.
- Shortened both project overviews; removed unused ending-frame, transition, image-stitching and video-run code plus an obsolete test. Removed duplicate payload and raw model debug output. Keep docs and tests local, retain demo assets in GitHub, and exclude caches and debug output from releases.
- Added post-merge optimization with local cuts, parallel short repairs, independent patch reviews, original audio, lighting matching, timed subtitles and final review.
- One fast review judges story importance, local score and viewer acceptance. Cut noncritical faults first; reshoot critical faults only below 40 when clearly unacceptable.
- Raised the default pre-merge review threshold to 80. Service failures remain separate from content rejection.
- Ship bounded references and hashes for seven video skills. Generation models receive adapted stage guidance.
- Added a reusable BytePlus CLI deployment script and linux/amd64 image with FFmpeg/FFprobe, Node 22, Chromium, CPU PyTorch, cached Whisper small/turbo and locked Node packages.
- Cloud setup uses 1–5 instances, an HTTPS API gateway, shared signed access cookies, HTTP/WebSocket authentication and TOS snapshot recovery. Local access stays open by default.
- Updated all five UI languages, 13px download links, centered single-line progress and static cache versions.
- Scan source, staged files and Git history before release. Build from committed source; exclude secrets, logs, tests, IDE files and local output. Redact keys, endpoints and signed URLs from logs.
- Large images upload in 32 MiB chunks by default, save acknowledged progress and refresh authentication. Valid sessions resume; expired sessions repeat their current layer only.

- Added a director review for generated and revised scripts, with scene evidence for cause and effect, repeated story beats, pacing, tone, genre, and ending payoff.
- Check candidate findings against the script before requesting edits. Allow implied actions, object inserts, and continuing camera and light setups without padding descriptions to a fixed sentence count.
- Preserve planned scene timing instead of rewarding longer prose. Configure generation and revision review rounds with `script_generation.validation_max_rounds` (3 total rounds by default).
- When review rounds run out, manual mode pauses for a choice to restart the project or continue with the best draft; automatic mode continues with the best draft. Pending choices and findings persist across page refreshes.
- Added genre-specific story guidance with optional hooks, and fixed total-duration parsing and silent-script validation.
- Removed tests for exact prompt wording, frontend version strings, logic copied into tests, and duplicate cases. Kept behavior tests for generation, script recovery, asset binding, review, merging, and cloud recovery.

## \[2.0.0] - 2026-08-16

### Added

- Added support for upgrading to the `SeeDance-2.5` video generation model, with scene-video generation and the final merge flow aligned around `MOV` output.
- Added concurrent scene-video generation according to the configured concurrency limit, improving throughput for long multi-scene videos.
- Added comic PDF generation: when scene-video generation starts, the system exports a script-title PDF in parallel, including a cover page, character page, and one storyboard page per scene, then uploads it to `TOS` for download in the Web UI.
- Added project refresh recovery through `sessionStorage` and `/project/{id}/restore`, restoring scripts, reference images, scenes, review status, and comic PDF download status.

### Improved

- Improved cloud stability for long-running scene generation and review tasks. Background tasks now route messages through the stable `client_id` to the latest WebSocket connection, reducing failures where API gateway 504s or browser reconnects prevent the frontend from receiving results.
- Improved parallel scene processing by persisting project state as each scene completes, preventing late-index scene or review results from being lost during long-running jobs.
- Improved auto-review fallback flow: when the retry limit is reached, the workflow can accept the highest-scoring result and continue merging, marking the scene as accepted over retry.
- Improved thread-pool isolation by separating interactive requests from generation tasks, reducing upload and state-restore starvation during multi-window concurrent generation.
- Improved outfit/backdrop variant deduplication and explicit scene binding. Ongoing adult scenes can inherit the previous scene's outfit state, while character-consistency review prefers the current scene's outfit references.
- Improved service logging by moving heartbeats, asset polling, and complete model request bodies to `DEBUG`; `INFO` now keeps compact request summaries to prevent rapid log growth during long jobs.
- Switched TOS access to private buckets: uploads no longer receive public-read ACLs, browsers and external models use automatically refreshed temporary URLs, backend downloads and copies use the TOS SDK directly, and signed query parameters are removed from logs.

### Fixed

- Fixed auto mode not advancing after image upload because draft project ID initialization happened before the auto-start decision.
- Fixed merge-blocking logic for regenerated scenes so final merge does not start while any regenerated scene is unfinished or has not passed review.
- Fixed incomplete progress-bar and completed-step recovery after page refresh.
- Fixed frontend crashes when project cleanup receives a non-JSON 504 response from upstream infrastructure.
- Fixed tofu-box text corruption in cloud-generated comic PDFs for Chinese, Japanese, Korean, and emoji text. PDF generation now requires a CJK-capable font, and the runtime image must include `fonts-noto-cjk`, `fonts-noto-color-emoji`, `fontconfig`, and `ffmpeg` / `ffprobe`.
- Fixed scene-number drift and races when failed parallel scenes are removed. Generation now binds a content fingerprint to each scene video; merge rejects duplicate, missing, extra, or mismatched scenes and follows storyboard order exactly.
- Fixed outfit-image lookup repeatedly rebuilding the complete variant plan; lookup now resolves directly through `scene_numbers`, `variant_key`, and visual-equivalence fallback.
- Fixed review-service network failures being treated as video-quality failures and triggering costly regeneration; project termination is also rechecked before archive, review, and retry work.

### Removed

- Removed the Extend video-generation mode and previous-scene video reference chain; generation now uses references matched to the current scene only.
- Removed key-action reference images and their generation, review, API, and frontend paths.

### Documentation

- Updated bilingual README files to document `SeeDance-2.5`, concurrent video generation, comic PDF export, cloud runtime font/media dependencies, credential governance, and deployment requirements.
- Updated multilingual i18n copy for comic PDF download, generating, ready, and failure states.

## \[1.1.0] - 2026-05-09

### Added

- Added unified application versioning and static asset versioning in `app/__init__.py`.
- Added coordinated support for both character reference libraries and backdrop reference libraries in the generation flow.
- Added more complete bilingual project documentation and standalone `CHANGELOG` files.
- Added screenshot and screen-recording references to the repository documentation.
- Added tests for multi-reference-image mapping in `ImageAgent`.

### Improved

- Improved the scene video generation strategy to increase long-video completeness and continuity between scenes.
- Improved credential governance by emphasizing that model `apikey`, `AK/SK`, and `appid` are read only through `config.yaml`.
- Improved frontend asset cache control and version handling for more predictable releases.
- Improved multilingual resource naming and structure for easier frontend maintenance.

### Changed

- Renamed and reorganized the video review implementation around the `video_review` naming model for clearer responsibilities.
- Split common locale handling and temporary task path logic into dedicated utility modules.
- Added unified author and open-source notice headers to core modules.

### Documentation

- Rewrote and aligned `README.md`, `README.en.md`, and related project description content.
- Clarified credential governance rules, prerequisites, common commands, and changelog entry points for the open-source repository.

## \[1.0.0] - 2026-03-03

### Initial Release

- Established the multi-agent video generation pipeline covering script generation, reference image generation, scene video generation, review, and merge.
- Integrated the core BytePlus capabilities: `TOS`, `Seed-Speech`, `Seed-2.0`, `SeeDream-5.0`, and `SeeDance-2.0`.
- Added the FastAPI + WebSocket service foundation and basic project state management.
- Added a multilingual frontend, realtime status updates, and the base interaction workflow.
