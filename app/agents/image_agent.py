# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import random
import re
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.config import config
from app.services.llm_service import llm_service
from app.utils.logger import get_logger
from app.models.schemas import Script, GeneratedImage, Character

logger = get_logger("image_agent")


class ImageAgent:
    """角色/场景/图片生成Agent - 调用seedream-5.0-off"""

    def __init__(self):
        self.model = config.get('models.image.endpoint')
        self.size = config.get('models.image.default_params.size', '2K')
        self.default_aspect_ratio = "16:9"
        # 并发配置
        self.concurrency_enabled = config.get('generation.concurrency.enabled', True)
        self.max_workers = config.get('generation.concurrency.image_workers', 5)

    def _should_apply_japanese_manga_live_action_style(self, user_style_info: str, script: Script) -> bool:
        """未指定明确风格，或指定真人/写实时，参考图默认回落到写实漫画风格。"""
        combined = " ".join(filter(None, [user_style_info or "", getattr(script, "style", "") or ""])).lower()
        combined = combined.replace("：", ":")

        realistic_keywords = [
            "真人", "写实", "寫實", "realistic", "photoreal", "photorealistic",
            "live action", "live-action", "cinematic realism", "真人电影", "真人電影"
        ]
        stylized_non_realistic_keywords = [
            "美漫", "q版", "q版动漫", "q版動漫", "卡通", "动画", "動畫", "anime", "cartoon",
            "watercolor", "水彩", "油画", "油畫", "pixel", "像素", "cyberpunk", "赛博", "賽博",
            "国风", "國風", "水墨", "chibi", "disney", "ghibli"
        ]

        if any(keyword in combined for keyword in realistic_keywords):
            return True
        if any(keyword in combined for keyword in stylized_non_realistic_keywords):
            return False
        return True

    def _append_reference_style_guidance(self, prompt_parts: List[str], user_style_info: str, script: Script) -> None:
        if self._should_apply_japanese_manga_live_action_style(user_style_info, script):
            prompt_parts.append("[CRITICAL] Reference character visual style: 写实漫画风格")
            prompt_parts.append("[CRITICAL] Use a realistic comic-inspired look with natural facial anatomy, realistic skin detail, and cinematic character rendering")
            prompt_parts.append("[CRITICAL] Keep the skin, facial features, eyes, and lighting realistic while preserving a polished realistic comic aesthetic")

    def _extract_reference_prompt_style(self, user_style_info: str) -> str:
        """从用户输入中仅提炼参考图需要的风格/比例/样式，不保留原始长文本。"""
        if not user_style_info:
            return ""

        text = str(user_style_info).replace("\r", "\n")
        text = re.sub(r'[*#>`_-]{2,}', '\n', text)
        text = text.replace("：", ":")

        style_keywords = [
            "风格", "樣式", "样式", "style", "比例", "aspect ratio",
            "真人", "写实", "寫實", "日漫", "电影", "電影", "美漫", "动漫", "動畫", "anime",
            "live action", "live-action", "realistic", "photoreal", "photorealistic", "cinematic",
        ]
        disallowed_keywords = [
            "故事原文", "剧情", "故事线", "分场景", "场景一", "场景二", "场景三", "场景四", "场景五",
            "对白", "旁白", "台词", "對白", "旁白", "鏡頭", "镜头", "钩子", "系统音",
            "时长", "秒", "分钟", "音樂", "音乐", "配乐", "配樂", "bgm", "music", "voiceover",
            "dialogue", "narration", "episode", "scene", "story", "plot",
        ]

        raw_segments = re.split(r'[\n]+|(?<=[。！？；;])', text)
        style_segments: List[str] = []

        for segment in raw_segments:
            cleaned = segment.strip(" \t,，。；;:-")
            if not cleaned:
                continue

            lowered = cleaned.lower()
            has_style = any(keyword in lowered for keyword in style_keywords)
            has_disallowed = any(keyword in lowered for keyword in disallowed_keywords)

            if has_disallowed and not has_style:
                continue

            if has_style:
                style_segments.append(cleaned)

        def _dedupe(items: List[str], limit: int) -> str:
            deduped: List[str] = []
            seen = set()
            for item in items:
                normalized = re.sub(r'\s+', ' ', item).strip().lower()
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    deduped.append(item)
            return "；".join(deduped[:limit])

        style_text = _dedupe(style_segments, 6)
        logger.info(
            f"Extracted reference prompt style from {len(user_style_info)} chars to {len(style_text)} chars"
        )
        return style_text

    def _normalize_asset_name(self, name: Optional[str], fallback: str) -> str:
        normalized = re.sub(r"\s+", " ", str(name or "").strip())
        return normalized or fallback

    def _build_scene_reference_name(self, scene_description: str, index: int) -> str:
        cleaned = re.sub(r"\s+", " ", str(scene_description or "").strip())
        if not cleaned:
            return f"Scene {index}"
        shortened = re.split(r"[，。；;,.!?！？\n]", cleaned)[0].strip()
        return shortened[:24] or f"Scene {index}"

    def _infer_scene_time_guidance(self, scene_name: str, scene_description: str) -> List[str]:
        text = " ".join([
            str(scene_name or ""),
            str(scene_description or ""),
        ]).lower()

        night_keywords = ["深夜", "夜晚", "夜里", "晚上", "午夜", "月光", "霓虹夜景", "night", "midnight", "moonlight", "evening"]
        dawn_keywords = ["凌晨", "黎明", "清晨", "晨光", "破晓", "dawn", "sunrise", "morning"]
        sunset_keywords = ["黄昏", "傍晚", "日落", "晚霞", "sunset", "dusk", "twilight"]
        winter_keywords = ["冬", "寒冬", "雪", "冰", "winter", "snow", "icy"]
        autumn_keywords = ["秋", "落叶", "金黄", "autumn", "fall", "maple"]
        summer_keywords = ["夏", "盛夏", "炎热", "蝉鸣", "summer", "sunny season"]
        spring_keywords = ["春", "花开", "新芽", "spring", "blossom"]

        guidance: List[str] = []
        if any(keyword in text for keyword in night_keywords):
            guidance.append("Time requirement: night scene with lighting, sky, and ambience matching nighttime")
        elif any(keyword in text for keyword in dawn_keywords):
            guidance.append("Time requirement: dawn or morning scene with early light and fresh atmosphere")
        elif any(keyword in text for keyword in sunset_keywords):
            guidance.append("Time requirement: sunset or dusk scene with warm low-angle light")
        else:
            guidance.append("Time requirement: default to clear daytime lighting when no explicit time cue is provided")

        if any(keyword in text for keyword in winter_keywords):
            guidance.append("Season requirement: winter atmosphere")
        elif any(keyword in text for keyword in autumn_keywords):
            guidance.append("Season requirement: autumn atmosphere")
        elif any(keyword in text for keyword in summer_keywords):
            guidance.append("Season requirement: summer atmosphere")
        elif any(keyword in text for keyword in spring_keywords):
            guidance.append("Season requirement: spring atmosphere")

        return guidance

    def generate_character_reference_image(
        self,
        character: Character,
        script: Script,
        user_style_info: str = None,
        aspect_ratio: str = None,
        user_reference_images: List[str] = None,
        variation_requirements: str = None,
    ) -> GeneratedImage:
        if not aspect_ratio:
            aspect_ratio = self.default_aspect_ratio

        prompt_parts: List[str] = [f"Aspect ratio: {aspect_ratio}"]
        reference_style_info = self._extract_reference_prompt_style(user_style_info)
        self._append_reference_style_guidance(prompt_parts, reference_style_info, script)

        if reference_style_info:
            prompt_parts.append("[REFERENCE STYLE REQUIREMENTS]")
            prompt_parts.append(reference_style_info)

        if variation_requirements:
            prompt_parts.append("[VARIATION REQUIREMENTS]")
            prompt_parts.append(variation_requirements)

        prompt_parts.append(f"[CHARACTER REFERENCE] {character.name}")
        if user_reference_images:
            prompt_parts.append("[CRITICAL] Preserve the uploaded character identity and key appearance traits.")
            prompt_parts.append("[CRITICAL] Keep the face, hairstyle, clothing details, and silhouette recognizable.")
        else:
            prompt_parts.append("[CRITICAL] Generate the character from the character definition only.")

        prompt_parts.append(f"Name: {character.name}")
        prompt_parts.append(f"Profile: {character.age} {character.gender}")
        prompt_parts.append(f"Face features: {character.face_features}")
        prompt_parts.append(f"Skin tone: {character.skin_tone}")
        if character.clothing:
            prompt_parts.append(f"Clothing: {character.clothing}")

        prompt_parts.append("Front view, facing camera, clear facial features")
        prompt_parts.append("Large half-body portrait above the knees, face occupying a significant portion of the frame")
        prompt_parts.append("Crop above the knees and keep the face details prominent")
        prompt_parts.append("Pure white background, no scenery, no props")
        prompt_parts.append("High quality character reference image for consistent video generation")

        prompt = "\n".join(prompt_parts)
        response = llm_service.generate_image(
            prompt=prompt,
            model=self.model,
            size=self.size,
            image_urls=user_reference_images or None,
            ratio=aspect_ratio,
        )
        return GeneratedImage(
            scene_number=0,
            url=response["data"][0]["url"],
            prompt=prompt,
            is_reference=True,
            name=self._normalize_asset_name(character.name, "Character"),
            reference_type="character",
        )

    def generate_scene_reference_image(
        self,
        scene_name: str,
        scene_description: str,
        script: Script,
        user_style_info: str = None,
        aspect_ratio: str = None,
        user_reference_images: List[str] = None,
        variation_requirements: str = None,
    ) -> GeneratedImage:
        if not aspect_ratio:
            aspect_ratio = self.default_aspect_ratio

        prompt_parts: List[str] = [f"Aspect ratio: {aspect_ratio}"]
        reference_style_info = self._extract_reference_prompt_style(user_style_info)
        if reference_style_info:
            prompt_parts.append("[REFERENCE STYLE REQUIREMENTS]")
            prompt_parts.append(reference_style_info)
        else:
            prompt_parts.append(f"Story style: {script.style}")

        if variation_requirements:
            prompt_parts.append("[VARIATION REQUIREMENTS]")
            prompt_parts.append(variation_requirements)

        prompt_parts.append(f"[SCENE REFERENCE] {scene_name}")
        prompt_parts.append(f"Backdrop definition: {scene_description}")
        prompt_parts.extend(self._infer_scene_time_guidance(scene_name, scene_description))
        if user_reference_images:
            prompt_parts.append("[CRITICAL] Preserve the major environment layout and landmark details from uploaded images.")
        prompt_parts.append("[CRITICAL] Environment-only scene board. Do not show any person, face, body, crowd, character silhouette, or creature.")
        prompt_parts.append("[CRITICAL] Focus on environment and scene atmosphere only.")
        prompt_parts.append("Wide cinematic environment composition")
        prompt_parts.append("Empty environment, no humans, no characters, no foreground person")
        prompt_parts.append("No subtitles, no text overlay, no street sign text")
        prompt_parts.append("High quality key scene reference image for consistent video generation")

        prompt = "\n".join(prompt_parts)
        response = llm_service.generate_image(
            prompt=prompt,
            model=self.model,
            size=self.size,
            image_urls=user_reference_images or None,
            ratio=aspect_ratio,
        )
        return GeneratedImage(
            scene_number=0,
            url=response["data"][0]["url"],
            prompt=prompt,
            is_reference=True,
            name=self._normalize_asset_name(scene_name, "Scene"),
            reference_type="scene",
        )

    def generate_reference_image(
        self,
        script: Script,
        user_style_info: str = None,
        aspect_ratio: str = None,
        user_reference_images: List[str] = None,
        variation_requirements: str = None,
    ) -> GeneratedImage:
        """
        第一步：生成参考图库中的角色主参考图

        逻辑：
        1. 如果chatbot用户输入中包含风格信息，则仅使用用户输入的风格生成图片
        2. 如果用户上传了图片（支持多张），对用户上传的图片进行风格化后去掉背景，仅保留主要角色的大半身正面像（膝盖以上），作为参考图库中的角色主参考图
        3. 如果用户没有上传图片，根据chatbot用户补充的描述信息（尤其是风格、场景、其他描述信息）与初始输入，
           生成无背景的风格化主要角色的大半身正面像（膝盖以上）作为参考图库中的角色主参考图

        参考图库会展示于预览区域，可以被下载/重新生成。待用户确认参考图库可用后再进行下一步。

        Args:
            script: 剧本对象
            user_style_info: 用户补充的风格和场景描述信息（严格使用用户输入的风格）
            aspect_ratio: 图片比例（如用户未指定，使用默认16:9）
            user_reference_images: 用户上传的参考图片URL列表（支持1-4张）

        Returns:
            生成的参考图库
        """
        # 使用默认比例如果未指定
        if not aspect_ratio:
            aspect_ratio = self.default_aspect_ratio

        # 处理单张图片的兼容性（向后兼容）
        if isinstance(user_reference_images, str):
            user_reference_images = [user_reference_images]

        logger.log_agent_call("ImageAgent", "generate_reference_image", {
            "script_title": script.title,
            "aspect_ratio": aspect_ratio,
            "has_user_style_info": bool(user_style_info),
            "num_user_reference_images": len(user_reference_images) if user_reference_images else 0,
        })

        logger.info(f"Step 1: Generating reference library")
        logger.info(f"Using aspect_ratio: {aspect_ratio}")
        logger.info(f"User style info: {user_style_info[:200] if user_style_info else 'None'}...")
        reference_prompt_style = self._extract_reference_prompt_style(user_style_info)
        logger.info(
            f"Reference-image style metadata: {reference_prompt_style[:200] if reference_prompt_style else 'None'}..."
        )
        logger.info(f"User reference images count: {len(user_reference_images) if user_reference_images else 0}")
        if user_reference_images:
            for i, url in enumerate(user_reference_images):
                logger.info(f"  Image {i+1}: {url[:100]}...")

        # 生成参考图库中的角色主参考图
        if user_reference_images and len(user_reference_images) > 0:
            # 用户上传了图片（支持多张），进行风格化后去掉背景，仅保留主要角色的全身正面像作为参考图库主图
            logger.info(f"User uploaded {len(user_reference_images)} image(s). Stylizing as reference library image - large half-body front view above the knees, no background")
            reference_image = self._stylize_user_images(
                user_image_urls=user_reference_images,
                script=script,
                user_style_info=user_style_info,
                reference_style_info=reference_prompt_style,
                aspect_ratio=aspect_ratio,
                variation_requirements=variation_requirements,
            )
        else:
            # 用户没有上传图片，根据描述生成无背景的风格化主要角色全身正面像作为参考图库主图
            logger.info("Generating stylized reference library image - large half-body front view above the knees, no background based on user description")
            reference_image = self._generate_reference_image_from_description(
                script=script,
                user_style_info=reference_prompt_style,
                aspect_ratio=aspect_ratio,
                variation_requirements=variation_requirements,
            )

        logger.info(f"Reference library image generated successfully: {reference_image.url}")

        return reference_image

    def generate_scene_images(
        self,
        script: Script,
        reference_image_url: str,
        user_style_info: str = None,
        aspect_ratio: str = None,
    ) -> List[GeneratedImage]:
        """
        【已废弃】第二步：生成分镜首帧图片和结尾帧

        由于流程简化，此方法不再生成分镜首帧图和结尾帧。
        视频生成现在直接使用参考图库和分镜脚本。

        保留此方法以兼容旧代码，但返回空列表。

        Args:
            script: 剧本对象
            reference_image_url: 参考图库主图URL
            user_style_info: 用户补充的风格和场景描述信息
            aspect_ratio: 图片比例

        Returns:
            空列表（流程简化后不再生成分镜图）
        """
        logger.info("generate_scene_images: 流程已简化，不再生成分镜首帧图和结尾帧")
        logger.info("视频生成将直接使用参考图库和分镜脚本")
        return []

    def _parse_image_role_mappings(self, user_style_info: str, user_image_urls: List[str]) -> Dict[str, Any]:
        """
        解析用户输入中的 @图片1 @图片2 等角色定义

        支持格式：
        - @图片1 是角色 Jamoson
        - @图片2 是角色 Red
        - @图片1 中的 角色 Jamoson 在左侧 和 角色 Red 在右侧
        - @图片1 是主角，@图片2 是配角

        Args:
            user_style_info: 用户输入的风格和场景描述信息
            user_image_urls: 用户上传的图片URL列表

        Returns:
            角色到图片的映射字典，格式：
            {
                "image_to_roles": {
                    "image_1": [{"role_name": "Jamoson", "position": "left"}],
                    "image_2": [{"role_name": "Red", "position": "right"}]
                },
                "role_to_image": {
                    "Jamoson": "image_1",
                    "Red": "image_2"
                }
            }
        """
        import re

        mappings = {
            "image_to_roles": {},
            "role_to_image": {}
        }

        if not user_style_info:
            return mappings

        # 匹配 @图片1 @图片2 等格式
        image_pattern = r'@图片(\d+)'

        # 查找所有 @图片N 的引用
        image_refs = re.findall(image_pattern, user_style_info)

        if not image_refs:
            # 如果没有显式定义，使用默认映射（按顺序）
            for i, url in enumerate(user_image_urls, 1):
                mappings["image_to_roles"][f"image_{i}"] = [{"role_name": None, "position": None}]
            return mappings

        logger.info(f"Found {len(image_refs)} image references in user input")

        # 解析每个 @图片N 后的角色定义
        for img_num_str in image_refs:
            img_num = int(img_num_str)
            img_key = f"image_{img_num}"

            # 找到 @图片N 的位置
            pattern = rf'@图片{img_num}([^@]*)'
            match = re.search(pattern, user_style_info)

            if match:
                context = match.group(1).strip()
                roles_in_image = []

                # 尝试解析角色名和位置
                # 格式1: "是角色 XXX"
                role_match = re.search(r'是角色\s+(\w+)', context)
                if role_match:
                    role_name = role_match.group(1)
                    roles_in_image.append({"role_name": role_name, "position": None})
                    mappings["role_to_image"][role_name] = img_key

                # 格式2: "中的 角色 XXX 在左侧/右侧/中间"
                multi_role_pattern = r'角色\s+(\w+)\s+在(左侧|右侧|中间|左边|右边|左|右)'
                multi_matches = re.findall(multi_role_pattern, context)
                for role_name, position in multi_matches:
                    roles_in_image.append({"role_name": role_name, "position": position})
                    mappings["role_to_image"][role_name] = img_key

                # 格式3: "是主角/配角/角色名"（简化格式）
                if not roles_in_image:
                    simple_match = re.search(r'是(\w+)', context)
                    if simple_match:
                        role_desc = simple_match.group(1)
                        roles_in_image.append({"role_name": role_desc, "position": None})

                if roles_in_image:
                    mappings["image_to_roles"][img_key] = roles_in_image

        logger.info(f"Parsed image-role mappings: {mappings}")
        return mappings

    def _stylize_user_images(
        self,
        user_image_urls: List[str],
        script: Script,
        user_style_info: str,
        reference_style_info: str,
        aspect_ratio: str,
        variation_requirements: str = None,
    ) -> GeneratedImage:
        """
        对用户上传的多张图片进行处理，生成参考图库主图

        核心逻辑：
        1. 如果用户上传了角色图片，应该保留原角色的脸部/身材/其他重要特征
        2. 只进行简单的背景移除处理，转为白底
        3. 不要重新生成角色形象，而是保留上传图片中的角色特征
        4. 支持1-9张用户上传图片

        Args:
            user_image_urls: 用户上传的图片URL列表（1-9张）
            script: 剧本对象
            user_style_info: 用户补充的风格和场景描述信息
            aspect_ratio: 图片比例

        Returns:
            生成的参考图库
        """
        # 解析用户输入中的角色映射
        role_mappings = self._parse_image_role_mappings(user_style_info, user_image_urls)

        prompt_parts = []
        num_user_images = len(user_image_urls)

        # 添加画面比例
        prompt_parts.append(f"Aspect ratio: {aspect_ratio}")
        self._append_reference_style_guidance(prompt_parts, reference_style_info, script)

        if variation_requirements:
            prompt_parts.append("\n[VARIATION REQUIREMENTS]")
            prompt_parts.append(variation_requirements)

        if reference_style_info:
            prompt_parts.append("\n[REFERENCE STYLE REQUIREMENTS]")
            prompt_parts.append(reference_style_info)

        # 【最高优先级 - 保留上传图片中的角色特征】
        prompt_parts.append("[CRITICAL - PRESERVE ORIGINAL CHARACTERS]")
        prompt_parts.append("[CRITICAL] The uploaded images contain the EXACT characters to use")
        prompt_parts.append("[CRITICAL] DO NOT generate new characters or change the character designs")
        prompt_parts.append("[CRITICAL] Keep the EXACT same facial features, body shape, clothing, colors from uploaded images")
        prompt_parts.append("[CRITICAL] Preserve all unique characteristics: hairstyle, accessories, markings, logos, colors")
        prompt_parts.append("[CRITICAL] The character in the output must be IDENTICAL to the character in the input image")

        # 分析角色类型（人类 vs 非人类）
        human_chars = []
        non_human_chars = []

        if script.characters:
            for char in script.characters:
                # 判断是否为非人类角色（根据名称、描述中的关键词）
                char_desc = f"{char.name} {char.face_features} {char.clothing or ''}".lower()
                non_human_keywords = ['熊', 'cat', 'dog', 'animal', 'pet', 'creature', 'monster', '波波', 'panda', 'rabbit', 'fox', 'wolf', 'lion', 'tiger', 'elephant', 'bird', 'fish']

                is_non_human = any(keyword in char_desc for keyword in non_human_keywords)

                if is_non_human:
                    non_human_chars.append(char)
                else:
                    human_chars.append(char)

        logger.info(f"Character analysis: {len(human_chars)} human characters, {len(non_human_chars)} non-human characters")
        logger.info(f"User uploaded {num_user_images} image(s) for character reference")
        logger.info(f"Role mappings: {role_mappings}")

        # 【关键 - 角色保留说明】
        prompt_parts.append("\n[CHARACTER PRESERVATION RULES]")
        prompt_parts.append("[CRITICAL] Process ALL characters from the uploaded images")
        prompt_parts.append("[CRITICAL] Keep characters in their original form - only remove background")

        # 处理人类角色（保留上传图片中的角色）
        if human_chars:
            for i, char in enumerate(human_chars):
                # 检查角色是否有对应的图片映射
                char_img_key = role_mappings["role_to_image"].get(char.name)

                if char_img_key:
                    # 从映射中提取图片编号
                    img_num = int(char_img_key.replace("image_", ""))
                    img_index = img_num - 1

                    if img_index < num_user_images:
                        # 有对应的用户图片
                        prompt_parts.append(f"\n[CHARACTER {i+1}: {char.name} - FROM REFERENCE IMAGE {img_num}]")
                        prompt_parts.append(f"[CRITICAL] Use the EXACT character from reference image {img_num}:")
                        prompt_parts.append("  - IDENTICAL facial features, expression, and identity")
                        prompt_parts.append("  - IDENTICAL body shape, proportions, and posture")
                        prompt_parts.append("  - IDENTICAL clothing, colors, and accessories")
                        prompt_parts.append("  - IDENTICAL hairstyle and unique markings")
                        prompt_parts.append("[CRITICAL] DO NOT change or stylize the character - preserve it exactly as shown")

                        # 检查是否有位置信息
                        roles_in_img = role_mappings["image_to_roles"].get(char_img_key, [])
                        for role_info in roles_in_img:
                            if role_info.get("role_name") == char.name and role_info.get("position"):
                                prompt_parts.append(f"[INFO] This character is located at the {role_info['position']} of image {img_num}")
                    else:
                        char_img_key = None

                if not char_img_key:
                    # 没有对应的用户图片映射，尝试按顺序匹配
                    if i < num_user_images:
                        img_num = i + 1
                        prompt_parts.append(f"\n[CHARACTER {i+1}: {char.name} - FROM REFERENCE IMAGE {img_num}]")
                        prompt_parts.append(f"[CRITICAL] Use the EXACT character from reference image {img_num}")
                        prompt_parts.append("[CRITICAL] Preserve all original features exactly - do not modify")
                    else:
                        # 没有对应的用户图片，根据文本生成
                        prompt_parts.append(f"\n[CHARACTER {i+1}: {char.name} - GENERATE FROM CHARACTER DEFINITION]")
                        prompt_parts.append("[NOTE] No reference image for this character")
                        prompt_parts.append("[RULE] Generate based on the character definition only")

                prompt_parts.append(f"\nCharacter Details (for reference):")
                prompt_parts.append(f"  Name: {char.name}")
                prompt_parts.append(f"  Role: {char.age}{char.gender}")
                if char.clothing:
                    prompt_parts.append(f"  Costume: {char.clothing}")
                if char.face_features:
                    prompt_parts.append(f"  Features: {char.face_features}")

        # 处理非人类角色（同样保留上传图片中的特征）
        if non_human_chars:
            prompt_parts.append("\n[NON-HUMAN CHARACTERS - PRESERVE FROM IMAGES]")
            prompt_parts.append("[CRITICAL] For non-human characters, also preserve from reference images if available")

            for i, char in enumerate(non_human_chars, 1):
                prompt_parts.append(f"\nNon-Human Character {i}: {char.name}")
                prompt_parts.append(f"  Description: {char.face_features}")
                if char.clothing:
                    prompt_parts.append(f"  Features: {char.clothing}")
                prompt_parts.append(f"  [RULE] Preserve exact appearance from reference image if available")

        # 【关键 - 只进行背景移除，不改变角色】
        prompt_parts.append("\n[CRITICAL - BACKGROUND REMOVAL ONLY]")
        prompt_parts.append("[CRITICAL] ONLY task: Remove the background and replace with pure white")
        prompt_parts.append("[CRITICAL] DO NOT change the character design, style, or appearance")
        prompt_parts.append("[CRITICAL] DO NOT re-interpret or re-imagine the character")
        prompt_parts.append("[CRITICAL] Keep the character EXACTLY as it appears in the uploaded image")
        prompt_parts.append("[CRITICAL] Remove ONLY the background, keep ALL character details intact")
        prompt_parts.append("[CRITICAL] If the image already has white background, preserve the character as-is")

        # 【关键 - 正面形象要求】
        prompt_parts.append("\n[POSE REQUIREMENT]")
        prompt_parts.append("[CRITICAL] Convert all characters to front-facing pose if they are not already")
        prompt_parts.append("[CRITICAL] Maintain the EXACT same character appearance, just change the viewing angle to front")
        prompt_parts.append("[CRITICAL] Large half-body front view above the knees, facing forward")
        prompt_parts.append("[CRITICAL] Crop the character above the knees")
        prompt_parts.append("[CRITICAL] The face should occupy a large portion of the frame with clear facial details")

        # 总角色数
        total_chars = len(human_chars) + len(non_human_chars)
        if total_chars > 0:
            prompt_parts.append(f"\n[CRITICAL] Show exactly {total_chars} different main characters")
            prompt_parts.append("[CRITICAL] Each character must match its reference image exactly")

        prompt_parts.append("Pure white background, high quality, detailed")
        prompt_parts.append("Large half-body portrait above the knees, front-facing, face clearly visible, detailed facial features")
        prompt_parts.append("This is a reference image for video generation - characters must be recognizable")

        prompt = "\n".join(prompt_parts)

        logger.info(f"Processing {num_user_images} user image(s) with prompt: {prompt[:300]}...")
        logger.info(f"Using aspect ratio: {aspect_ratio}")

        # 使用所有用户图片作为参考进行处理（最多9张）
        image_urls_to_use = user_image_urls[:9]
        logger.info(f"Using {len(image_urls_to_use)} reference image(s) for processing")

        response = llm_service.generate_image(
            prompt=prompt,
            model=self.model,
            size=self.size,
            image_urls=image_urls_to_use,
            ratio=aspect_ratio
        )

        image_url = response['data'][0]['url']
        logger.info(f"User images processed successfully as reference library image - {total_chars} characters preserved using {len(image_urls_to_use)} reference image(s)")

        return GeneratedImage(
            scene_number=0,
            url=image_url,
            prompt=prompt,
            is_end_frame=False,
            is_reference=True
        )

    def _generate_reference_image_from_description(
        self,
        script: Script,
        user_style_info: str,
        aspect_ratio: str,
        variation_requirements: str = None,
    ) -> GeneratedImage:
        """根据描述生成参考图库主图 - 无背景的风格化所有主要角色大半身正面像（膝盖以上）参考图"""
        prompt_parts = []

        # 添加画面比例
        prompt_parts.append(f"Aspect ratio: {aspect_ratio}")
        self._append_reference_style_guidance(prompt_parts, user_style_info, script)

        if variation_requirements:
            prompt_parts.append("[VARIATION REQUIREMENTS]")
            prompt_parts.append(variation_requirements)

        # 仅保留提炼后的参考图风格要点，不再直接拼接原始长文本
        if user_style_info:
            prompt_parts.append("[REFERENCE STYLE REQUIREMENTS]")
            prompt_parts.append(user_style_info)
        else:
            prompt_parts.append(f"Story style: {script.style}")

        # 【关键 - 无背景要求】
        prompt_parts.append("[CRITICAL - NO BACKGROUND] Pure white/transparent background ONLY")
        prompt_parts.append("[CRITICAL] No environment, no scenery, no background elements whatsoever")
        prompt_parts.append("[CRITICAL] Characters isolated on plain white background")

        # 添加角色描述（根据角色数量决定）
        num_characters = len(script.characters) if script.characters else 0
        if num_characters > 0:
            if num_characters == 1:
                # 只有一个角色
                char = script.characters[0]
                prompt_parts.append("Main character reference image (front view, large half body above the knees):")
                prompt_parts.append("[CRITICAL] Show exactly ONE character only")
                prompt_parts.append("[CRITICAL] Do NOT duplicate the character, show only ONE instance")
                char_desc = f"{char.name}: {char.age} {char.gender}, {char.face_features}, {char.skin_tone}"
                prompt_parts.append(char_desc)
                if char.clothing:
                    prompt_parts.append(f"Wearing: {char.clothing}")
            else:
                # 多个角色
                prompt_parts.append(f"Main characters reference image (front view, large half body above the knees):")
                prompt_parts.append(f"[CRITICAL] Show exactly {num_characters} different main characters")
                prompt_parts.append("[CRITICAL] Each character should be distinct and unique, NO duplicates")
                for i, char in enumerate(script.characters, 1):
                    char_desc = f"Character {i} - {char.name}: {char.age} {char.gender}, {char.face_features}, {char.skin_tone}"
                    prompt_parts.append(char_desc)
                    if char.clothing:
                        prompt_parts.append(f"  Wearing: {char.clothing}")

        prompt_parts.append("Front view, facing camera, clear facial features")
        prompt_parts.append("Large half-body portrait above the knees, face occupying a significant portion of the frame")
        prompt_parts.append("Crop above the knees and keep the face details prominent")
        prompt_parts.append("Consistent character design, standalone figures on white background")
        prompt_parts.append("This is a stylized reference image for subsequent scene generation")
        prompt_parts.append("High quality, detailed, 8k resolution, white background")

        prompt = ", ".join(prompt_parts)

        logger.info(f"Generating reference library image with prompt: {prompt[:200]}...")
        logger.info(f"Using aspect ratio: {aspect_ratio}")

        response = llm_service.generate_image(
            prompt=prompt,
            model=self.model,
            size=self.size,
            ratio=aspect_ratio
        )

        image_url = response['data'][0]['url']

        logger.info(f"Reference library image generated successfully - all main characters large half-body front view above the knees, no background")

        return GeneratedImage(
            scene_number=0,
            url=image_url,
            prompt=prompt,
            is_end_frame=False,
            is_reference=True
        )

    def _generate_images_concurrent(self, tasks: List[Dict]) -> List[GeneratedImage]:
        """并发生成图片"""
        images = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_task = {
                executor.submit(self._generate_single_image_with_reference, task): task
                for task in tasks
            }

            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    image = future.result()
                    images.append(image)
                    logger.info(f"Image generated: {task['type']} {task['scene_number']}")
                except Exception as e:
                    logger.error(f"Failed to generate image for {task['type']} {task['scene_number']}: {str(e)}")
                    raise

        return images

    def _generate_images_sequential(self, tasks: List[Dict]) -> List[GeneratedImage]:
        """串行生成图片"""
        images = []

        for task in tasks:
            try:
                image = self._generate_single_image_with_reference(task)
                images.append(image)
                logger.info(f"Image generated: {task['type']} {task['scene_number']}")
            except Exception as e:
                logger.error(f"Failed to generate image for {task['type']} {task['scene_number']}: {str(e)}")
                raise

        return images

    def _generate_single_image_with_reference(self, task: Dict) -> GeneratedImage:
        """生成单张图片（使用参考图）"""
        scene_number = task['scene_number']
        prompt = task['prompt']
        is_end_frame = task['is_end_frame']
        reference_image = task.get('reference_image')
        aspect_ratio = task.get('aspect_ratio', '16:9')

        label = f"结尾帧" if is_end_frame else f"分镜 {scene_number}"
        logger.info(f"Generating {label} image with reference image")
        logger.info(f"Using aspect ratio: {aspect_ratio}")

        # 使用参考图生成（如果提供了参考图）
        response = llm_service.generate_image(
            prompt=prompt,
            model=self.model,
            size=self.size,
            image_urls=[reference_image] if reference_image else None,
            ratio=aspect_ratio
        )

        image_url = response['data'][0]['url']

        logger.info(f"{label} image generated successfully")

        return GeneratedImage(
            scene_number=scene_number,
            url=image_url,
            prompt=prompt,
            is_end_frame=is_end_frame
        )

    def _build_reference_prompt(
        self,
        script: Script,
        user_style_info: str,
        aspect_ratio: str,
    ) -> str:
        """构建参考图库提示词 - 无背景角色大半身正面像（膝盖以上）"""
        prompt_parts = []

        # 添加画面比例
        prompt_parts.append(f"Aspect ratio: {aspect_ratio}")
        self._append_reference_style_guidance(prompt_parts, user_style_info, script)

        # 仅保留提炼后的参考图风格要点，不再直接拼接原始长文本
        if user_style_info:
            prompt_parts.append("[REFERENCE STYLE REQUIREMENTS]")
            prompt_parts.append(user_style_info)
        else:
            prompt_parts.append(f"Story style: {script.style}")

        # 【关键 - 无背景要求】
        prompt_parts.append("[CRITICAL - NO BACKGROUND] Pure white/transparent background ONLY")
        prompt_parts.append("[CRITICAL] No environment, no scenery, no background elements whatsoever")
        prompt_parts.append("[CRITICAL] Character isolated on plain white background")

        # 添加角色描述（根据角色数量决定）
        num_characters = len(script.characters) if script.characters else 0
        if num_characters > 0:
            if num_characters == 1:
                # 只有一个角色
                char = script.characters[0]
                prompt_parts.append("Main character reference image (front view, large half body above the knees):")
                prompt_parts.append("[CRITICAL] Show exactly ONE character only")
                prompt_parts.append("[CRITICAL] Do NOT duplicate the character, show only ONE instance")
                char_desc = f"{char.name}: {char.age} {char.gender}, {char.face_features}, {char.skin_tone}"
                prompt_parts.append(char_desc)
                if char.clothing:
                    prompt_parts.append(f"Wearing: {char.clothing}")
            else:
                # 多个角色
                prompt_parts.append(f"Main characters reference image (front view, large half body above the knees):")
                prompt_parts.append(f"[CRITICAL] Show exactly {num_characters} different main characters")
                prompt_parts.append("[CRITICAL] Each character should be distinct and unique, NO duplicates")
                for i, char in enumerate(script.characters, 1):
                    char_desc = f"Character {i} - {char.name}: {char.age} {char.gender}, {char.face_features}, {char.skin_tone}"
                    prompt_parts.append(char_desc)
                    if char.clothing:
                        prompt_parts.append(f"  Wearing: {char.clothing}")

        prompt_parts.append("Front view, facing camera, clear facial features")
        prompt_parts.append("Large half-body portrait above the knees, face occupying a significant portion of the frame")
        prompt_parts.append("Crop above the knees and keep the face details prominent")
        prompt_parts.append("Consistent character design, standalone figures on white background")
        prompt_parts.append("This is a stylized reference image for subsequent scene generation")
        prompt_parts.append("High quality, detailed, 8k resolution, white background")

        return ", ".join(prompt_parts)

    def _build_end_frame_prompt(
        self,
        script: Script,
        user_style_info: str,
        aspect_ratio: str,
        reference_image_url: str = None,
    ) -> str:
        """构建结尾帧提示词"""
        prompt_parts = []

        # 添加画面比例
        prompt_parts.append(f"Aspect ratio: {aspect_ratio}")

        # 【最高优先级 - 严格使用用户风格】
        if user_style_info:
            prompt_parts.append(f"[CRITICAL - STRICT] Style: {user_style_info}")
            prompt_parts.append("[RULE] Use ONLY the above style for the ending frame")
            prompt_parts.append("[RULE] DO NOT mix with other styles")
        else:
            prompt_parts.append(f"Story style: {script.style}")

        # 【关键 - 使用参考图角色】
        if reference_image_url:
            prompt_parts.append("\n[CRITICAL - USE REFERENCE IMAGE CHARACTERS]")
            prompt_parts.append("[CRITICAL] The reference image shows the MAIN CHARACTERS - USE THESE EXACTLY")
            prompt_parts.append("[CRITICAL] Include the characters from the reference image in the ending scene")
            prompt_parts.append("[CRITICAL] Use the EXACT facial features, clothing, and appearance from the reference image")
            prompt_parts.append("[CRITICAL] Characters must match the reference image perfectly")

        # 根据剧本风格生成结尾画面
        prompt_parts.append(f"Ending scene for: {script.title}")
        prompt_parts.append(f"Background: {script.background}")

        # 生成带有"The End"的结尾画面
        prompt_parts.append("A cinematic closing title card with 'The End' text")
        prompt_parts.append("Elegant typography, centered text, movie poster style")
        prompt_parts.append("The text 'The End' is clearly visible and prominent")
        prompt_parts.append("Beautiful background matching the story theme and specified style")
        prompt_parts.append("Professional film ending credits style")

        prompt_parts.append("High quality, 8k resolution, cinematic lighting")

        return ", ".join(prompt_parts)

    def _build_reference_regeneration_requirements(
        self,
        has_user_reference_images: bool,
        feedback: str = None,
    ) -> str:
        """构建参考图重新生成时的多样化约束。"""
        variation_groups = [
            [
                "change the pose significantly while keeping the character identity",
                "use a clearly different body gesture and hand movement",
            ],
            [
                "change the facial expression noticeably",
                "use a different emotional state and eye expression",
            ],
            [
                "change the camera framing and composition",
                "use a different crop, distance, or character placement",
            ],
            [
                "change the outfit styling details without changing the core identity",
                "use different clothing combinations or accessory emphasis while preserving identity",
            ],
            [
                "change the lighting and color mood",
                "use a different light direction and highlight treatment",
            ],
        ]
        chosen_variations = [random.choice(group) for group in random.sample(variation_groups, k=3)]

        prompt_parts = [
            "Create a clearly fresh alternative reference image instead of repeating the last attempt.",
            "Make the new result noticeably different in visual presentation.",
            "Apply these diversity directions:",
        ]
        prompt_parts.extend(f"- {item}" for item in chosen_variations)

        if has_user_reference_images:
            prompt_parts.extend([
                "- Base the character ONLY on the user-uploaded source images.",
                "- Preserve the identity, face, body shape, hairstyle, and signature features from the uploaded images.",
                "- You may change pose, expression, composition, lighting, and styling details, but do not replace the character.",
            ])
        else:
            prompt_parts.extend([
                "- Generate from text description ONLY.",
                "- Do NOT use any previous generated image as reference.",
                "- Do NOT try to match the previous generated image.",
                "- Build a fresh character presentation purely from the written description and style requirements.",
            ])

        if feedback:
            prompt_parts.extend([
                "- Additional user feedback to follow:",
                feedback,
            ])

        return "\n".join(prompt_parts)

    def regenerate_reference_image(
        self,
        script: Script,
        feedback: str,
        user_style_info: str = None,
        aspect_ratio: str = None,
        user_reference_images: List[str] = None,
    ) -> GeneratedImage:
        """重新生成参考图库，并显式提高多样性。"""
        if not aspect_ratio:
            aspect_ratio = self.default_aspect_ratio

        if isinstance(user_reference_images, str):
            user_reference_images = [user_reference_images]

        variation_requirements = self._build_reference_regeneration_requirements(
            has_user_reference_images=bool(user_reference_images),
            feedback=feedback,
        )

        logger.log_agent_call("ImageAgent", "regenerate_reference_image", {
            "aspect_ratio": aspect_ratio,
            "has_user_reference_images": bool(user_reference_images),
            "reference_image_count": len(user_reference_images) if user_reference_images else 0,
        })

        return self.generate_reference_image(
            script=script,
            user_style_info=user_style_info,
            aspect_ratio=aspect_ratio,
            user_reference_images=user_reference_images,
            variation_requirements=variation_requirements,
        )

    def regenerate_character_reference_image(
        self,
        character: Character,
        script: Script,
        feedback: str,
        user_style_info: str = None,
        aspect_ratio: str = None,
        user_reference_images: List[str] = None,
    ) -> GeneratedImage:
        variation_requirements = self._build_reference_regeneration_requirements(
            has_user_reference_images=bool(user_reference_images),
            feedback=feedback,
        )
        return self.generate_character_reference_image(
            character=character,
            script=script,
            user_style_info=user_style_info,
            aspect_ratio=aspect_ratio,
            user_reference_images=user_reference_images,
            variation_requirements=variation_requirements,
        )

    def regenerate_scene_reference_image(
        self,
        scene_name: str,
        scene_description: str,
        script: Script,
        feedback: str,
        user_style_info: str = None,
        aspect_ratio: str = None,
        user_reference_images: List[str] = None,
    ) -> GeneratedImage:
        variation_requirements = self._build_reference_regeneration_requirements(
            has_user_reference_images=bool(user_reference_images),
            feedback=feedback,
        )
        return self.generate_scene_reference_image(
            scene_name=scene_name,
            scene_description=scene_description,
            script=script,
            user_style_info=user_style_info,
            aspect_ratio=aspect_ratio,
            user_reference_images=user_reference_images,
            variation_requirements=variation_requirements,
        )

    def regenerate_image(
        self,
        scene_number: int,
        script: Script,
        feedback: str,
        user_style_info: str = None,
        aspect_ratio: str = None,
        reference_image_url: str = None,
    ) -> GeneratedImage:
        """
        根据反馈重新生成某一场景的图片
        """
        if not aspect_ratio:
            aspect_ratio = self.default_aspect_ratio

        logger.log_agent_call("ImageAgent", "regenerate_image", {
            "scene_number": scene_number,
            "feedback": feedback,
            "aspect_ratio": aspect_ratio,
            "has_reference": bool(reference_image_url)
        })

        if scene_number == 0:
            logger.info("Scene 0 regeneration delegated to regenerate_reference_image")
            return self.regenerate_reference_image(
                script=script,
                feedback=feedback,
                user_style_info=user_style_info,
                aspect_ratio=aspect_ratio,
                user_reference_images=[reference_image_url] if reference_image_url else None,
            )

        else:
            # 其他场景编号（包括999结尾帧）不再支持重新生成
            # 因为分镜图和结尾帧生成功能已废弃
            logger.warning(f"Scene {scene_number} regeneration not supported - feature deprecated")
            raise ValueError(f"Scene {scene_number} regeneration not supported. Only reference image (scene 0) can be regenerated.")
