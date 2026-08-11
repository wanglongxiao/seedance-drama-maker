# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import asyncio
from pathlib import Path

import pytest

import app.main as main_module
from app.agents.image_agent import ImageAgent
from app.agents.main_agent import MainAgent
from app.agents.merge_agent import MergeAgent
from app.agents.script_agent import ScriptAgent
from app.agents.video_agent import VideoAgent
from app.config import config
from app.models.schemas import Character, GeneratedImage, GeneratedVideo, Scene, Script, VideoProject
from app.services.asset_library_service import AssetLibraryService
from app.services.ffmpeg_service import ffmpeg_service


class TestVideoAgentAssetReferences:
    def setup_method(self):
        self.agent = VideoAgent()

    def test_all_generation_references_use_asset_uris(self):
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
                asset_id="asset-456",
            ),
            GeneratedImage(
                scene_number=1,
                url="https://example.com/storyboard.png",
                prompt="storyboard",
                name="scene 01 storyboard",
                reference_type="storyboard",
                asset_id="asset-789",
            ),
        ]

        urls = self.agent._dedupe_reference_urls(references, None)

        assert urls == [
            "asset://asset-123",
            "asset://asset-456",
            "asset://asset-789",
        ]

    def test_reference_without_asset_id_is_rejected(self):
        references = [
            GeneratedImage(
                scene_number=1,
                url="https://example.com/scene.png",
                prompt="scene",
                name="street",
                reference_type="scene",
            )
        ]

        with pytest.raises(ValueError, match="missing asset_id"):
            self.agent._dedupe_reference_urls(references, None)

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

    def test_prompt_mentions_storyboard_and_structured_scene_metadata(self):
        script = build_demo_script()
        scene = Scene(
            scene_number=1,
            scene_name="街道",
            description="主角在雨夜街道奔跑",
            dialogue="快走",
            duration=10,
            character_description="主角回头张望后继续奔跑",
            voice_description="急促",
            mood="紧张",
            time_of_day="夜晚",
            weather="雨天",
            camera_angle="跟拍",
            characters_present=["Hero"],
        )
        prompt = self.agent._build_video_prompt(
            scene=scene,
            characters=script.characters,
            scene_definitions=script.scene_definitions,
            reference_images=[
                GeneratedImage(scene_number=0, url="u1", prompt="p1", name="Hero", reference_type="character", asset_id="asset-char"),
                GeneratedImage(scene_number=0, url="u2", prompt="p2", name="街道", reference_type="scene", asset_id="asset-scene"),
                GeneratedImage(scene_number=1, url="u3", prompt="p3", name="分镜板", reference_type="storyboard", asset_id="asset-storyboard"),
            ],
            duration=10,
        )

        assert "严格参考<图片3>中的6宫格白描 storyboard" in prompt
        assert "从参考图片顺序：" in prompt
        assert "时间信息：夜晚" in prompt
        assert "天气信息：雨天" in prompt
        assert "稳定场景特征：" in prompt
        assert "角色设定：Hero" in prompt

    def test_prompt_groups_outfit_and_state_images_and_extend_video_hint(self):
        script = build_demo_script()
        scene = Scene(
            scene_number=2,
            scene_name="街道",
            description="主角换装后继续奔跑",
            dialogue="快走",
            duration=10,
            character_description="主角披上雨衣继续奔跑",
            voice_description="急促",
            mood="紧张",
            time_of_day="夜晚",
            weather="雨天",
            camera_angle="跟拍",
            characters_present=["Hero"],
        )
        prompt = self.agent._build_video_prompt(
            scene=scene,
            characters=script.characters,
            scene_definitions=script.scene_definitions,
            reference_images=[
                GeneratedImage(scene_number=0, url="u1", prompt="p1", name="Hero-雨衣", reference_type="character_outfit", asset_id="asset-outfit"),
                GeneratedImage(scene_number=0, url="u2", prompt="p2", name="街道-夜雨", reference_type="scene_state", asset_id="asset-state"),
                GeneratedImage(scene_number=2, url="u3", prompt="p3", name="分镜板", reference_type="storyboard", asset_id="asset-storyboard"),
            ],
            duration=10,
            scene_index=1,
            total_scenes=3,
            previous_video_url="https://example.com/prev.mp4",
        )

        # 装扮图归入角色组，状态图归入场景组
        assert "人物/角色参考图中的出场角色设定与布景设定" in prompt
        assert "人物/角色装扮" in prompt
        assert "场景状态" in prompt
        # 延长模式：加入参考前一分镜生成视频的说明
        assert "参考上一分镜的生成视频" in prompt

    def test_create_video_task_appends_previous_video_as_reference(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "app.agents.video_agent.llm_service.create_video_task_with_content",
            lambda model, content, duration, resolution, aspect_ratio: captured.update(
                {"content": content}
            ) or "task-123",
        )

        task_id = self.agent._create_video_task_with_references(
            prompt="生成分镜视频",
            reference_image_urls=["asset://asset-char", "asset://asset-storyboard"],
            duration=10,
            resolution="720p",
            aspect_ratio="16:9",
            previous_video_url="https://example.com/prev.mp4",
        )

        assert task_id == "task-123"
        video_items = [item for item in captured["content"] if item.get("type") == "video_url"]
        assert len(video_items) == 1
        assert video_items[0]["role"] == "reference_video"
        assert video_items[0]["video_url"]["url"] == "https://example.com/prev.mp4"

    def test_create_video_task_omits_video_when_no_previous(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "app.agents.video_agent.llm_service.create_video_task_with_content",
            lambda model, content, duration, resolution, aspect_ratio: captured.update(
                {"content": content}
            ) or "task-456",
        )

        self.agent._create_video_task_with_references(
            prompt="并行模式分镜视频",
            reference_image_urls=["asset://asset-char"],
            duration=10,
            resolution="720p",
            aspect_ratio="16:9",
            previous_video_url=None,
        )

        assert not [item for item in captured["content"] if item.get("type") == "video_url"]


