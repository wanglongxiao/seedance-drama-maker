# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.
import time
import random
import re
import uuid
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.config import config
from app.services.llm_service import llm_service
from app.services.ffmpeg_service import ffmpeg_service
from app.services.tos_service import tos_service
from app.utils.logger import get_logger
from app.utils.task_paths import ensure_project_temp_subdir
from app.models.schemas import Script, GeneratedImage, GeneratedVideo

logger = get_logger("video_agent")


class VideoAgent:
    """视频生成分镜Agent - 调用seedance-2.0-off"""

    def __init__(self):
        self.model = config.get('models.video.endpoint')
        self.poll_interval = config.get('limits.video_poll_interval', 20)
        # 视频生成最大等待时间：600秒（10分钟）
        self.max_wait_time = 600
        # 从 yaml 配置读取分镜时长范围（当无法从剧本解析时使用）
        self.default_duration_min = config.get('video_generation.scene_duration.min', 8)
        self.default_duration_max = config.get('video_generation.scene_duration.max', 15)
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
                config.get('generation.concurrency.video_workers', 6)
            )
        )
        # 参考图URL（用于人物一致性）
        self.reference_image_url = None

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
        self.reference_image_url = reference_image.url if reference_image else None

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
        use_previous_last_frame = self._should_use_previous_last_frame(
            previous_scene=previous_scene,
            current_scene=scene,
            previous_video_url=previous_video_url,
        )
        prepared_reference_images = self._prepare_reference_images_for_generation(
            project_id=project_id,
            scene_number=scene_number,
            previous_video_url=previous_video_url if use_previous_last_frame else None,
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
            characters=None,  # 可以从scene获取
            reference_images=prepared_reference_images,
            has_previous_video=use_previous_last_frame,
            user_style_info=user_style_info,
            user_requirement_text=user_requirement_text,
            duration=duration,
            scene_index=scene_index,
            total_scenes=total_scenes,
        )

        logger.info(
            f"Scene {scene_number}: Generating video with "
            f"{'previous last-frame image + reference images' if use_previous_last_frame else 'reference images only'}"
        )

        task_id = self._create_video_task_with_references(
            prompt=prompt,
            reference_image_urls=reference_urls,
            duration=duration,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
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
        reference_type = str(getattr(image, "reference_type", "") or "").strip().lower()
        asset_id = str(getattr(image, "asset_id", "") or "").strip()
        if reference_type == "character" and asset_id:
            return f"asset://{asset_id}"
        return getattr(image, "url", None)

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
    ) -> str:
        content = [{"type": "text", "text": prompt}]
        for url in reference_image_urls:
            content.append({
                "type": "image_url",
                "role": "reference_image",
                "image_url": {"url": url},
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
        project_id: str,
        scene_number: int,
        previous_video_url: Optional[str],
        reference_images: Optional[List[GeneratedImage]],
        fallback_reference_image: Optional[GeneratedImage] = None,
    ) -> List[GeneratedImage]:
        prepared: List[GeneratedImage] = []
        if previous_video_url and scene_number > 1:
            first_frame_image = self._extract_and_upload_previous_last_frame(
                project_id=project_id,
                scene_number=scene_number,
                previous_video_url=previous_video_url,
            )
            prepared.append(first_frame_image)

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

    def _scene_transition_keywords_present(self, text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        keywords = [
            "随着", "随后", "接着", "紧接着", "下一刻", "片刻后", "与此同时", "此时",
            "画面切换", "镜头转向", "镜头跟随", "切换至", "转场", "然后", "接下来",
            "随即", "延续", "继续", "跟着", "the camera", "cut to", "then", "meanwhile",
            "moments later", "next",
        ]
        return any(keyword in normalized for keyword in keywords)

    def _scene_character_keys(self, scene) -> set:
        keys = set()
        for name in getattr(scene, "characters_present", None) or []:
            key = re.sub(r"\s+", "", str(name or "").strip().lower())
            key = re.sub(r"[^0-9a-z\u4e00-\u9fff_-]+", "", key)
            if key:
                keys.add(key)
        return keys

    def _should_use_previous_last_frame(
        self,
        previous_scene,
        current_scene,
        previous_video_url: Optional[str],
    ) -> bool:
        if not previous_video_url or previous_scene is None or current_scene is None:
            return False

        previous_scene_keys = set(self._split_scene_name_keys(getattr(previous_scene, "scene_name", "")))
        current_scene_keys = set(self._split_scene_name_keys(getattr(current_scene, "scene_name", "")))
        has_shared_backdrop = bool(previous_scene_keys.intersection(current_scene_keys))
        if not has_shared_backdrop:
            logger.info("Skip previous-scene last-frame reference: no shared backdrop between adjacent scenes")
            return False

        previous_description = str(getattr(previous_scene, "description", "") or "")
        current_description = str(getattr(current_scene, "description", "") or "")
        previous_has_transition_cue = self._scene_transition_keywords_present(previous_description)
        current_has_transition_cue = self._scene_transition_keywords_present(current_description)
        shared_characters = bool(self._scene_character_keys(previous_scene).intersection(self._scene_character_keys(current_scene)))
        looks_continuous = current_has_transition_cue or (shared_characters and previous_has_transition_cue)
        if not looks_continuous:
            logger.info("Skip previous-scene last-frame reference: adjacent scenes do not look continuous enough")
            return False

        return True

    def _extract_and_upload_previous_last_frame(
        self,
        project_id: str,
        scene_number: int,
        previous_video_url: str,
    ) -> GeneratedImage:
        work_dir = ensure_project_temp_subdir(project_id, "video_first_frames")
        revision = uuid.uuid4().hex[:8]
        local_filename = f"scene_{scene_number:02d}_prev_last_frame_{revision}.png"
        local_path = ffmpeg_service.extract_last_frame_from_video_url(
            video_url=previous_video_url,
            output_filename=local_filename,
            work_dir=str(work_dir),
            blackout_faces=True,
        )
        uploaded_url = tos_service.upload_file(
            local_path=local_path,
            custom_filename=local_filename,
            project_id=project_id,
            category="references/first_frames",
        )
        logger.info(f"Scene {scene_number}: blacked-out previous-scene last-frame reference image URL: {uploaded_url}")
        return GeneratedImage(
            scene_number=0,
            url=uploaded_url,
            prompt="分镜首帧参考图（来自上一分镜最后一帧并完成黑脸处理）",
            name="分镜首帧",
            reference_type="first_frame",
            source="generated",
            is_reference=True,
        )
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
        有效范围：4-12秒（根据yaml配置）
        """
        try:
            if hasattr(scene, 'duration') and scene.duration is not None:
                duration = int(scene.duration)
                # 检查时长是否在有效范围内（4-12秒）
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
        reference_images: Optional[List[GeneratedImage]] = None,
        has_previous_video: bool = False,
        user_style_info: str = None,
        user_requirement_text: str = None,
        duration: int = 12,
        scene_index: int = 0,
        total_scenes: int = 1,
    ) -> str:
        """
        构建视频生成提示词 - 整合完整的分镜信息

        结合用户补充的风格/场景描述信息和剧本分镜的完整文本
        （包括描述、对话、时长、角色动作、声音描述、情绪、镜头角度、出场角色等）
        """
        # 进一步精简提示词：只保留分镜脚本的核心字段，去掉冗余的段落标题/规则块，避免过分重复。
        _ = (characters, scene_index, total_scenes, duration)  # keep signature stable; these are intentionally unused in the slim prompt.
        parts = []

        reference_specs = self._build_reference_specs(reference_images)
        if reference_specs:
            first_frame_tag = next((spec["tag"] for spec in reference_specs if spec["reference_type"] == "first_frame"), "")
            character_tags = ''.join(spec["tag"] for spec in reference_specs if spec["reference_type"] == "character")
            scene_tags = ''.join(spec["tag"] for spec in reference_specs if spec["reference_type"] == "scene")
            if has_previous_video and first_frame_tag:
                parts.append(f"参考{first_frame_tag}为本视频的首帧参考图。")

            if character_tags and scene_tags:
                parts.append(f"结合{character_tags}人物/角色参考图中的出场角色设定与布景设定{scene_tags}生成当前分镜。")
            elif character_tags:
                parts.append(f"结合{character_tags}人物/角色参考图中的出场角色设定生成当前分镜。")
            elif scene_tags:
                parts.append(f"结合布景设定{scene_tags}生成当前分镜。")
            mapping_parts = []
            for spec in reference_specs:
                if spec["reference_type"] == "first_frame":
                    mapping_parts.append(f'{spec["tag"]}=分镜首帧')
                else:
                    mapping_parts.append(
                        f'{spec["tag"]}={spec["name"]}（{self._reference_type_label(spec["reference_type"])}）'
                    )
            parts.append(f"从参考图片顺序：{'，'.join(mapping_parts)}")

        if user_style_info:
            parts.append(f"风格：{user_style_info}")

        scene_name = getattr(scene, 'scene_name', '') or ''
        if scene_name:
            scene_name_with_tags = self._annotate_scene_names_with_reference_tags(scene_name, reference_specs)
            parts.append(f"使用布景：{scene_name_with_tags}")
        parts.append(f"场景描述：{getattr(scene, 'description', '')}")
        parts.append("保持无字幕，避免画面生成字幕。不生成街边招牌的文字。限定主要人物/角色在不同分镜视频中的各自音色保持一致。")

        dialogue = getattr(scene, 'dialogue', '') or ''
        parts.append(f"对白/旁白：{dialogue if dialogue else '无'}")

        character_desc = getattr(scene, 'character_description', '') or ''
        if character_desc:
            parts.append(f"角色动作：{character_desc}")

        mood = getattr(scene, 'mood', '') or ''
        if mood:
            parts.append(f"情绪氛围：{mood}")

        camera = getattr(scene, 'camera_angle', None) or ''
        if camera:
            parts.append(f"镜头角度：{camera}")

        chars_present = getattr(scene, 'characters_present', None) or []
        if isinstance(chars_present, list) and chars_present:
            parts.append(f"出场角色：{self._annotate_character_names_with_reference_tags(chars_present, reference_specs)}")

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
        if reference_type == "scene":
            return "场景"
        if reference_type == "first_frame":
            return "首帧参考"
        return "参考"

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
        previous_scene = script.scenes[scene_number - 2] if scene_number > 1 else None

        logger.info(f"Regenerating video for scene {scene_number}/{num_scenes}")
        logger.info(f"Using reference image: {reference_image.url if reference_image else 'None'}")

        # 获取分镜时长
        duration = self._get_scene_duration(scene)

        prepared_reference_images = self._prepare_reference_images_for_generation(
            project_id=project_id,
            scene_number=scene_number,
            previous_video_url=previous_video_url if self._should_use_previous_last_frame(previous_scene, scene, previous_video_url) else None,
            reference_images=reference_images,
            fallback_reference_image=reference_image,
        )
        use_previous_last_frame = any(
            getattr(image, "reference_type", None) == "first_frame"
            for image in prepared_reference_images
        )

        # 构建新的提示词
        prompt = self._build_video_prompt(
            scene=scene,
            characters=script.characters,
            reference_images=prepared_reference_images,
            has_previous_video=use_previous_last_frame,
            user_style_info=user_style_info,
            user_requirement_text=user_requirement_text,
            duration=duration,
            scene_index=scene_number - 1,
            total_scenes=num_scenes,
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
