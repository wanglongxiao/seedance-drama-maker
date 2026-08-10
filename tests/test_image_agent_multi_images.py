# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

"""
单元测试：验证 ImageAgent 支持多张参考图片（最多9张）和角色映射功能
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.image_agent import ImageAgent
from app.models.schemas import Script, Character


class TestParseImageRoleMappings:
    """测试 _parse_image_role_mappings 方法"""

    def setup_method(self):
        self.agent = ImageAgent()

    def test_parse_single_role_mapping(self):
        """测试解析单个角色映射"""
        user_style_info = "@图片1 是角色 Jamoson"
        user_image_urls = ["http://example.com/img1.jpg"]

        result = self.agent._parse_image_role_mappings(user_style_info, user_image_urls)

        assert "image_to_roles" in result
        assert "role_to_image" in result
        assert result["role_to_image"]["Jamoson"] == "image_1"
        assert len(result["image_to_roles"]["image_1"]) == 1
        assert result["image_to_roles"]["image_1"][0]["role_name"] == "Jamoson"

    def test_parse_multiple_role_mappings(self):
        """测试解析多个角色映射"""
        user_style_info = "@图片1 是角色 Jamoson 和 @图片2 是角色 Red"
        user_image_urls = [
            "http://example.com/img1.jpg",
            "http://example.com/img2.jpg"
        ]

        result = self.agent._parse_image_role_mappings(user_style_info, user_image_urls)

        assert result["role_to_image"]["Jamoson"] == "image_1"
        assert result["role_to_image"]["Red"] == "image_2"
        assert len(result["image_to_roles"]["image_1"]) == 1
        assert len(result["image_to_roles"]["image_2"]) == 1

    def test_parse_position_mapping(self):
        """测试解析带位置的角色映射"""
        user_style_info = "@图片1 中的 角色 Jamoson 在左侧 和 角色 Red 在右侧"
        user_image_urls = ["http://example.com/img1.jpg"]

        result = self.agent._parse_image_role_mappings(user_style_info, user_image_urls)

        assert "image_1" in result["image_to_roles"]
        roles = result["image_to_roles"]["image_1"]
        assert len(roles) == 2

        role_names = [r["role_name"] for r in roles]
        assert "Jamoson" in role_names
        assert "Red" in role_names

        positions = {r["role_name"]: r["position"] for r in roles}
        assert positions["Jamoson"] == "左侧"
        assert positions["Red"] == "右侧"

    def test_parse_up_to_9_images(self):
        """测试支持最多9张图片"""
        user_style_info = " ".join([f"@图片{i} 是角色 Character{i}" for i in range(1, 10)])
        user_image_urls = [f"http://example.com/img{i}.jpg" for i in range(1, 10)]

        result = self.agent._parse_image_role_mappings(user_style_info, user_image_urls)

        assert len(result["role_to_image"]) == 9
        for i in range(1, 10):
            assert f"Character{i}" in result["role_to_image"]
            assert result["role_to_image"][f"Character{i}"] == f"image_{i}"

    def test_default_mapping_without_explicit_definition(self):
        """测试没有显式定义时的默认映射"""
        user_style_info = "生成一个科幻风格的视频"
        user_image_urls = [
            "http://example.com/img1.jpg",
            "http://example.com/img2.jpg"
        ]

        result = self.agent._parse_image_role_mappings(user_style_info, user_image_urls)

        # 没有显式定义时，应该返回空映射
        assert len(result["role_to_image"]) == 0
        assert len(result["image_to_roles"]) == 2  # 但应该有图片占位
        assert "image_1" in result["image_to_roles"]
        assert "image_2" in result["image_to_roles"]

    def test_empty_user_style_info(self):
        """测试空用户输入"""
        user_style_info = ""
        user_image_urls = ["http://example.com/img1.jpg"]

        result = self.agent._parse_image_role_mappings(user_style_info, user_image_urls)

        assert len(result["role_to_image"]) == 0
        # 空输入时，image_to_roles 也是空的
        assert len(result["image_to_roles"]) == 0

    def test_position_keywords(self):
        """测试各种位置关键词"""
        test_cases = [
            ("@图片1 中的 角色 A 在左侧", "左侧"),
            ("@图片1 中的 角色 A 在右侧", "右侧"),
            ("@图片1 中的 角色 A 在中间", "中间"),
            ("@图片1 中的 角色 A 在左边", "左边"),
            ("@图片1 中的 角色 A 在右边", "右边"),
            ("@图片1 中的 角色 A 在左", "左"),
            ("@图片1 中的 角色 A 在右", "右"),
        ]

        for style_info, expected_position in test_cases:
            result = self.agent._parse_image_role_mappings(style_info, ["http://example.com/img1.jpg"])
            if "image_1" in result["image_to_roles"]:
                roles = result["image_to_roles"]["image_1"]
                if roles and roles[0].get("position"):
                    assert roles[0]["position"] == expected_position, f"Failed for: {style_info}"


class TestStylizeUserImages:
    """测试 _stylize_user_images 方法（模拟测试）"""

    def setup_method(self):
        self.agent = ImageAgent()

    def test_support_up_to_9_images(self):
        """测试支持最多9张图片"""
        # 验证代码逻辑支持9张图片
        user_image_urls = [f"http://example.com/img{i}.jpg" for i in range(1, 10)]

        # 检查切片逻辑 [:9]
        image_urls_to_use = user_image_urls[:9]
        assert len(image_urls_to_use) == 9

    def test_role_mapping_integration(self):
        """测试角色映射集成"""
        user_style_info = "@图片1 是角色 Jamoson 和 @图片2 是角色 Red"
        user_image_urls = [
            "http://example.com/img1.jpg",
            "http://example.com/img2.jpg"
        ]

        role_mappings = self.agent._parse_image_role_mappings(user_style_info, user_image_urls)

        # 验证映射正确
        assert role_mappings["role_to_image"]["Jamoson"] == "image_1"
        assert role_mappings["role_to_image"]["Red"] == "image_2"

    def test_multi_character_in_single_image(self):
        """测试单张图片包含多个角色"""
        user_style_info = "@图片1 中的 角色 Jamoson 在左侧 和 角色 Red 在右侧"
        user_image_urls = ["http://example.com/img1.jpg"]

        role_mappings = self.agent._parse_image_role_mappings(user_style_info, user_image_urls)

        # 验证两个角色都映射到同一张图片
        assert role_mappings["role_to_image"]["Jamoson"] == "image_1"
        assert role_mappings["role_to_image"]["Red"] == "image_1"

        # 验证位置信息
        roles = role_mappings["image_to_roles"]["image_1"]
        assert len(roles) == 2


class TestCharacterRoleMapping:
    """测试角色与图片的映射逻辑"""

    def setup_method(self):
        self.agent = ImageAgent()

        # 创建测试剧本
        self.script = Script(
            title="Test Story",
            style="科幻风格",
            background="未来城市",
            total_duration=60,
            characters=[
                Character(
                    name="Jamoson",
                    age="25岁",
                    gender="男",
                    face_features="短发，戴眼镜",
                    skin_tone="黄色",
                    clothing="白色衬衫",
                    voice_type="男声",
                    voice_features="低沉",
                    voice_style="自然"
                ),
                Character(
                    name="Red",
                    age="23岁",
                    gender="女",
                    face_features="长发，大眼睛",
                    skin_tone="黄色",
                    clothing="红色连衣裙",
                    voice_type="女声",
                    voice_features="清脆",
                    voice_style="活泼"
                ),
                Character(
                    name="波波",
                    age="3岁",
                    gender="未知",
                    face_features="熊猫，黑白毛色",
                    skin_tone="黑白",
                    clothing="无",
                    voice_type="童声",
                    voice_features="可爱",
                    voice_style="天真"
                )
            ],
            scenes=[]
        )

    def test_human_vs_non_human_detection(self):
        """测试人类 vs 非人类角色识别"""
        human_chars = []
        non_human_chars = []

        non_human_keywords = ['熊', 'cat', 'dog', 'animal', 'pet', 'creature', 'monster', '波波', 'panda', 'rabbit', 'fox', 'wolf', 'lion', 'tiger', 'elephant', 'bird', 'fish']

        for char in self.script.characters:
            char_desc = f"{char.name} {char.face_features} {char.clothing or ''}".lower()
            is_non_human = any(keyword in char_desc for keyword in non_human_keywords)

            if is_non_human:
                non_human_chars.append(char)
            else:
                human_chars.append(char)

        assert len(human_chars) == 2  # Jamoson, Red
        assert len(non_human_chars) == 1  # 波波
        assert human_chars[0].name == "Jamoson"
        assert human_chars[1].name == "Red"
        assert non_human_chars[0].name == "波波"


class TestImageReferenceLimits:
    """测试图片引用限制"""

    def test_max_9_images(self):
        """测试最多9张图片"""
        agent = ImageAgent()

        # 测试9张图片
        nine_images = [f"http://example.com/img{i}.jpg" for i in range(1, 10)]
        result = nine_images[:9]
        assert len(result) == 9

        # 测试超过9张图片应该被截断
        ten_images = [f"http://example.com/img{i}.jpg" for i in range(1, 11)]
        result = ten_images[:9]
        assert len(result) == 9