class TestImageAgentSceneAssets:
    def setup_method(self):
        self.agent = ImageAgent()

    def test_generate_scene_storyboard_includes_structured_scene_context(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "app.agents.image_agent.llm_service.generate_image",
            lambda prompt, model, size, image_urls=None, ratio=None: captured.update(
                {"prompt": prompt, "model": model, "size": size, "image_urls": image_urls, "ratio": ratio}
            ) or {"data": [{"url": "https://example.com/storyboard.png"}]},
        )

        script = build_demo_script()
        scene = script.scenes[0]
        image = self.agent.generate_scene_storyboard_image(
            scene=scene,
            script=script,
            reference_images=[
                GeneratedImage(scene_number=0, url="https://example.com/hero.png", prompt="p", name="Hero", reference_type="character"),
                GeneratedImage(scene_number=0, url="https://example.com/street.png", prompt="p", name="街道", reference_type="scene"),
            ],
            user_style_info="写实电影风格",
            aspect_ratio="9:16",
        )

        assert image.reference_type == "storyboard"
        assert image.scene_number == 1
        assert captured["ratio"] == "9:16"
        assert captured["image_urls"] == [
            "https://example.com/hero.png",
            "https://example.com/street.png",
        ]
        assert "[STORYBOARD SHEET]" in captured["prompt"]
        assert "[REFERENCE ASSETS]" in captured["prompt"]
        assert "[SCENE CHARACTER DEFINITIONS]" in captured["prompt"]
        assert "Time of day: 夜晚" in captured["prompt"]
        assert "Weather: 雨天" in captured["prompt"]
        assert "Scene features: 雨夜路灯, 湿漉漉柏油路, 霓虹反光" in captured["prompt"]
        # 回归：故事版禁止注入用户的彩色/写实风格，且必须显式要求按白描线稿重绘参考图
        assert "[REFERENCE STYLE REQUIREMENTS]" not in captured["prompt"]
        assert "写实电影风格" not in captured["prompt"]
        assert "content/identity references only" in captured["prompt"]

    def test_generate_scene_storyboard_uses_six_panel_prompt(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "app.agents.image_agent.llm_service.generate_image",
            lambda prompt, model, size, image_urls=None, ratio=None: captured.update(
                {"prompt": prompt, "image_urls": image_urls, "ratio": ratio}
            ) or {"data": [{"url": "https://example.com/storyboard.png"}]},
        )

        script = build_demo_script()
        scene = script.scenes[0]
        image = self.agent.generate_scene_storyboard_image(
            scene=scene,
            script=script,
            reference_images=[
                GeneratedImage(scene_number=0, url="https://example.com/hero.png", prompt="p", name="Hero", reference_type="character"),
                GeneratedImage(scene_number=0, url="https://example.com/street.png", prompt="p", name="街道", reference_type="scene"),
            ],
        )

        assert image.reference_type == "storyboard"
        assert image.scene_number == 1
        assert "[STORYBOARD SHEET]" in captured["prompt"]
        assert "Layout: 2 columns x 3 rows" in captured["prompt"]
        assert "black-and-white line drawing" in captured["prompt"]
        assert captured["image_urls"] == [
            "https://example.com/hero.png",
            "https://example.com/street.png",
        ]

    def test_storyboard_prompt_includes_cast_constraints(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "app.agents.image_agent.llm_service.generate_image",
            lambda prompt, model, size, image_urls=None, ratio=None: captured.update(
                {"prompt": prompt}
            ) or {"data": [{"url": "https://example.com/storyboard.png"}]},
        )

        script = build_demo_script()
        scene = script.scenes[0]
        self.agent.generate_scene_storyboard_image(scene=scene, script=script)

        prompt = captured["prompt"]
        # 数量约束：本分镜恰好 1 个角色
        assert "[CAST CONSTRAINTS]" in prompt
        assert "EXACTLY 1 distinct main character" in prompt
        # 性别信息透出（Hero 为 female）
        assert "gender=female" in prompt
        # 防重复与性别约束
        assert "SAME character must NEVER appear more than once" in prompt
        assert "Strictly respect each character's gender" in prompt


