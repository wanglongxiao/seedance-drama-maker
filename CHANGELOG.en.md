# Changelog

## \[2.0.0] - 2026-08-16

### Added

- Added support for upgrading to the `SeeDance-2.5` video generation model, with scene-video generation, reference-video inputs, and final merge flow aligned around `MOV` output.
- Added dual video generation modes: `parallel` and `extend`:
  - `parallel` generates multiple scenes concurrently according to the configured video-generation concurrency, improving throughput for long multi-scene videos.
  - `extend` generates scenes sequentially and uses the previous scene video as `reference_video` starting from scene 2, improving continuity of characters, outfits, settings, and lighting.
- Added comic PDF generation: when scene-video generation starts, the system exports a script-title PDF in parallel, including a cover page, character page, and one storyboard page per scene, then uploads it to `TOS` for download in the Web UI.
- Added project refresh recovery through `sessionStorage` and `/project/{id}/restore`, restoring scripts, reference images, scenes, review status, and comic PDF download status.

### Improved

- Improved cloud stability for long-running scene generation and review tasks. Background tasks now route messages through the stable `client_id` to the latest WebSocket connection, reducing failures where API gateway 504s or browser reconnects prevent the frontend from receiving results.
- Improved parallel scene processing by persisting project state as each scene completes, preventing late-index scene or review results from being lost during long-running jobs.
- Improved auto-review fallback flow: when the retry limit is reached, the workflow can accept the highest-scoring result and continue merging, marking the scene as accepted over retry.
- Improved thread-pool isolation by separating interactive requests from generation tasks, reducing upload and state-restore starvation during multi-window concurrent generation.

### Fixed

- Fixed auto mode not advancing after image upload because draft project ID initialization happened before the auto-start decision.
- Fixed merge-blocking logic for regenerated scenes so final merge does not start while any regenerated scene is unfinished or has not passed review.
- Fixed incomplete progress-bar and completed-step recovery after page refresh.
- Fixed frontend crashes when project cleanup receives a non-JSON 504 response from upstream infrastructure.
- Fixed tofu-box text corruption in cloud-generated comic PDFs for Chinese, Japanese, Korean, and emoji text. PDF generation now requires a CJK-capable font, and the runtime image must include `fonts-noto-cjk`, `fonts-noto-color-emoji`, `fontconfig`, and `ffmpeg` / `ffprobe`.

### Documentation

- Updated bilingual README files to document `SeeDance-2.5`, dual video generation modes, comic PDF export, cloud runtime font/media dependencies, credential governance, and deployment requirements.
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
