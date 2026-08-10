# Changelog

This document records the notable version changes of the project.

Notes:

- The early history of this repository is not fully traceable.
- `1.0.0` and `1.1.0` are summarized from the current codebase, documentation, and the repository cleanup performed in this round.
- Future entries should be appended in reverse chronological order.

## [1.1.0] - 2026-05-09

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

## [1.0.0] - 2026-03-03

### Initial Release

- Established the multi-agent video generation pipeline covering script generation, reference image generation, scene video generation, review, and merge.
- Integrated the core BytePlus capabilities: `TOS`, `Seed-Speech`, `Seed-2.0`, `SeeDream-5.0`, and `SeeDance-2.0`.
- Added the FastAPI + WebSocket service foundation and basic project state management.
- Added a multilingual frontend, realtime status updates, and the base interaction workflow.
