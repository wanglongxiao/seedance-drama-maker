# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import asyncio

import pytest

import app.main as main_module
from app.agents.merge_agent import MergeAgent
from app.agents.video_agent import VideoAgent
from app.models.schemas import GeneratedImage


class TestVideoAgentAssetReferences:
    def setup_method(self):
        self.agent = VideoAgent()

    def test_character_reference_uses_asset_uri_for_generation(self):
        references = [
            GeneratedImage(
                scene_number=0,
                url="https://example.com/character.png",
                prompt="character",
                name="hero",
                reference_type="character",
                asset_id="asset-123",
            ),
            GeneratedImage(
                scene_number=1,
                url="https://example.com/scene.png",
                prompt="scene",
                name="street",
                reference_type="scene",
            ),
        ]

        urls = self.agent._dedupe_reference_urls(references, None)

        assert urls == [
            "asset://asset-123",
            "https://example.com/scene.png",
        ]

    def test_primary_reference_source_url_keeps_original_url(self):
        references = [
            GeneratedImage(
                scene_number=0,
                url="https://example.com/character.png",
                prompt="character",
                name="hero",
                reference_type="character",
                asset_id="asset-123",
            )
        ]

        source_url = self.agent._primary_reference_source_url(references, None)

        assert source_url == "https://example.com/character.png"


def test_end_project_resources_cleans_draft_uploads_without_project(monkeypatch):
    cleanup_calls = []

    monkeypatch.setattr(main_module.main_agent, "get_project", lambda project_id: None)
    monkeypatch.setattr(
        main_module.tos_service,
        "cleanup_project_directory",
        lambda project_id, keep_prefixes=None: cleanup_calls.append(("tos", project_id, keep_prefixes)),
    )
    monkeypatch.setattr(
        main_module,
        "cleanup_project_temp_dir",
        lambda project_id: cleanup_calls.append(("temp", project_id)),
    )

    main_module.project_client_owners["draft123"] = "client-x"

    result = asyncio.run(
        main_module.end_project_resources(
            "draft123",
            reason="pagehide",
            client_id="client-x",
        )
    )

    assert result["status"] == "missing"
    assert ("tos", "draft123", []) in cleanup_calls
    assert ("temp", "draft123") in cleanup_calls
    assert "draft123" not in main_module.project_client_owners


def test_merge_agent_copies_remote_single_scene_video_to_final_directory(monkeypatch):
    merge_agent = MergeAgent()
    calls = []

    monkeypatch.setattr(
        "app.agents.merge_agent.tos_service.copy_url_to_tos",
        lambda source_url, target_filename, project_id=None, category=None: calls.append(
            (source_url, target_filename, project_id, category)
        ) or "https://example.com/final.mov",
    )

    result = merge_agent._upload_or_copy_merged_output(
        source="https://example.com/scenes/scene_01.mov",
        output_filename="final_video_demo.mov",
        project_id="demo123",
    )

    assert result == "https://example.com/final.mov"
    assert calls == [
        (
            "https://example.com/scenes/scene_01.mov",
            "final_video_demo.mov",
            "demo123",
            "videos/final",
        )
    ]