class TestStoryboardReviewAgent:
    def setup_method(self):
        from app.agents.storyboard_review_agent import StoryboardReviewAgent
        self.agent = StoryboardReviewAgent()

    def _patch_response(self, monkeypatch, content: str):
        captured = {}

        def fake_chat(model, messages, temperature=0.3, max_tokens=1000, **kwargs):
            captured["messages"] = messages
            return {"choices": [{"message": {"content": content}}]}

        monkeypatch.setattr(
            "app.agents.storyboard_review_agent.llm_service.chat_completion", fake_chat
        )
        return captured

    def test_review_passes_when_all_checks_true(self, monkeypatch):
        captured = self._patch_response(
            monkeypatch,
            '{"approved": true, "feedback": "ok", "checks": '
            '{"is_line_art_six_panel": true, "no_duplicate_character": true, "gender_correct": true}}',
        )
        approved, feedback = self.agent.review_storyboard(
            image_url="https://example.com/sb.png",
            scene_description="两人对峙",
            characters_present=["Hero", "Villain"],
            character_gender_map={"Hero": "female", "Villain": "male"},
        )
        assert approved is True
        # 审核请求必须携带图片
        content = captured["messages"][0]["content"]
        assert any(part.get("type") == "image_url" for part in content)

    def test_review_fails_when_any_check_false(self, monkeypatch):
        self._patch_response(
            monkeypatch,
            '{"approved": true, "feedback": "同一角色重复", "checks": '
            '{"is_line_art_six_panel": true, "no_duplicate_character": false, "gender_correct": true}}',
        )
        approved, feedback = self.agent.review_storyboard(
            image_url="https://example.com/sb.png",
            characters_present=["Hero"],
        )
        # checks 有一项 false，即使模型 approved=true 也应判为不通过
        assert approved is False

    def test_review_fails_on_unparsable_output(self, monkeypatch):
        self._patch_response(monkeypatch, "这不是JSON")
        approved, feedback = self.agent.review_storyboard(image_url="https://example.com/sb.png")
        assert approved is False

    def test_review_prompt_includes_limb_count_check(self, monkeypatch):
        captured = self._patch_response(
            monkeypatch,
            '{"approved": true, "feedback": "ok", "checks": '
            '{"is_line_art_six_panel": true, "no_duplicate_character": true, "gender_correct": true}}',
        )
        self.agent.review_storyboard(
            image_url="https://example.com/sb.png",
            characters_present=["Hero"],
        )
        prompt_text = "".join(
            part.get("text", "")
            for part in captured["messages"][0]["content"]
            if part.get("type") == "text"
        )
        # 条件2 更新：新增“任一角色不得多于2只手臂或多于2条腿”的肢体校验
        assert "2 只手臂" in prompt_text
        assert "2 条腿" in prompt_text

    def test_review_fails_when_limbs_abnormal(self, monkeypatch):
        # 角色出现多于2只手臂时，no_duplicate_character 应判 false -> 不通过
        self._patch_response(
            monkeypatch,
            '{"approved": true, "feedback": "角色出现三只手臂", "checks": '
            '{"is_line_art_six_panel": true, "no_duplicate_character": false, "gender_correct": true}}',
        )
        approved, _ = self.agent.review_storyboard(
            image_url="https://example.com/sb.png",
            characters_present=["Hero"],
        )
        assert approved is False


class TestStoryboardReviewRetry:
    def test_regenerates_until_review_passes(self, monkeypatch):
        agent = MainAgent()
        project = VideoProject(
            project_id="sbretry",
            user_input="x",
            script=build_demo_script(),
        )

        gen_calls = {"n": 0}

        def fake_generate(scene, script, reference_images=None, user_style_info=None, aspect_ratio=None):
            gen_calls["n"] += 1
            return GeneratedImage(
                scene_number=1,
                url=f"https://example.com/sb_{gen_calls['n']}.png",
                prompt="p",
                name="Scene 01 Storyboard",
                reference_type="storyboard",
            )

        review_calls = {"n": 0}

        def fake_review(image_url, scene_description="", characters_present=None,
                        character_gender_map=None, output_language="zh-CN"):
            review_calls["n"] += 1
            # 第一次不通过，第二次通过
            return (review_calls["n"] >= 2, "fb")

        monkeypatch.setattr(agent.image_agent, "generate_scene_storyboard_image", fake_generate)
        monkeypatch.setattr(agent.storyboard_review_agent, "review_storyboard", fake_review)

        scene = project.script.scenes[0]
        result = asyncio.run(
            agent._generate_storyboard_with_review(
                project=project,
                scene=scene,
                reference_images=[],
                user_style_info=None,
                aspect_ratio=None,
                scene_number=1,
            )
        )
        # 第一次审核不通过 -> 重新生成一次，第二次通过
        assert gen_calls["n"] == 2
        assert review_calls["n"] == 2
        assert result.url == "https://example.com/sb_2.png"

    def test_stops_at_max_attempts_when_never_approved(self, monkeypatch):
        agent = MainAgent()
        project = VideoProject(project_id="sbmax", user_input="x", script=build_demo_script())

        gen_calls = {"n": 0}

        def fake_generate(scene, script, reference_images=None, user_style_info=None, aspect_ratio=None):
            gen_calls["n"] += 1
            return GeneratedImage(scene_number=1, url=f"u{gen_calls['n']}", prompt="p",
                                  name="sb", reference_type="storyboard")

        monkeypatch.setattr(agent.image_agent, "generate_scene_storyboard_image", fake_generate)
        monkeypatch.setattr(
            agent.storyboard_review_agent, "review_storyboard",
            lambda **kwargs: (False, "always fail"),
        )
        monkeypatch.setattr(
            "app.agents.main_agent.config.get",
            _config_get_override({"storyboard_review.max_retries": 2}),
        )

        scene = project.script.scenes[0]
        result = asyncio.run(
            agent._generate_storyboard_with_review(
                project=project, scene=scene, reference_images=[],
                user_style_info=None, aspect_ratio=None, scene_number=1,
            )
        )
        # 总生成次数 = 首次 + max_retries(2) = 3
        assert gen_calls["n"] == 3
        assert result is not None


def _config_get_override(overrides):
    from app.config import config as _cfg
    original = _cfg.get

    def _get(key, default=None):
        if key in overrides:
            return overrides[key]
        return original(key, default)

    return _get


def build_demo_script() -> Script:
    return Script(
        title="demo",
        style="cinematic",
        background="background",
        characters=[
            Character(
                name="Hero",
                age="18",
                gender="female",
                face_features="sharp",
                skin_tone="fair",
                clothing="coat",
                voice_type="warm",
                voice_features="clear",
                voice_style="calm",
            )
        ],
        scene_definitions=[
            {
                "name": "街道",
                "description": "深夜城市街道，雨水打湿柏油路，路灯与霓虹交错反光",
                "time_of_day": "夜晚",
                "weather": "雨天",
                "scene_features": ["雨夜路灯", "湿漉漉柏油路", "霓虹反光"],
            }
        ],
        scenes=[
            Scene(
                scene_number=1,
                scene_name="街道",
                description="主角在雨夜街道奔跑",
                dialogue="快走",
                duration=10,
                character_description="主角回头张望后继续奔跑",
                voice_description="急促",
                mood="紧张",
                time_of_day="夜晚",
                weather="雨天",
                camera_angle="跟拍",
                characters_present=["Hero"],
            )
        ],
        total_duration=10,
    )


