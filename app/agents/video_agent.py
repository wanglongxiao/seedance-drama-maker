# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.
import time
import random
import re
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.config import config
from app.prompt_skill import load_optional_nsfw_prompt, load_prompt, nsfw_content_requested
from app.services.llm_service import llm_service
from app.utils.logger import get_logger
from app.models.schemas import Script, GeneratedImage, GeneratedVideo

logger = get_logger("video_agent")


class VideoAgent:
    """视频生成分镜Agent - 调用seedance-2.0-off"""

    def __init__(self):
        self.model = config.get('models.video.endpoint')
        self.poll_interval = config.get('limits.video_poll_interval', 20)
        # 视频生成最大等待时间：读取配置，默认 900 秒（15分钟）
        self.max_wait_time = int(config.get('limits.video_max_wait_time', 900))
        # 从 yaml 配置读取分镜时长范围（当无法从剧本解析时使用）
        self.default_duration_min = config.get('video_generation.scene_duration.min', 10)
        self.default_duration_max = config.get('video_generation.scene_duration.max', 30)
        # 视频分辨率 (480p, 720p)，默认使用 480p
        self.default_resolution = config.get('video_generation.default_resolution', '480p')
        self.resolution_options = config.get('video_generation.resolution_options', ['480p', '720p'])
        # 平滑转场提示词
        self.transition_prompt = config.get('video_generation.transition_prompt', '平滑的转场，自然流畅，无明显跳帧或卡顿')
        # 并发配置
        self.concurrency_enabled = config.get('generation.concurrency.enabled', True)
        self.max_workers = int(
            config.get(
                'video_generation.scene_max_concurrency',
                config.get('generation.concurrency.video_workers', 10)
            )
        )
        # 参考图URL（用于人物一致性）
        self.reference_image_url = None

    def _sanitize_video_private_prompt(self, text: str) -> str:
        """Remove private prompt metadata and example timelines that can conflict with the current scene."""
        if not text:
            return ""
        kept_lines: List[str] = []
        skipping_example = False
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                if not skipping_example:
                    kept_lines.append(raw_line)
                continue
            if re.search(r"^示例[:：]?$|^#+\s*示例|^\d+\s*-\s*\d+\s*秒", line):
                skipping_example = True
                continue
            if skipping_example:
                continue
            if re.search(r"本地私有扩展|NSFW\\?_?ENABLED|公开仓库|关闭.*跳过|该文件|仅用于.*本地私有", line):
                continue
            if "图片：" in line or "图片：" in raw_line or "图片：" in line.replace(" ", ""):
                continue
            if "图片" in line and ("增加" in line or "尝试" in line):
                continue
            kept_lines.append(raw_line)
        return "\n".join(kept_lines).strip()

    def _append_video_private_extensions(
        self,
        prompt_parts: List[str],
        names: List[str],
        *trigger_texts,
    ) -> None:
        """Append bounded private video extensions only when enabled and adult content is requested."""
        if not nsfw_content_requested(*trigger_texts):
            return
        extensions: List[str] = []
        for name in names:
            text = self._sanitize_video_private_prompt(load_optional_nsfw_prompt(name))
            if text:
                extensions.append(text)
        if extensions:
            prompt_parts.extend([
                "",
                "【本地私有视频扩展 - 限定优先级】",
                "以下内容仅作为当前分镜的视频动作节奏、视角或互动细节补充；不得覆盖参考图顺序、角色身份、布景状态、故事版顺序、关键动作参考图、无字幕、音色一致、镜头时长、背景音乐限制或当前分镜文本。",
                "\n\n".join(extensions),
            ])

    def _parse_resolution(self, user_input: str) -> str:
        """
        从用户输入中解析分辨率

        Args:
            user_input: 用户输入文本

        Returns:
            分辨率字符串 (480p/720p/1080p)，如果未找到或无效则返回默认分辨率
        """
        if not user_input:
            return self.default_resolution

        input_lower = str(user_input).lower().replace('：', ':')
        valid_resolutions = [str(item).strip().lower() for item in (self.resolution_options or []) if str(item).strip()]
        if self.default_resolution not in valid_resolutions:
            valid_resolutions.append(self.default_resolution)

        for resolution in valid_resolutions:
            if resolution in input_lower:
                logger.info(f"Resolution '{resolution}' found in user input")
                return resolution

        logger.info(f"No valid resolution found in user input, using default: {self.default_resolution}")
        return self.default_resolution

    def _extract_video_visual_style(self, user_style_info: Optional[str]) -> str:
        """Keep only visual style/aspect-ratio phrases from user input for the video style line."""
        text = str(user_style_info or "").strip()
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text)
        segments = re.split(r"[\n，,、；;。！？]+", text)
        visual_segments: List[str] = []
        visual_keywords = [
            "风格", "樣式", "样式", "style", "比例", "aspect ratio",
            "真人", "写实", "寫實", "电影", "電影", "电影感", "電影感",
            "live action", "live-action", "realistic", "photoreal", "cinematic",
            "日漫", "动漫", "動畫", "anime", "cartoon", "水墨", "油画", "油畫", "水彩", "像素",
        ]
        non_visual_keywords = [
            "对白", "對白", "旁白", "台词", "臺詞", "语音", "声音", "音色",
            "时长", "時長", "秒", "分钟", "分鐘", "auto", "自动", "自動",
            "bgm", "music", "dialogue", "narration", "voice", "duration",
        ]
        for segment in segments:
            cleaned = segment.strip(" \t,，。；;:-")
            if not cleaned:
                continue
            lowered = cleaned.lower()
            is_aspect_ratio = bool(re.fullmatch(r"\d+\s*:\s*\d+", lowered))
            has_visual = is_aspect_ratio or any(keyword in lowered for keyword in visual_keywords)
            has_non_visual = any(keyword in lowered for keyword in non_visual_keywords)
            if has_visual and (is_aspect_ratio or not has_non_visual):
                visual_segments.append(cleaned)
        style_text = "，".join(dict.fromkeys(visual_segments))
        return style_text[:300]

    def _extract_video_dialogue_voice_requirements(
        self,
        *values: Optional[str],
    ) -> str:
        """Keep dialogue language and voice/timbre requirements out of the visual style line."""
        text = "，".join(str(value or "").strip() for value in values if str(value or "").strip())
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text)
        segments = re.split(r"[\n，,、；;。！？]+", text)
        requirement_segments: List[str] = []
        voice_keywords = [
            "对白", "對白", "旁白", "台词", "臺詞", "语言", "語言",
            "中文", "汉语", "漢語", "普通话", "普通話", "英文", "英语", "英語",
            "日文", "日语", "日語", "粤语", "粵語", "声音", "聲音", "音色",
            "声线", "聲線", "语气", "語氣", "口音", "配音", "呻吟", "喘息",
            "voice", "dialogue",
            "narration", "timbre", "tone of voice", "accent",
        ]
        duration_or_mode_keywords = [
            "时长", "時長", "秒", "分钟", "分鐘", "auto", "自动", "自動",
            "duration",
        ]
        for segment in segments:
            cleaned = segment.strip(" \t,，。；;:-")
            if not cleaned:
                continue
            lowered = cleaned.lower()
            has_voice = any(keyword in lowered for keyword in voice_keywords)
            has_duration_or_mode = any(keyword in lowered for keyword in duration_or_mode_keywords)
            if has_voice and not has_duration_or_mode:
                requirement_segments.append(cleaned)
        requirement_text = "，".join(dict.fromkeys(requirement_segments))
        return requirement_text[:500]

    def generate_videos(
        self,
        script: Script,
        reference_image: GeneratedImage,
        user_style_info: str = None,
        resolution: str = None,
    ) -> List[GeneratedVideo]:
        """
        为每个分镜生成视频（支持并发）- 简化版本

        根据chatbot用户补充的描述信息（尤其是风格、场景、其他描述信息）
        与剧本分镜台词生成agent输出的单一分镜文本（包括描述、对话、时长）、
        结合角色特征、语气、音色描述等作为提示词生成视频。

        新流程（简化版）：
        - 直接使用参考图库中的角色图作为角色参考
        - 根据分镜脚本直接生成视频，无需先生成首帧图
        - 移除尾帧图生成步骤

        Args:
            script: 剧本对象
            reference_image: 参考图库角色图 - 角色形象参考
            user_style_info: 用户补充的风格和场景描述信息

        Returns:
            生成的视频片段列表
        """
        resolution = (resolution or "").strip().lower() or self._parse_resolution(user_style_info)

        # 保存参考图URL用于人物一致性
        self.reference_image_url = self._get_generation_reference_url(reference_image)

        logger.log_agent_call("VideoAgent", "generate_videos", {
            "num_scenes": len(script.scenes),
            "has_user_style_info": bool(user_style_info),
            "concurrency_enabled": self.concurrency_enabled,
            "max_workers": self.max_workers,
            "resolution": resolution
        })

        num_scenes = len(script.scenes)

        logger.info(f"Generating {num_scenes} videos using reference image for character consistency")
        logger.info(f"Reference library image: {self.reference_image_url}")

        # 准备所有生成任务
        generation_tasks = []
        for i, scene in enumerate(script.scenes):
            # 获取分镜时长（12-15秒）
            duration = self._get_scene_duration(scene)

            # 构建视频生成提示词
            prompt = self._build_video_prompt(
                scene=scene,
                characters=script.characters,
                scene_definitions=script.scene_definitions,
                user_style_info=user_style_info,
                duration=duration,
                scene_index=i,
                total_scenes=num_scenes,
            )

            generation_tasks.append({
                'scene_number': i + 1,
                'scene': scene,
                'duration': duration,
                'prompt': prompt,
                'resolution': resolution,
                'reference_image_url': self.reference_image_url,
            })

            logger.info(f"Scene {i+1}: duration={duration}s, using reference image for character consistency")

        logger.info(f"Generating {len(generation_tasks)} videos with concurrency={self.concurrency_enabled}")

        # 执行生成（并发或串行）
        if self.concurrency_enabled and len(generation_tasks) > 1:
            videos = self._generate_videos_concurrent(generation_tasks)
        else:
            videos = self._generate_videos_sequential(generation_tasks)

        # 按scene_number排序
        videos.sort(key=lambda x: x.scene_number)

        logger.info(f"Total videos generated: {len(videos)}")
        return videos

    def _generate_videos_concurrent(self, tasks: List[Dict]) -> List[GeneratedVideo]:
        """并发生成视频"""
        videos = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_task = {
                executor.submit(self._generate_single_video, task): task
                for task in tasks
            }

            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    video = future.result()
                    videos.append(video)
                    logger.info(f"Video generated for scene {task['scene_number']}")
                except Exception as e:
                    logger.error(f"Failed to generate video for scene {task['scene_number']}: {str(e)}")
                    raise

        return videos

    def _generate_videos_sequential(self, tasks: List[Dict]) -> List[GeneratedVideo]:
        """串行生成视频"""
        videos = []

        for task in tasks:
            try:
                video = self._generate_single_video(task)
                videos.append(video)
                logger.info(f"Video generated for scene {task['scene_number']}")
            except Exception as e:
                logger.error(f"Failed to generate video for scene {task['scene_number']}: {str(e)}")
                raise

        return videos

    def generate_video_with_previous(
        self,
        scene,
        scene_index: int,
        total_scenes: int,
        project_id: str,
        reference_image: GeneratedImage,
        reference_images: Optional[List[GeneratedImage]] = None,
        previous_video_url: str = None,
        user_style_info: str = None,
        user_requirement_text: str = None,
        resolution: str = None,
        aspect_ratio: str = None,
        previous_scene=None,
        characters=None,
        scene_definitions=None,
        asset_group_id: Optional[str] = None,
        asset_project_name: Optional[str] = None,
    ) -> GeneratedVideo:
        """
        基于前一个视频生成延伸视频

        新流程：
        - 首分镜：使用参考图生成
        - 后续分镜：使用前一个视频 + 参考图 + 剧本生成延伸视频

        Args:
            scene: 当前分镜
            scene_index: 当前分镜索引
            total_scenes: 总分镜数
            project_id: 项目ID
            reference_image: 参考图
            previous_video_url: 前一个分镜视频URL
            user_style_info: 用户风格信息
            resolution: 分辨率

        Returns:
            生成的视频
        """
        scene_number = scene_index + 1
        prepared_reference_images = self._prepare_reference_images_for_generation(
            reference_images=reference_images,
            fallback_reference_image=reference_image,
        )
        reference_urls = self._dedupe_reference_urls(prepared_reference_images, None)
        reference_image_url = self._primary_reference_source_url(prepared_reference_images, None)

        if not reference_image_url:
            raise ValueError(f"Scene {scene_number}: reference_image_url is required")

        if not resolution:
            resolution = self._parse_resolution(user_style_info)
        resolution = str(resolution).strip().lower()
        aspect_ratio = aspect_ratio or "9:16"

        # 获取分镜时长
        duration = self._get_scene_duration(scene)

        # 构建视频生成提示词
        prompt = self._build_video_prompt(
            scene=scene,
            characters=characters,
            scene_definitions=scene_definitions,
            reference_images=prepared_reference_images,
            user_style_info=user_style_info,
            user_requirement_text=user_requirement_text,
            duration=duration,
            scene_index=scene_index,
            total_scenes=total_scenes,
            previous_video_url=previous_video_url,
        )

        logger.info(
            f"Scene {scene_number}: Generating video with reference images"
            f"{' + previous-scene video (extend mode)' if previous_video_url else ''}"
        )

        task_id = self._create_video_task_with_references(
            prompt=prompt,
            reference_image_urls=reference_urls,
            duration=duration,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            previous_video_url=previous_video_url,
        )

        logger.info(f"Scene {scene_number}: Created video task, task_id={task_id}")

        video_result = self._wait_for_video(task_id)

        return GeneratedVideo(
            scene_number=scene_number,
            url=video_result["video_url"],
            first_frame_url=reference_image_url,
            last_frame_url=None,
            duration=duration,
            prompt=prompt,
            seed=video_result.get("seed"),
        )

    def _dedupe_reference_urls(
        self,
        reference_images: Optional[List[GeneratedImage]],
        fallback_reference_image: Optional[GeneratedImage] = None,
    ) -> List[str]:
        urls: List[str] = []
        for image in reference_images or []:
            url = self._get_generation_reference_url(image)
            if url and url not in urls:
                urls.append(url)
        fallback_url = self._get_generation_reference_url(fallback_reference_image)
        if fallback_url and fallback_url not in urls:
            urls.insert(0, fallback_url)
        return urls

    def _get_generation_reference_url(self, image: Optional[GeneratedImage]) -> Optional[str]:
        if image is None:
            return None
        asset_id = str(getattr(image, "asset_id", "") or "").strip()
        if not asset_id:
            reference_type = str(getattr(image, "reference_type", "") or "reference").strip().lower()
            name = str(getattr(image, "name", "") or getattr(image, "url", "") or reference_type)
            raise ValueError(f"Reference image is missing asset_id: {reference_type} {name}")
        return f"asset://{asset_id}"

    def _primary_reference_source_url(
        self,
        reference_images: Optional[List[GeneratedImage]],
        fallback_reference_image: Optional[GeneratedImage] = None,
    ) -> Optional[str]:
        for image in reference_images or []:
            source_url = getattr(image, "url", None)
            if source_url:
                return source_url
        return getattr(fallback_reference_image, "url", None)

    def _create_video_task_with_references(
        self,
        prompt: str,
        reference_image_urls: List[str],
        duration: int,
        resolution: str,
        aspect_ratio: str,
        previous_video_url: str = None,
    ) -> str:
        content = [{"type": "text", "text": prompt}]
        for url in reference_image_urls:
            if not str(url).startswith("asset://"):
                raise ValueError(f"Video generation only accepts asset references, got: {url}")
            content.append({
                "type": "image_url",
                "role": "reference_image",
                "image_url": {"url": url},
            })
        # 延长模式：附加前一分镜的生成视频作为 reference_video，保证画面连续过渡。
        if previous_video_url:
            content.append({
                "type": "video_url",
                "role": "reference_video",
                "video_url": {"url": previous_video_url},
            })
        return llm_service.create_video_task_with_content(
            model=self.model,
            content=content,
            duration=duration,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
        )

    def _prepare_reference_images_for_generation(
        self,
        reference_images: Optional[List[GeneratedImage]],
        fallback_reference_image: Optional[GeneratedImage] = None,
    ) -> List[GeneratedImage]:
        prepared: List[GeneratedImage] = []

        deduped_images = []
        seen_urls = set()
        for image in reference_images or []:
            url = getattr(image, "url", None)
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            deduped_images.append(image)

        fallback_url = getattr(fallback_reference_image, "url", None)
        if fallback_url and fallback_url not in seen_urls:
            deduped_images.insert(0, fallback_reference_image)

        prepared.extend(deduped_images)
        return prepared

    def _split_scene_name_keys(self, scene_name: str) -> List[str]:
        keys: List[str] = []
        seen = set()
        for part in re.split(r"[、,，/|]+", str(scene_name or "")):
            key = re.sub(r"\s+", "", part.strip().lower())
            key = re.sub(r"[^0-9a-z\u4e00-\u9fff_-]+", "", key)
            if not key or key in seen:
                continue
            seen.add(key)
            keys.append(key)
        return keys

    def _generate_single_video(self, task: Dict) -> GeneratedVideo:
        """生成单个视频 - 使用 reference_image 保持人物一致性"""
        scene_number = task['scene_number']
        reference_image_url = task.get('reference_image_url')

        if not reference_image_url:
            raise ValueError(f"Scene {scene_number}: reference_image_url is required for character consistency")

        # 获取分辨率（从任务或默认）
        resolution = task.get('resolution', self.default_resolution)

        logger.info(f"Scene {scene_number}: Creating video task with reference image (resolution: {resolution})")
        logger.info(f"  Reference image: {reference_image_url[:100]}...")

        # 使用 seedance-2.0 API 格式，使用 reference_image 角色参考
        task_id = llm_service.create_video_task(
            prompt=task['prompt'],
            reference_image_url=reference_image_url,
            model=self.model,
            duration=task['duration'],
            resolution=resolution,
        )

        logger.info(f"Scene {scene_number}: Created video task with reference image, task_id={task_id}")

        video_result = self._wait_for_video(task_id)

        return GeneratedVideo(
            scene_number=scene_number,
            url=video_result["video_url"],
            first_frame_url=reference_image_url,  # 记录参考图URL
            last_frame_url=None,
            duration=task['duration'],
            prompt=task['prompt'],
            seed=video_result.get("seed"),
        )

    def _get_scene_duration(self, scene) -> int:
        """
        获取分镜时长

        优先从剧本分镜的duration字段获取（由ScriptAgent根据yaml配置生成），
        如果解析不到或无效，则根据yaml配置的分镜时长范围随机生成
        有效范围：由配置决定，当前默认 10-30 秒
        """
        try:
            if hasattr(scene, 'duration') and scene.duration is not None:
                duration = int(scene.duration)
                # 检查时长是否在有效范围内（由配置决定）
                if self.default_duration_min <= duration <= self.default_duration_max:
                    logger.info(f"Using scene duration from script: {duration}s")
                    return duration
                else:
                    logger.warning(f"Scene duration {duration}s out of range ({self.default_duration_min}-{self.default_duration_max}s), using random")
            else:
                logger.warning(f"No duration found in scene, using random duration")
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse scene duration: {str(e)}, using random")

        random_duration = random.randint(self.default_duration_min, self.default_duration_max)
        logger.info(f"Generated random duration: {random_duration}s (range: {self.default_duration_min}-{self.default_duration_max}s)")
        return random_duration

    def _build_video_prompt(
        self,
        scene,
        characters,
        scene_definitions=None,
        reference_images: Optional[List[GeneratedImage]] = None,
        user_style_info: str = None,
        user_requirement_text: str = None,
        duration: int = 12,
        scene_index: int = 0,
        total_scenes: int = 1,
        previous_video_url: str = None,
    ) -> str:
        """
        构建视频生成提示词 - 整合完整的分镜信息

        结合用户补充的风格/场景描述信息和剧本分镜的完整文本
        （包括描述、对话、时长、角色动作、声音描述、情绪、镜头角度、出场角色等）
        """
        parts = []

        reference_specs = self._build_reference_specs(reference_images)
        if reference_specs:
            # 角色装扮图归入角色组，布景状态图归入场景组
            character_tags = ''.join(spec["tag"] for spec in reference_specs if spec["reference_type"] in {"character", "character_outfit"})
            scene_tags = ''.join(spec["tag"] for spec in reference_specs if spec["reference_type"] in {"scene", "scene_state"})
            key_action_tags = ''.join(spec["tag"] for spec in reference_specs if spec["reference_type"] == "key_action")
            storyboard_tags = ''.join(spec["tag"] for spec in reference_specs if spec["reference_type"] == "storyboard")

            if character_tags and scene_tags:
                parts.append(f"结合{character_tags}人物/角色参考图中的出场角色形象与布景设定{scene_tags}生成当前分镜。")
            elif character_tags:
                parts.append(f"结合{character_tags}人物/角色参考图中的出场角色形象生成当前分镜。")
            elif scene_tags:
                parts.append(f"结合布景设定{scene_tags}生成当前分镜。")
            if storyboard_tags:
                parts.append(f"严格参考{storyboard_tags}中的9宫格白描线稿分镜故事版图片，按照故事版图的顺序，运镜合理连贯，遵循其节奏、镜头拆分和动作推进。")
            if key_action_tags:
                parts.append(f"重点参考{key_action_tags}中的关键动作参考图，保持该分镜的核心动作姿态、人物相对位置、镜头构图和情绪张力。")
            mapping_parts = []
            for spec in reference_specs:
                mapping_parts.append(
                    f'{spec["tag"]}={spec["name"]}（{self._reference_type_label(spec["reference_type"])}）'
                )
            parts.append(f"从参考图片顺序：{'，'.join(mapping_parts)}")

        # 延长模式：上一分镜的生成视频仅用于保持角色形象/服装/场景/光线的一致性，
        # 但当前分镜是一段“全新镜头”，必须立刻推进到本分镜的新剧情，
        # 严禁把上一分镜结尾的画面、构图、动作原样回放/复制到开头（这会导致首帧与前半段雷同）。
        if previous_video_url:
            parts.append(load_prompt("video_extend_continuity.md"))

        visual_style = self._extract_video_visual_style(user_style_info)
        if visual_style:
            parts.append(f"视觉风格：{visual_style}")
        dialogue_voice_requirements = self._extract_video_dialogue_voice_requirements(
            user_style_info,
            user_requirement_text,
        )
        if dialogue_voice_requirements:
            parts.append(f"对白与声音要求：{dialogue_voice_requirements}")

        scene_name = getattr(scene, 'scene_name', '') or ''
        if scene_name:
            scene_name_with_tags = self._annotate_scene_names_with_reference_tags(scene_name, reference_specs)
            parts.append(f"使用布景：{scene_name_with_tags}")
        scene_context = self._build_scene_context(scene, scene_definitions)
        if scene_context["descriptions"]:
            parts.append(f"布景定义：{'；'.join(scene_context['descriptions'])}")
        if scene_context["scene_features"]:
            parts.append(f"稳定场景特征：{', '.join(scene_context['scene_features'])}")
        scene_state = str(getattr(scene, "scene_state", "") or "").strip()
        if scene_state:
            parts.append(f"本分镜布景状态：{scene_state}")
        if scene_context["time_of_day"]:
            parts.append(f"时间信息：{scene_context['time_of_day']}")
        if scene_context["weather"]:
            parts.append(f"天气信息：{scene_context['weather']}")
        scene_outfits = getattr(scene, "character_outfits", None) or {}
        if scene_outfits:
            outfit_lines = [
                f"{name}={outfit}"
                for name, outfit in scene_outfits.items()
                if str(name or "").strip() and str(outfit or "").strip()
            ]
            if outfit_lines:
                parts.append(f"本分镜角色装扮（含发型）：{'；'.join(outfit_lines)}")
        parts.append(f"场景描述：{getattr(scene, 'description', '')}")
        parts.append(load_prompt("video_scene_static_rules.md"))

        dialogue = getattr(scene, 'dialogue', '') or ''
        parts.append(f"对白/旁白：{dialogue if dialogue else '无'}")

        character_desc = getattr(scene, 'character_description', '') or ''
        if character_desc:
            parts.append(f"角色动作：{character_desc}")

        mood = getattr(scene, 'mood', '') or ''
        if mood:
            parts.append(f"情绪氛围：{mood}")
        voice_description = getattr(scene, 'voice_description', '') or ''
        if voice_description:
            parts.append(f"声音状态：{voice_description}")

        camera = getattr(scene, 'camera_angle', None) or ''
        if camera:
            parts.append(f"镜头角度：{camera}")

        chars_present = getattr(scene, 'characters_present', None) or []
        if isinstance(chars_present, list) and chars_present:
            parts.append(f"出场角色：{self._annotate_character_names_with_reference_tags(chars_present, reference_specs)}")
            parts.extend(self._build_scene_character_details(chars_present, characters, reference_specs))

        parts.append(f"镜头时长：{duration}秒。当前分镜序号：{scene_index + 1}/{total_scenes}。")

        self._append_video_private_extensions(
            parts,
            ["video_action_closeup.md", "video_enhancement.md"],
            user_requirement_text,
            user_style_info,
            getattr(scene, "description", ""),
            getattr(scene, "character_description", ""),
            getattr(scene, "dialogue", ""),
            getattr(scene, "mood", ""),
        )

        parts.append(self._extract_background_music_instruction(user_requirement_text))

        return "\n".join(parts)

    def _extract_background_music_instruction(self, user_requirement_text: Optional[str]) -> str:
        text = str(user_requirement_text or "").strip()
        if not text:
            return "限定：不生成背景音乐。"

        normalized = text.replace("：", ":")
        music_patterns = [
            r'(?:背景音乐|配乐|bgm|background music|music)\s*[:：]?\s*([^\n，。；;]+)',
            r'([^\n，。；;]{0,20}(?:背景音乐|配乐|bgm|background music|music)[^\n，。；;]{0,30})',
        ]
        for pattern in music_patterns:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if match:
                music_hint = re.sub(r'\s+', ' ', match.group(1)).strip(" \t,，。；;")
                if music_hint:
                    return f"背景音乐要求：{music_hint}。"
        return "限定：不生成背景音乐。"

    def _reference_type_label(self, reference_type: str) -> str:
        if reference_type == "character":
            return "人物/角色"
        if reference_type == "character_outfit":
            return "人物/角色装扮"
        if reference_type == "scene":
            return "场景"
        if reference_type == "scene_state":
            return "布景状态"
        if reference_type == "key_action":
            return "关键动作参考图"
        if reference_type == "storyboard":
            return "9宫格 storyboard"
        return "参考"

    def _character_has_visual_reference(
        self,
        character_name: str,
        reference_specs: Optional[List[Dict[str, str]]],
    ) -> bool:
        character_key = self._normalize_name_key(character_name)
        if not character_key:
            return False
        for spec in reference_specs or []:
            if spec.get("reference_type") not in {"character", "character_outfit"}:
                continue
            ref_key = spec.get("name_key") or ""
            if ref_key == character_key or character_key in ref_key or ref_key in character_key:
                return True
        return False

    def _build_scene_character_details(
        self,
        character_names: List[str],
        characters,
        reference_specs: Optional[List[Dict[str, str]]] = None,
    ) -> List[str]:
        if not character_names or not characters:
            return []

        character_map = {
            self._normalize_name_key(getattr(character, "name", "")): character
            for character in characters or []
        }
        lines: List[str] = []
        for name in character_names:
            if self._character_has_visual_reference(name, reference_specs):
                continue
            character = character_map.get(self._normalize_name_key(name))
            if character is None:
                continue
            summary = [
                f"角色设定：{character.name}",
                f"年龄={getattr(character, 'age', '')}",
                f"性别={getattr(character, 'gender', '')}",
                f"国籍={getattr(character, 'nationality', '')}",
                f"外貌特征={getattr(character, 'face_features', '')}",
                f"发型={getattr(character, 'hairstyle', '')}",
                f"身材特征={getattr(character, 'body_features', '')}",
                f"肤色={getattr(character, 'skin_tone', '')}",
            ]
            if getattr(character, "clothing", None):
                summary.append(f"通常装扮={character.clothing}")
            if getattr(character, "personality", None):
                summary.append(f"性格特征={character.personality}")
            if getattr(character, "identity_background", None):
                summary.append(f"身份背景={character.identity_background}")
            lines.append("，".join([item for item in summary if item.split('=', 1)[-1]]))
        return lines

    def _build_scene_context(self, scene, scene_definitions) -> Dict[str, str]:
        descriptions: List[str] = []
        scene_features: List[str] = []
        seen_descriptions = set()
        seen_features = set()
        time_of_day = str(getattr(scene, "time_of_day", "") or "").strip()
        weather = str(getattr(scene, "weather", "") or "").strip()
        scene_name_keys = set(self._split_scene_name_keys(getattr(scene, "scene_name", "")))

        for item in scene_definitions or []:
            item_name = getattr(item, "name", None) if not isinstance(item, dict) else item.get("name")
            if self._normalize_name_key(item_name) not in scene_name_keys:
                continue

            description = str(
                getattr(item, "description", None) if not isinstance(item, dict) else item.get("description") or ""
            ).strip()
            if description and description not in seen_descriptions:
                seen_descriptions.add(description)
                descriptions.append(description)

            features = getattr(item, "scene_features", None) if not isinstance(item, dict) else item.get("scene_features")
            for feature in features or []:
                feature_text = str(feature or "").strip()
                if feature_text and feature_text not in seen_features:
                    seen_features.add(feature_text)
                    scene_features.append(feature_text)

            if not time_of_day:
                time_of_day = str(
                    getattr(item, "time_of_day", None) if not isinstance(item, dict) else item.get("time_of_day") or ""
                ).strip()
            if not weather:
                weather = str(
                    getattr(item, "weather", None) if not isinstance(item, dict) else item.get("weather") or ""
                ).strip()

        return {
            "descriptions": descriptions,
            "scene_features": scene_features,
            "scene_state": str(getattr(scene, "scene_state", "") or "").strip(),
            "time_of_day": time_of_day,
            "weather": weather,
        }

    def _build_reference_specs(self, reference_images: Optional[List[GeneratedImage]]) -> List[Dict[str, str]]:
        specs: List[Dict[str, str]] = []
        for index, image in enumerate(reference_images or [], start=1):
            name = str(getattr(image, "name", "") or f"参考图{index}").strip()
            reference_type = str(getattr(image, "reference_type", "") or "").strip().lower() or "character"
            specs.append({
                "tag": f"<图片{index}>",
                "name": name,
                "name_key": self._normalize_name_key(name),
                "reference_type": reference_type,
            })
        return specs

    def _normalize_name_key(self, value: str) -> str:
        normalized = re.sub(r"\s+", "", str(value or "").strip().lower())
        normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff_-]+", "", normalized)
        return normalized

    def _annotate_character_names_with_reference_tags(
        self,
        character_names: List[str],
        reference_specs: List[Dict[str, str]],
    ) -> str:
        character_map = {
            spec["name_key"]: spec["tag"]
            for spec in reference_specs
            if spec["reference_type"] == "character"
        }
        annotated = []
        for name in character_names:
            clean_name = str(name or "").strip()
            tag = character_map.get(self._normalize_name_key(clean_name))
            annotated.append(f"{clean_name}{tag or ''}")
        return ", ".join([item for item in annotated if item])

    def _annotate_scene_names_with_reference_tags(
        self,
        scene_names: str,
        reference_specs: List[Dict[str, str]],
    ) -> str:
        scene_map = {
            spec["name_key"]: spec["tag"]
            for spec in reference_specs
            if spec["reference_type"] == "scene"
        }
        parts = []
        for part in re.split(r"([、,，/|])", str(scene_names or "")):
            stripped = str(part).strip()
            if not stripped:
                continue
            if stripped in {"、", ",", "，", "/", "|"}:
                parts.append(stripped)
                continue
            tag = scene_map.get(self._normalize_name_key(stripped))
            parts.append(f"{stripped}{tag or ''}")
        return "".join(parts)

    def _wait_for_video(self, task_id: str) -> Dict[str, str]:
        """轮询等待视频生成完成，并提取最终视频 URL 与 seed。"""
        logger.info(f"Waiting for video task: {task_id}")

        elapsed = 0
        while elapsed < self.max_wait_time:
            result = llm_service.query_video_task(task_id)
            status = result.get('status')

            logger.info(f"Video task status: {status}, elapsed: {elapsed}s")

            if status == 'succeeded':
                video_result = llm_service.extract_video_result(result)
                video_url = video_result.get('video_url')
                if not video_url:
                    raise ValueError(f"Video generation succeeded but video_url is missing: {result}")
                logger.info(f"Video generation completed: {video_url}, seed={video_result.get('seed')}")
                return video_result

            elif status == 'failed':
                error_msg = f"Video generation failed: {result}"
                logger.error(error_msg)
                raise Exception(error_msg)

            time.sleep(self.poll_interval)
            elapsed += self.poll_interval

        raise TimeoutError(f"Video generation timeout after {self.max_wait_time}s")

    def regenerate_video(
        self,
        scene_number: int,
        script: Script,
        project_id: str,
        reference_image: GeneratedImage,
        feedback: str,
        reference_images: Optional[List[GeneratedImage]] = None,
        previous_video_url: str = None,
        user_style_info: str = None,
        user_requirement_text: str = None,
        resolution: str = None,
        aspect_ratio: str = None,
        characters=None,
        scene_definitions=None,
        asset_group_id: Optional[str] = None,
        asset_project_name: Optional[str] = None,
    ) -> GeneratedVideo:
        """
        根据反馈重新生成某个分镜的视频 - 简化版本

        Args:
            scene_number: 场景编号
            script: 剧本对象
            project_id: 项目ID
            reference_image: 参考图库角色图 - 角色形象参考
            previous_video_url: 前一个分镜视频URL（非首分镜重生成时必需）
            feedback: 修改意见
            user_style_info: 用户补充的风格和场景描述信息

        Returns:
            新生成的视频
        """
        logger.log_agent_call("VideoAgent", "regenerate_video", {
            "scene_number": scene_number,
            "feedback": feedback,
            "has_user_style_info": bool(user_style_info),
            "has_previous_video_url": bool(previous_video_url),
        })

        num_scenes = len(script.scenes)
        scene = script.scenes[scene_number - 1]

        logger.info(f"Regenerating video for scene {scene_number}/{num_scenes}")
        logger.info(f"Using reference image: {reference_image.url if reference_image else 'None'}")

        # 获取分镜时长
        duration = self._get_scene_duration(scene)

        prepared_reference_images = self._prepare_reference_images_for_generation(
            reference_images=reference_images,
            fallback_reference_image=reference_image,
        )

        # 构建新的提示词
        prompt = self._build_video_prompt(
            scene=scene,
            characters=characters or script.characters,
            scene_definitions=scene_definitions or script.scene_definitions,
            reference_images=prepared_reference_images,
            user_style_info=user_style_info,
            user_requirement_text=user_requirement_text,
            duration=duration,
            scene_index=scene_number - 1,
            total_scenes=num_scenes,
            previous_video_url=previous_video_url,
        )
        prompt += f"。修改意见: {feedback}"

        reference_urls = self._dedupe_reference_urls(prepared_reference_images, None)
        reference_image_url = self._primary_reference_source_url(prepared_reference_images, None)
        if not reference_image_url:
            raise ValueError(f"Reference image is required for video regeneration")

        resolution = (resolution or "").strip().lower() or self._parse_resolution(user_style_info)
        aspect_ratio = aspect_ratio or "9:16"

        task_id = self._create_video_task_with_references(
            prompt=prompt,
            reference_image_urls=reference_urls,
            duration=duration,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            previous_video_url=previous_video_url,
        )

        video_result = self._wait_for_video(task_id)

        return GeneratedVideo(
            scene_number=scene_number,
            url=video_result["video_url"],
            first_frame_url=reference_image_url,
            last_frame_url=None,
            duration=duration,
            prompt=prompt,
            seed=video_result.get("seed"),
        )