def test_reference_output_includes_scene_level_assets_and_mappings():
    agent = MainAgent()
    project = VideoProject(
        project_id="demo123",
        user_input="生成短片",
        script=build_demo_script(),
        character_reference_images=[
            GeneratedImage(scene_number=0, url="https://example.com/hero.png", prompt="p", name="Hero", reference_type="character", asset_id="asset-char"),
        ],
        scene_reference_images=[
            GeneratedImage(scene_number=0, url="https://example.com/street.png", prompt="p", name="街道", reference_type="scene", asset_id="asset-scene"),
        ],
        storyboard_images=[
            GeneratedImage(scene_number=1, url="https://example.com/storyboard.png", prompt="p", name="Scene 01 Storyboard", reference_type="storyboard", asset_id="asset-storyboard"),
        ],
    )

    output = agent._build_reference_output(project)

    assert output["count"] == 3
    assert len(output["storyboard_images"]) == 1
    mapping = output["scene_reference_mappings"][1]
    assert mapping["character_assets"][0]["asset_id"] == "asset-char"
    assert mapping["scene_assets"][0]["asset_id"] == "asset-scene"
    assert mapping["storyboard"]["asset_id"] == "asset-storyboard"
    assert mapping["time_of_day"] == "夜晚"
    assert mapping["weather"] == "雨天"
    assert mapping["scene_features"] == ["雨夜路灯", "湿漉漉柏油路", "霓虹反光"]


def test_regenerate_storyboard_asset_replaces_scene_entry(monkeypatch):
    agent = MainAgent()
    project = VideoProject(
        project_id="demo-story",
        user_input="生成短片",
        script=build_demo_script(),
        character_reference_images=[
            GeneratedImage(scene_number=0, url="https://example.com/hero.png", prompt="p", name="Hero", reference_type="character", asset_id="asset-char"),
        ],
        scene_reference_images=[
            GeneratedImage(scene_number=0, url="https://example.com/street.png", prompt="p", name="街道", reference_type="scene", asset_id="asset-scene"),
        ],
        storyboard_images=[
            GeneratedImage(scene_number=1, url="https://example.com/old_storyboard.png", prompt="p", name="Scene 01 Storyboard", reference_type="storyboard", asset_id="asset-old"),
        ],
    )
    agent.projects[project.project_id] = project

    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return GeneratedImage(
            scene_number=1,
            url="https://example.com/new_storyboard.png",
            prompt="p",
            name="Scene 01 Storyboard",
            reference_type="storyboard",
        )

    monkeypatch.setattr(agent.image_agent, "generate_scene_storyboard_image", fake_generate)

    async def fake_store(project, image, category, asset_name, index, used_original=False):
        image.reference_type = category
        image.name = asset_name
        image.asset_id = "asset-new"
        return image

    monkeypatch.setattr(agent, "_store_reference_asset_async", fake_store)

    result = asyncio.run(agent.regenerate_storyboard_asset(project, scene_number=1))

    assert result.url == "https://example.com/new_storyboard.png"
    assert result.asset_id == "asset-new"
    # 故事版列表中该分镜条目被替换，数量不变
    assert len(project.storyboard_images) == 1
    assert project.storyboard_images[0].url == "https://example.com/new_storyboard.png"
    # 生成时参考了该分镜对应的角色图与场景图
    reference_urls = {img.url for img in captured.get("reference_images", [])}
    assert "https://example.com/hero.png" in reference_urls
    assert "https://example.com/street.png" in reference_urls


def build_variant_demo_script() -> Script:
    """含角色特殊装扮 + 场景不同时间/天气状态的分镜脚本，用于变体参考图测试。"""
    base = build_demo_script()
    # 分镜2：Hero 换成舞会盛装，场景切换为清晨晴天（与场景默认夜晚/雨天不同）。
    base.scenes.append(
        Scene(
            scene_number=2,
            scene_name="街道",
            description="清晨街道，主角身穿舞会盛装现身",
            dialogue="终于天亮了",
            duration=10,
            character_description="主角整理裙摆",
            voice_description="轻松",
            mood="舒缓",
            time_of_day="清晨",
            weather="晴天",
            camera_angle="平拍",
            characters_present=["Hero"],
            character_outfits={"Hero": "舞会盛装"},
        )
    )
    base.total_duration = 20
    return base


def test_plan_scene_variant_assets_dedup_and_trigger():
    agent = MainAgent()
    project = VideoProject(
        project_id="demo-variant",
        user_input="生成短片",
        script=build_variant_demo_script(),
    )

    plan = agent._plan_scene_variant_assets(project)

    # 装扮不同于默认 coat -> 生成一张装扮图
    assert len(plan["outfits"]) == 1
    outfit_task = plan["outfits"][0]
    assert outfit_task["character_key"] == agent._normalize_name_key("Hero")
    assert outfit_task["outfit"] == "舞会盛装"
    assert "::" in outfit_task["dedup_key"]

    # 场景时间/天气不同于默认 夜晚/雨天 -> 生成一张场景状态图
    assert len(plan["scene_states"]) == 1
    scene_task = plan["scene_states"][0]
    assert scene_task["scene_key"] == agent._normalize_name_key("街道")
    assert scene_task["time_of_day"] == "清晨"
    assert scene_task["weather"] == "晴天"


def test_plan_scene_variant_assets_skips_default_outfit_and_state():
    agent = MainAgent()
    # 仅有默认装扮/默认时间天气的分镜，不应生成任何变体图。
    project = VideoProject(
        project_id="demo-variant-default",
        user_input="生成短片",
        script=build_demo_script(),
    )
    project.script.scenes[0].character_outfits = {"Hero": "coat"}  # 与默认 clothing 相同

    plan = agent._plan_scene_variant_assets(project)

    assert plan["outfits"] == []
    assert plan["scene_states"] == []


def test_expected_counts_include_variant_assets():
    agent = MainAgent()
    project = VideoProject(
        project_id="demo-variant-count",
        user_input="生成短片",
        script=build_variant_demo_script(),
    )

    counts = agent._expected_reference_counts(project)

    assert counts["character_outfits"] == 1
    assert counts["scene_states"] == 1
    # total = characters(1) + scenes(1) + outfits(1) + scene_states(1) + storyboards(2)
    assert counts["total"] == counts["characters"] + counts["scenes"] + 1 + 1 + counts["storyboards"]


def test_build_reference_output_includes_variant_libraries():
    agent = MainAgent()
    project = VideoProject(
        project_id="demo-variant-output",
        user_input="生成短片",
        script=build_variant_demo_script(),
        character_outfit_images=[
            GeneratedImage(
                scene_number=0,
                url="https://example.com/outfit.png",
                prompt="p",
                name="Hero - 舞会盛装",
                reference_type="character_outfit",
                variant_key="hero::舞会盛装",
                asset_id="asset-outfit",
            ),
        ],
        scene_state_images=[
            GeneratedImage(
                scene_number=0,
                url="https://example.com/state.png",
                prompt="p",
                name="街道 - 清晨 晴天",
                reference_type="scene_state",
                variant_key="街道::清晨::晴天",
                asset_id="asset-state",
            ),
        ],
    )

    output = agent._build_reference_output(project)

    assert len(output["character_outfit_images"]) == 1
    assert len(output["scene_state_images"]) == 1
    assert output["character_outfit_images"][0]["variant_key"] == "hero::舞会盛装"
    assert output["scene_state_images"][0]["variant_key"] == "街道::清晨::晴天"
    assert output["library"]["character_outfits"][0]["url"] == "https://example.com/outfit.png"
    assert output["library"]["scene_states"][0]["url"] == "https://example.com/state.png"


def test_regenerate_variant_asset_replaces_outfit_entry(monkeypatch):
    agent = MainAgent()
    project = VideoProject(
        project_id="demo-variant-regen",
        user_input="生成短片",
        script=build_variant_demo_script(),
        character_reference_images=[
            GeneratedImage(scene_number=0, url="https://example.com/hero.png", prompt="p", name="Hero", reference_type="character", asset_id="asset-char"),
        ],
        scene_reference_images=[
            GeneratedImage(scene_number=0, url="https://example.com/street.png", prompt="p", name="街道", reference_type="scene", asset_id="asset-scene"),
        ],
        character_outfit_images=[
            GeneratedImage(
                scene_number=0,
                url="https://example.com/old_outfit.png",
                prompt="p",
                name="Hero - 舞会盛装",
                reference_type="character_outfit",
                variant_key=f"{MainAgent()._normalize_name_key('Hero')}::{MainAgent()._normalize_name_key('舞会盛装')}",
                asset_id="asset-old",
            ),
        ],
    )
    agent.projects[project.project_id] = project
    variant_key = project.character_outfit_images[0].variant_key

    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return GeneratedImage(
            scene_number=0,
            url="https://example.com/new_outfit.png",
            prompt="p",
            name="Hero - 舞会盛装",
            reference_type="character_outfit",
        )

    monkeypatch.setattr(agent.image_agent, "generate_character_outfit_image", fake_generate)

    async def fake_store(project, image, category, asset_name, index, used_original=False):
        image.reference_type = category
        image.name = asset_name
        image.asset_id = "asset-new"
        return image

    monkeypatch.setattr(agent, "_store_reference_asset_async", fake_store)

    result = asyncio.run(
        agent.regenerate_variant_asset(project, reference_type="character_outfit", variant_key=variant_key)
    )

    assert result.url == "https://example.com/new_outfit.png"
    assert result.variant_key == variant_key
    assert len(project.character_outfit_images) == 1
    assert project.character_outfit_images[0].url == "https://example.com/new_outfit.png"
    # 生成时以角色主图作为参考底图
    assert captured.get("base_reference_image").url == "https://example.com/hero.png"
    assert captured.get("outfit") == "舞会盛装"


def test_regenerate_video_forwards_asset_group_metadata(monkeypatch):
    agent = MainAgent()
    project = VideoProject(
        project_id="demo123",
        user_input="生成短片",
        script=build_demo_script(),
        videos=[],
        reference_image=GeneratedImage(
            scene_number=0,
            url="https://example.com/hero.png",
            prompt="p",
            name="Hero",
            reference_type="character",
            asset_id="asset-char",
        ),
        character_reference_images=[
            GeneratedImage(scene_number=0, url="https://example.com/hero.png", prompt="p", name="Hero", reference_type="character", asset_id="asset-char"),
        ],
        scene_reference_images=[
            GeneratedImage(scene_number=0, url="https://example.com/street.png", prompt="p", name="街道", reference_type="scene", asset_id="asset-scene"),
        ],
        storyboard_images=[
            GeneratedImage(scene_number=1, url="https://example.com/storyboard.png", prompt="p", name="Scene 01 Storyboard", reference_type="storyboard", asset_id="asset-storyboard"),
        ],
        asset_group_id="group-123",
        asset_project_name="asset-project",
    )

    captured = {}
    monkeypatch.setattr(agent, "_get_previous_video_url", lambda project, scene_number: None)
    monkeypatch.setattr(
        agent.video_agent,
        "regenerate_video",
        lambda **kwargs: captured.update(kwargs) or GeneratedVideo(
            scene_number=1,
            url="https://example.com/scene_01.mov",
            first_frame_url="https://example.com/hero.png",
            duration=10,
            prompt="updated prompt",
        ),
    )

    asyncio.run(agent._regenerate_video(project, 1, "动作更强"))

    assert captured["asset_group_id"] == "group-123"
    assert captured["asset_project_name"] == "asset-project"
    assert [image.reference_type for image in captured["reference_images"]] == [
        "character",
        "scene",
        "storyboard",
    ]
    assert captured["characters"][0].gender == "female"
    assert captured["scene_definitions"][0].scene_features == ["雨夜路灯", "湿漉漉柏油路", "霓虹反光"]


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


def test_asset_group_description_uses_project_id_only():
    agent = MainAgent()
    project = VideoProject(
        project_id="project-safe-123",
        user_input="这里可能包含敏感文本，不应该写进 CreateAssetGroup Description",
    )

    assert agent._build_asset_group_description(project) == "project-safe-123"


def test_create_asset_group_omits_empty_description(monkeypatch):
    service = AssetLibraryService()
    calls = []

    monkeypatch.setattr(
        service,
        "_call",
        lambda action, payload: calls.append((action, payload)) or {"Id": "group-demo"},
    )

    result = service.create_asset_group(
        name="seedance-project-project-safe-123",
        description="",
        project_name="default",
    )

    assert result == "group-demo"
    assert calls == [
        (
            "CreateAssetGroup",
            {
                "Name": "seedance-project-project-safe-123",
                "GroupType": service.group_type,
                "ProjectName": "default",
            },
        )
    ]


def test_frontend_config_returns_updated_reference_limits():
    result = asyncio.run(main_module.get_frontend_config())

    assert result["success"] is True
    assert result["config"]["reference_image_max_count"] == 40
    assert result["config"]["character_reference_max_count"] == 20
    assert result["config"]["scene_reference_max_count"] == 20


def test_frontend_config_returns_default_video_generation_mode():
    result = asyncio.run(main_module.get_frontend_config())

    assert result["success"] is True
    assert result["config"]["default_video_generation_mode"] in ("parallel", "extend")


def test_normalize_generation_mode_handles_aliases_and_default():
    agent = MainAgent()

    assert agent._normalize_generation_mode("extend") == "extend"
    assert agent._normalize_generation_mode("延长") == "extend"
    assert agent._normalize_generation_mode("serial") == "extend"
    assert agent._normalize_generation_mode("parallel") == "parallel"
    assert agent._normalize_generation_mode("并行") == "parallel"
    # 未知/空值回落到配置默认（默认 parallel）
    assert agent._normalize_generation_mode("") == "parallel"
    assert agent._normalize_generation_mode(None) == "parallel"
    assert agent._normalize_generation_mode("unknown") == "parallel"


def test_set_project_video_generation_mode_persists_normalized_value():
    agent = MainAgent()
    project = VideoProject(project_id="mode-proj", user_input="hi")
    agent.projects[project.project_id] = project

    agent.set_project_video_generation_mode(project.project_id, "延长")
    assert project.video_generation_mode == "extend"

    agent.set_project_video_generation_mode(project.project_id, "并行")
    assert project.video_generation_mode == "parallel"

    # None 不覆盖既有值
    agent.set_project_video_generation_mode(project.project_id, None)
    assert project.video_generation_mode == "parallel"


def test_script_agent_prompt_reflects_updated_numeric_limits():
    agent = ScriptAgent()

    system_prompt = agent._get_system_prompt("zh-CN", 60)
    user_prompt = agent._build_prompt("请生成一个短剧", total_duration=60)

    assert agent.default_total_duration == 60
    assert agent.total_duration_min == 30
    assert agent.total_duration_max == 1200
    assert agent.scene_duration_min == 10
    assert agent.scene_duration_max == 30
    assert "10-30" in system_prompt
    assert "31、32" in system_prompt
    assert "不得超过1200秒" in user_prompt
    assert "16、17" not in system_prompt
    assert "16、17" not in user_prompt
    assert "scene_features" in system_prompt
    assert "time_of_day" in system_prompt
    assert "weather" in user_prompt


def test_script_prompt_requires_outfit_in_action_and_state_in_description():
    agent = ScriptAgent()

    system_prompt = agent._get_system_prompt("zh-CN", 60)
    user_prompt = agent._build_prompt("请生成一个短剧", total_duration=60)

    # 角色装扮信息必须写入 character_description（角色动作部分）
    assert "character_description（角色动作部分）" in system_prompt
    # 场景状态（时间/天气）信息必须写入 description（场景描述部分）
    assert "description（场景描述部分）" in system_prompt
    # 结构化字段仍需保留，供参考图触发逻辑使用
    assert "character_outfits" in system_prompt
    # 用户提示词侧也强化了同样的约束
    assert "角色动作部分" in user_prompt
    assert "场景描述部分" in user_prompt


def test_script_agent_parse_persists_scene_conditions_and_scene_features():
    agent = ScriptAgent()

    parsed = agent._parse_script(
        """
        {
          "title": "demo",
          "style": "cinematic",
          "background": "雨夜追逐",
          "characters": [
            {
              "name": "Hero",
              "age": "18",
              "gender": "female",
              "face_features": "sharp",
              "skin_tone": "fair",
              "clothing": "coat",
              "voice_type": "warm",
              "voice_features": "clear",
              "voice_style": "calm"
            }
          ],
          "scene_definitions": [
            {
              "name": "街道",
              "description": "深夜城市街道，雨水打湿柏油路",
              "scene_features": ["雨夜路灯", "湿漉漉柏油路"]
            }
          ],
          "scenes": [
            {
              "scene_number": 1,
              "scene_name": "街道",
              "description": "主角在雨夜街道奔跑",
              "dialogue": "快走",
              "duration": 10,
              "character_description": "回头张望后继续奔跑",
              "voice_description": "急促",
              "mood": "紧张",
              "characters_present": ["Hero"]
            }
          ]
        }
        """
    )

    assert parsed["scene_definitions"][0]["time_of_day"] == "夜晚"
    assert parsed["scene_definitions"][0]["weather"] == "雨天"
    assert parsed["scene_definitions"][0]["scene_features"] == ["雨夜路灯", "湿漉漉柏油路"]
    assert parsed["scenes"][0]["time_of_day"] == "夜晚"
    assert parsed["scenes"][0]["weather"] == "雨天"


def test_script_parse_coerces_dict_character_and_voice_description():
    """模型把 character_description / voice_description 输出为按角色分组的 dict 时，
    应被拍平成字符串，避免 Scene 校验因类型不符失败。"""
    agent = ScriptAgent()

    parsed = agent._parse_script(
        """
        {
          "title": "demo",
          "style": "cinematic",
          "background": "雨夜",
          "characters": [
            {"name": "小悠", "age": "20", "gender": "女", "face_features": "f",
             "skin_tone": "白", "clothing": "透视装", "voice_type": "v",
             "voice_features": "vf", "voice_style": "vs"}
          ],
          "scene_definitions": [
            {"name": "客厅", "description": "d", "time_of_day": "深夜",
             "weather": "阴雨连绵", "scene_features": ["昏暗"]}
          ],
          "scenes": [
            {
              "scene_number": 1,
              "scene_name": "客厅",
              "description": "深夜下雨",
              "dialogue": "小悠：借用浴室",
              "duration": 20,
              "character_description": {"小悠": "穿着透视装，头发凌乱", "阿强": "白衬衫，惊讶"},
              "voice_description": {"小悠": "声音颤抖", "阿强": "低沉犹豫"},
              "mood": "紧张",
              "time_of_day": "深夜",
              "weather": "阴雨连绵",
              "characters_present": ["小悠"]
            }
          ]
        }
        """
    )

    scene = parsed["scenes"][0]
    assert isinstance(scene["character_description"], str)
    assert isinstance(scene["voice_description"], str)
    assert "小悠：穿着透视装，头发凌乱" in scene["character_description"]
    assert "阿强：白衬衫，惊讶" in scene["character_description"]
    assert "小悠：声音颤抖" in scene["voice_description"]
    # 拍平后的数据必须能通过 Scene 校验
    Scene(**scene)


def test_coerce_scene_text_field_handles_str_list_dict():
    agent = ScriptAgent()

    assert agent._coerce_scene_text_field("已经是字符串") == "已经是字符串"
    assert agent._coerce_scene_text_field(None) == ""
    dict_out = agent._coerce_scene_text_field({"A": "跑", "B": "追"})
    assert dict_out == "A：跑\nB：追"
    list_out = agent._coerce_scene_text_field(
        [{"name": "A", "description": "跑"}, {"character": "B", "text": "追"}]
    )
    assert list_out == "A：跑\nB：追"


def test_ffmpeg_prepare_trimmed_segments_respects_configured_edge_frames(monkeypatch, tmp_path):
    original_get = config.get
    calls = []

    def fake_get(key, default=None):
        overrides = {
            "merge.trim_previous_end_frames": 6,
            "merge.trim_next_start_frames": 2,
            "video_generation.output_format": "mov",
        }
        return overrides.get(key, original_get(key, default))

    monkeypatch.setattr(config, "get", fake_get)
    monkeypatch.setattr(
        ffmpeg_service,
        "_trim_video_by_frames",
        lambda input_path, output_path, trim_start_frames, trim_end_frames: calls.append(
            (
                Path(input_path).name,
                Path(output_path).name,
                trim_start_frames,
                trim_end_frames,
            )
        ),
    )

    local_paths = [
        str(tmp_path / "scene_000.mov"),
        str(tmp_path / "scene_001.mov"),
        str(tmp_path / "scene_002.mov"),
    ]

    processed_paths = ffmpeg_service._prepare_trimmed_segments(local_paths, tmp_path)

    assert [Path(path).name for path in processed_paths] == [
        "scene_trimmed_000.mov",
        "scene_trimmed_001.mov",
        "scene_trimmed_002.mov",
    ]
    assert calls == [
        ("scene_000.mov", "scene_trimmed_000.mov", 0, 6),
        ("scene_001.mov", "scene_trimmed_001.mov", 2, 6),
        ("scene_002.mov", "scene_trimmed_002.mov", 2, 0),
    ]


def test_merge_agent_passes_temporary_edge_trim_to_ffmpeg(monkeypatch):
    original_get = config.get
    captured = {}

    def fake_get(key, default=None):
        overrides = {
            "merge.temporary_edge_trim": "on",
            "merge.trim_previous_end_frames": 6,
            "merge.trim_next_start_frames": 2,
            "video_generation.output_format": "mov",
        }
        return overrides.get(key, original_get(key, default))

    monkeypatch.setattr(config, "get", fake_get)

    merge_agent = MergeAgent()
    merge_agent.cleanup_enabled = False
    merge_agent.retain_hours = 1

    monkeypatch.setattr(
        "app.agents.merge_agent.ensure_project_temp_subdir",
        lambda project_id, name: Path("/tmp/test-merge"),
    )
    monkeypatch.setattr(
        "app.agents.merge_agent.ffmpeg_service.merge_videos",
        lambda video_urls, output_filename, transition_duration=0.5, temporary_edge_trim_enabled=False, work_dir=None: captured.update(
            {
                "video_urls": video_urls,
                "output_filename": output_filename,
                "temporary_edge_trim_enabled": temporary_edge_trim_enabled,
                "work_dir": work_dir,
            }
        ) or "/tmp/test-merge/final.mov",
    )
    monkeypatch.setattr(
        "app.agents.merge_agent.ffmpeg_service.convert_to_mp4",
        lambda source, output_filename, work_dir=None: captured.update(
            {
                "convert_source": source,
                "convert_output_filename": output_filename,
            }
        ) or "/tmp/test-merge/final_video.mp4",
    )
    monkeypatch.setattr(
        merge_agent,
        "_upload_or_copy_merged_output",
        lambda source, output_filename, project_id: captured.update(
            {"upload_source": source, "upload_output_filename": output_filename}
        ) or "https://example.com/final_video.mp4",
    )

    script = Script(
        title="demo",
        style="cinematic",
        background="background",
        characters=[Character(name="Hero", age="18", gender="female", face_features="sharp", skin_tone="fair", clothing="coat", voice_type="warm", voice_features="clear", voice_style="calm")],
        scene_definitions=[],
        scenes=[
            Scene(
                scene_number=1,
                scene_name="street",
                description="scene one",
                dialogue="hello",
                duration=10,
                character_description="desc",
                voice_description="voice",
                mood="tense",
                camera_angle="close-up",
                characters_present=["Hero"],
            )
        ],
        total_duration=10,
    )
    videos = [
        GeneratedVideo(
            scene_number=2,
            url="https://example.com/scene_02.mov",
            first_frame_url="https://example.com/scene_02.png",
            duration=10,
            prompt="scene 2",
        ),
        GeneratedVideo(
            scene_number=1,
            url="https://example.com/scene_01.mov",
            first_frame_url="https://example.com/scene_01.png",
            duration=10,
            prompt="scene 1",
        ),
    ]

    result = merge_agent.merge_videos(script, videos, project_id="demo123")

    assert result == "https://example.com/final_video.mp4"
    assert captured["temporary_edge_trim_enabled"] is True
    assert captured["video_urls"] == [
        "https://example.com/scene_01.mov",
        "https://example.com/scene_02.mov",
    ]
    # 合成后的 mov 转为 mp4 再上传
    assert captured["convert_source"] == "/tmp/test-merge/final.mov"
    assert captured["convert_output_filename"].endswith(".mp4")
    assert captured["upload_source"] == "/tmp/test-merge/final_video.mp4"
    assert captured["upload_output_filename"].endswith(".mp4")


def test_convert_to_mp4_uses_stream_copy_and_faststart(monkeypatch, tmp_path):
    source = tmp_path / "merged.mov"
    source.write_bytes(b"fake-mov-bytes")

    captured = {}

    def fake_run(cmd, capture_output=True, text=True, check=True):
        captured["cmd"] = cmd
        # 模拟 ffmpeg 生成输出文件
        Path(cmd[-1]).write_bytes(b"fake-mp4-bytes")

        class _Result:
            stderr = ""
            stdout = ""

        return _Result()

    monkeypatch.setattr("app.services.ffmpeg_service.subprocess.run", fake_run)
    monkeypatch.setattr(ffmpeg_service, "_require_binary", lambda name: "/usr/bin/ffmpeg")

    result = ffmpeg_service.convert_to_mp4(
        source=str(source),
        output_filename="final_video.mp4",
        work_dir=str(tmp_path),
    )

    assert result.endswith("final_video.mp4")
    assert Path(result).exists()
    cmd = captured["cmd"]
    # 优先无损转封装（stream copy）并前置 moov atom 以便 Web 边下边播
    assert "-c" in cmd and "copy" in cmd
    assert "-movflags" in cmd and "+faststart" in cmd


def test_normalize_segments_unifies_audio_params(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, capture_output=True, text=True, check=True):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"fake-normalized")

        class _Result:
            stderr = ""
            stdout = ""

        return _Result()

    monkeypatch.setattr("app.services.ffmpeg_service.subprocess.run", fake_run)
    monkeypatch.setattr(ffmpeg_service, "_require_binary", lambda name: "/usr/bin/ffmpeg")

    local_paths = [str(tmp_path / "scene_000.mov"), str(tmp_path / "scene_001.mov")]
    normalized = ffmpeg_service._normalize_segments_for_concat(local_paths, tmp_path)

    assert [Path(p).name for p in normalized] == [
        "scene_normalized_000.mp4",
        "scene_normalized_001.mp4",
    ]
    # 每个片段都被重编码为一致的 H.264 + AAC 48kHz 立体声，避免 concat 复制流后
    # 因采样率/profile 不一致导致后半段无声、播放中断。
    for cmd in calls:
        assert "libx264" in cmd
        assert "aac" in cmd
        assert "-ar" in cmd and "48000" in cmd
        assert "-ac" in cmd and "2" in cmd
    assert len(calls) == 2


def test_merge_videos_normalizes_segments_before_stream_copy_concat(monkeypatch, tmp_path):
    original_get = config.get

    def fake_get(key, default=None):
        overrides = {"video_generation.output_format": "mov"}
        return overrides.get(key, original_get(key, default))

    monkeypatch.setattr(config, "get", fake_get)
    monkeypatch.setattr(ffmpeg_service, "_require_binary", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ffmpeg_service, "_download_file", lambda url, local_path: Path(local_path).write_bytes(b"seg"))

    normalize_inputs = {}

    def fake_normalize(local_paths, work_path):
        normalize_inputs["paths"] = list(local_paths)
        out = [str(work_path / f"scene_normalized_{i:03d}.mp4") for i in range(len(local_paths))]
        for p in out:
            Path(p).write_bytes(b"norm")
        return out

    monkeypatch.setattr(ffmpeg_service, "_normalize_segments_for_concat", fake_normalize)

    concat_cmd = {}

    def fake_run(cmd, capture_output=True, text=True, check=True):
        concat_cmd["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"merged")

        class _Result:
            stderr = ""
            stdout = ""

        return _Result()

    monkeypatch.setattr("app.services.ffmpeg_service.subprocess.run", fake_run)

    result = ffmpeg_service.merge_videos(
        video_urls=["https://example.com/a.mov", "https://example.com/b.mov"],
        output_filename="merged.mov",
        work_dir=str(tmp_path),
    )

    # 归一化在 concat 之前被调用，且 concat 使用归一化后的片段
    assert len(normalize_inputs["paths"]) == 2
    concat_list = (tmp_path / "concat_list.txt").read_text()
    assert "scene_normalized_000.mp4" in concat_list
    assert "scene_normalized_001.mp4" in concat_list
    assert result.endswith("merged.mov")


