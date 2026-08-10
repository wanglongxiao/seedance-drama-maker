# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import json
import shutil
import subprocess
import tempfile
import urllib.request
from typing import Dict, List, Optional, Tuple, Union
from pathlib import Path
from app.config import config
from app.utils.logger import get_logger

logger = get_logger("ffmpeg_service")

class FFmpegService:
    """FFmpeg视频合成服务"""
    
    def __init__(self):
        self.temp_dir = Path(tempfile.gettempdir()) / "video_chatbot"
        self.temp_dir.mkdir(exist_ok=True)
        self.ffmpeg_binary = self._find_binary("ffmpeg")
        self.ffprobe_binary = self._find_binary("ffprobe")

    def _find_binary(self, binary_name: str) -> Optional[str]:
        configured_path = config.get(f"tools.{binary_name}_path")
        candidates: List[str] = []
        if configured_path:
            candidates.append(str(Path(str(configured_path)).expanduser()))

        discovered_path = shutil.which(binary_name)
        if discovered_path:
            candidates.append(discovered_path)

        candidates.extend([
            f"/opt/homebrew/bin/{binary_name}",
            f"/usr/local/bin/{binary_name}",
            f"/usr/bin/{binary_name}",
        ])

        seen = set()
        for candidate in candidates:
            normalized = str(candidate).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            if Path(normalized).exists():
                return normalized
        return None

    def _require_binary(self, binary_name: str) -> str:
        resolved = self.ffmpeg_binary if binary_name == "ffmpeg" else self.ffprobe_binary
        if resolved:
            return resolved
        raise RuntimeError(
            f"未找到 {binary_name} 可执行文件。请安装 {binary_name}，"
            f"或在 config.yaml 中配置 tools.{binary_name}_path。"
        )

    def _video_suffix(self) -> str:
        """本地视频文件的扩展名，与生成模型输出格式保持一致（默认 mov）。"""
        fmt = str(config.get('video_generation.output_format', 'mov')).strip().lstrip('.').lower()
        return fmt or "mov"

    def _ensure_work_dir(self, work_dir: str = None) -> Path:
        path = Path(work_dir) if work_dir else self.temp_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def merge_videos(
        self,
        video_urls: List[str],
        output_filename: str,
        transition_duration: float = 0.5,
        temporary_edge_trim_enabled: bool = False,
        work_dir: str = None,
    ) -> str:
        """
        合并多个视频片段
        
        Args:
            video_urls: 视频片段URL列表
            output_filename: 输出文件名
            transition_duration: 转场时长（秒）
        
        Returns:
            合并后的视频本地路径
        """
        if not video_urls:
            raise ValueError("视频列表不能为空")
        
        if len(video_urls) == 1:
            # 只有一个视频，直接返回
            return video_urls[0]
        
        logger.info(f"Merging {len(video_urls)} videos")
        work_path = self._ensure_work_dir(work_dir)
        
        # 下载所有视频片段
        local_paths = []
        video_suffix = self._video_suffix()
        for i, url in enumerate(video_urls):
            local_path = work_path / f"scene_{i:03d}.{video_suffix}"
            self._download_file(url, str(local_path))
            local_paths.append(str(local_path))
        
        processed_paths = local_paths
        if temporary_edge_trim_enabled and len(local_paths) >= 2:
            processed_paths = self._prepare_trimmed_segments(local_paths, work_path)

        # 创建concat文件列表
        concat_file = work_path / "concat_list.txt"
        with open(concat_file, 'w') as f:
            for path in processed_paths:
                f.write(f"file '{path}'\n")
        
        # 输出文件路径
        output_path = work_path / output_filename
        
        # 使用FFmpeg合并视频
        # 方法1: 简单concat（无转场）
        # -movflags +faststart 将 moov atom 前置，确保 mov/mp4 可在 Web 前端边下边播
        cmd = [
            self._require_binary("ffmpeg"),
            "-y",  # 覆盖输出文件
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path)
        ]
        
        try:
            logger.info(f"Running FFmpeg: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"Video merge successful: {output_path}")
            return str(output_path)
            
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg error: {e.stderr}")
            raise Exception(f"视频合成失败: {e.stderr}")

    def _prepare_trimmed_segments(self, local_paths: List[str], work_path: Path) -> List[str]:
        """按配置对各拼接点两侧片段执行临时裁帧。"""
        trim_previous_end_frames = max(0, int(config.get('merge.trim_previous_end_frames', 6)))
        trim_next_start_frames = max(0, int(config.get('merge.trim_next_start_frames', 1)))

        if trim_previous_end_frames == 0 and trim_next_start_frames == 0:
            return local_paths

        processed_paths: List[str] = []
        total = len(local_paths)

        for index, input_path in enumerate(local_paths):
            trim_start_frames = trim_next_start_frames if index > 0 else 0
            trim_end_frames = trim_previous_end_frames if index < total - 1 else 0

            if trim_start_frames == 0 and trim_end_frames == 0:
                processed_paths.append(input_path)
                continue

            output_path = work_path / f"scene_trimmed_{index:03d}.{self._video_suffix()}"
            self._trim_video_by_frames(
                input_path=input_path,
                output_path=str(output_path),
                trim_start_frames=trim_start_frames,
                trim_end_frames=trim_end_frames
            )
            processed_paths.append(str(output_path))

        return processed_paths

    def _trim_video_by_frames(
        self,
        input_path: str,
        output_path: str,
        trim_start_frames: int,
        trim_end_frames: int
    ) -> None:
        """按帧裁掉视频开头和结尾，并重建时间戳。"""
        info = self._get_video_stream_info(input_path)
        total_frames = info["total_frames"]
        fps = info["fps"]
        duration = info["duration"]

        if trim_start_frames + trim_end_frames >= total_frames:
            raise ValueError(
                f"裁帧失败：{Path(input_path).name} 总帧数仅 {total_frames}，"
                f"无法裁掉开头 {trim_start_frames} 帧和结尾 {trim_end_frames} 帧"
            )

        start_frame = trim_start_frames
        end_frame = total_frames - trim_end_frames
        start_time = trim_start_frames / fps if fps > 0 else 0.0
        end_time = duration - (trim_end_frames / fps if fps > 0 else 0.0)

        video_filter = f"trim=start_frame={start_frame}:end_frame={end_frame},setpts=PTS-STARTPTS"
        cmd = [
            self._require_binary("ffmpeg"),
            "-y",
            "-i", input_path,
            "-vf", video_filter,
        ]

        if end_time > start_time:
            audio_filter = f"atrim=start={start_time}:end={end_time},asetpts=PTS-STARTPTS"
            cmd.extend(["-af", audio_filter])

        cmd.extend([
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-c:a", "aac",
            output_path
        ])

        try:
            logger.info(
                f"Temporary merge trim for {Path(input_path).name}: "
                f"start={trim_start_frames} frames, "
                f"end={trim_end_frames} frames, "
                f"total={total_frames} frames"
            )
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg trim error: {e.stderr}")
            raise Exception(f"视频裁帧失败: {e.stderr}")

    def _get_video_stream_info(self, input_path: str) -> Dict[str, float]:
        """读取视频帧率、时长和总帧数，为按帧裁剪提供基础信息。"""
        cmd = [
            self._require_binary("ffprobe"),
            "-v", "error",
            "-count_frames",
            "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames,r_frame_rate",
            "-show_entries", "format=duration",
            "-of", "json",
            input_path
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            payload = json.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            logger.error(f"ffprobe error: {e.stderr}")
            raise Exception(f"读取视频信息失败: {e.stderr}")

        stream = (payload.get("streams") or [{}])[0]
        format_info = payload.get("format") or {}
        frame_rate = stream.get("r_frame_rate", "0/1")
        fps = self._parse_fraction(frame_rate)
        total_frames = int(stream.get("nb_read_frames") or 0)
        duration = float(format_info.get("duration") or 0.0)

        if fps <= 0:
            raise ValueError(f"读取视频帧率失败: {input_path}")
        if total_frames <= 0:
            total_frames = max(1, int(round(duration * fps)))
        if duration <= 0:
            duration = total_frames / fps

        return {
            "fps": fps,
            "total_frames": total_frames,
            "duration": duration
        }

    def _parse_fraction(self, value: str) -> float:
        if not value:
            return 0.0
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return 0.0
            return float(numerator) / denominator_value
        return float(value)
    
    def merge_videos_with_transitions(
        self,
        video_urls: List[str],
        output_filename: str,
        transition_duration: float = 0.5,
        work_dir: str = None,
    ) -> str:
        """
        合并多个视频片段（带转场效果）
        
        Args:
            video_urls: 视频片段URL列表
            output_filename: 输出文件名
            transition_duration: 转场时长（秒）
        
        Returns:
            合并后的视频本地路径
        """
        if not video_urls:
            raise ValueError("视频列表不能为空")
        
        if len(video_urls) == 1:
            # 只有一个视频，直接下载返回
            output_path = self.temp_dir / output_filename
            self._download_file(video_urls[0], str(output_path))
            return str(output_path)
        
        logger.info(f"Merging {len(video_urls)} videos with transitions")
        work_path = self._ensure_work_dir(work_dir)
        
        # 下载所有视频片段
        local_paths = []
        video_suffix = self._video_suffix()
        for i, url in enumerate(video_urls):
            local_path = work_path / f"scene_{i:03d}.{video_suffix}"
            self._download_file(url, str(local_path))
            local_paths.append(str(local_path))
        
        output_path = work_path / output_filename
        
        # 构建复杂的FFmpeg滤镜图
        # 使用xfade滤镜实现转场效果
        filter_complex = self._build_xfade_filter(len(local_paths), transition_duration)
        
        cmd = [self._require_binary("ffmpeg"), "-y"]
        
        # 添加输入文件
        for path in local_paths:
            cmd.extend(["-i", path])
        
        # 添加滤镜
        cmd.extend([
            "-filter_complex", filter_complex,
            "-vsync", "vfr",
            str(output_path)
        ])
        
        try:
            logger.info(f"Running FFmpeg with transitions")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"Video merge with transitions successful: {output_path}")
            return str(output_path)
            
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg error: {e.stderr}")
            # 回退到简单合并
            logger.warning("Falling back to simple concat")
            return self.merge_videos(video_urls, output_filename)
    
    def _build_xfade_filter(self, num_videos: int, transition_duration: float) -> str:
        """
        构建xfade滤镜字符串
        
        Args:
            num_videos: 视频数量
            transition_duration: 转场时长
        
        Returns:
            滤镜字符串
        """
        if num_videos == 2:
            return f"[0:v][1:v]xfade=transition=fade:duration={transition_duration}:offset=0[v]"
        
        # 多个视频的复杂滤镜
        filters = []
        
        # 第一个转场
        filters.append(f"[0:v][1:v]xfade=transition=fade:duration={transition_duration}:offset=0[vt0]")
        
        # 后续转场
        for i in range(1, num_videos - 1):
            filters.append(f"[vt{i-1}][{i+1}:v]xfade=transition=fade:duration={transition_duration}:offset=0[vt{i}]")
        
        # 最后一个输出
        filters[-1] = filters[-1].replace(f"[vt{num_videos-2}]", "")
        
        return ";".join(filters)
    
    def _download_file(self, url: str, local_path: str):
        """
        下载文件到本地
        
        Args:
            url: 文件URL
            local_path: 本地保存路径
        """
        import requests
        
        logger.debug(f"Downloading: {url}")
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        logger.debug(f"Downloaded to: {local_path}")

    def extract_last_frame_from_video_url(
        self,
        video_url: str,
        output_filename: str,
        work_dir: str = None,
        blackout_faces: bool = False,
    ) -> str:
        """
        下载远程视频并截取最后一帧图片。

        Args:
            video_url: 远程视频 URL
            output_filename: 输出图片文件名
            work_dir: 工作目录

        Returns:
            截取出的最后一帧图片本地路径
        """
        work_path = self._ensure_work_dir(work_dir)
        local_video_path = work_path / f"{Path(output_filename).stem}.{self._video_suffix()}"
        output_path = work_path / output_filename

        self._download_file(video_url, str(local_video_path))
        info = self._get_video_stream_info(str(local_video_path))
        fps = max(info.get("fps", 0.0), 1.0)
        duration = max(info.get("duration", 0.0), 0.0)
        candidate_offsets = [
            max(1.0 / fps, 0.001),
            0.05,
            0.2,
            0.5,
            1.0,
            min(2.0, duration if duration > 0 else 2.0),
        ]
        output_path.parent.mkdir(parents=True, exist_ok=True)

        last_error = ""
        attempt_modes = ["preseek", "sseof"]
        for mode in attempt_modes:
            for offset in candidate_offsets:
                if output_path.exists():
                    output_path.unlink()

                timestamp = max(duration - offset, 0.0)
                if mode == "preseek":
                    cmd = [
                        self._require_binary("ffmpeg"),
                        "-y",
                        "-ss", f"{timestamp:.3f}",
                        "-i", str(local_video_path),
                        "-frames:v", "1",
                        str(output_path),
                    ]
                    log_message = f"Extracting last frame from previous video at {timestamp:.3f}s"
                else:
                    cmd = [
                        self._require_binary("ffmpeg"),
                        "-y",
                        "-sseof", f"-{max(offset, 0.001):.3f}",
                        "-i", str(local_video_path),
                        "-frames:v", "1",
                        str(output_path),
                    ]
                    log_message = f"Extracting last frame from previous video using sseof -{max(offset, 0.001):.3f}s"

                try:
                    logger.info(f"{log_message} -> {output_path.name}")
                    subprocess.run(cmd, capture_output=True, text=True, check=True)
                    if output_path.exists() and output_path.stat().st_size > 0:
                        if blackout_faces:
                            self.blackout_faces_in_image(str(output_path))
                        return str(output_path)
                    last_error = f"ffmpeg completed but output file not created at {output_path}"
                    logger.warning(last_error)
                except subprocess.CalledProcessError as e:
                    last_error = e.stderr
                    logger.warning(
                        f"FFmpeg extract last frame attempt failed ({mode}) at offset {offset:.3f}s: {e.stderr}"
                    )

        raise Exception(f"截取上一分镜最后一帧失败: {last_error}")

    def blackout_faces_in_image(self, image_path: str) -> str:
        """
        使用 MediaPipe 检测图片中的正脸和侧脸，并用纯黑矩形完全遮挡。
        """
        try:
            import numpy as np
            from PIL import Image, ImageDraw
        except ImportError as exc:
            raise RuntimeError(
                "Face blackout dependencies are missing. Please install Pillow and numpy."
            ) from exc

        original_image = Image.open(image_path).convert("RGB")
        width, height = original_image.size
        image_np = np.array(original_image)
        detected_boxes = self._detect_faces_with_mediapipe(image_np, width, height)
        if not detected_boxes:
            logger.warning("MediaPipe face detection unavailable or found no faces; falling back to OpenCV face detection")
            detected_boxes = self._detect_faces_with_opencv(image_np, width, height)

        merged_boxes = self._merge_face_boxes(detected_boxes, width, height)
        if not merged_boxes:
            logger.warning(f"No faces detected for blackout in image: {image_path}")
            return image_path

        draw = ImageDraw.Draw(original_image)
        for box in merged_boxes:
            draw.rectangle(box, fill=(0, 0, 0))

        original_image.save(image_path)
        logger.info(f"Applied black face blackout to {len(merged_boxes)} face region(s): {Path(image_path).name}")
        return image_path

    def _detect_faces_with_mediapipe(
        self,
        image_np,
        image_width: int,
        image_height: int,
    ) -> List[Tuple[int, int, int, int]]:
        try:
            import mediapipe as mp
            from mediapipe.tasks.python.core.base_options import BaseOptions
            from mediapipe.tasks.python.vision import FaceDetector, FaceDetectorOptions
        except Exception as exc:
            logger.warning(f"MediaPipe import unavailable for face blackout: {exc}")
            return []

        detected_boxes: List[Tuple[int, int, int, int]] = []
        try:
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_np)
            for model_name, model_file in self._ensure_mediapipe_face_detector_models():
                options = FaceDetectorOptions(
                    base_options=BaseOptions(model_asset_path=str(model_file)),
                    min_detection_confidence=0.25,
                )
                with FaceDetector.create_from_options(options) as detector:
                    result = detector.detect(mp_image)

                for detection in getattr(result, "detections", []) or []:
                    bbox = getattr(detection, "bounding_box", None)
                    if not bbox:
                        continue
                    box = self._rect_to_padded_box(
                        origin_x=int(getattr(bbox, "origin_x", 0)),
                        origin_y=int(getattr(bbox, "origin_y", 0)),
                        width=int(getattr(bbox, "width", 0)),
                        height=int(getattr(bbox, "height", 0)),
                        image_width=image_width,
                        image_height=image_height,
                    )
                    if box:
                        detected_boxes.append(box)

                if detected_boxes:
                    break
        except Exception as exc:
            logger.warning(f"MediaPipe face detection failed: {exc}")
            return []
        return detected_boxes

    def _ensure_mediapipe_face_detector_models(self) -> List[Tuple[str, Path]]:
        configured_dir = config.get("video_generation.face_detection.model_cache_dir", "/tmp/aw-videobot-sd2/mediapipe-models")
        model_dir = Path(str(configured_dir)).expanduser()
        if not model_dir.is_absolute():
            model_dir = Path(__file__).resolve().parents[2] / model_dir
        model_dir.mkdir(parents=True, exist_ok=True)

        model_specs = [
            (
                "short-range",
                model_dir / "blaze_face_short_range.tflite",
                "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite",
            ),
            (
                "full-range",
                model_dir / "blaze_face_full_range.tflite",
                "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_full_range/float16/1/blaze_face_full_range.tflite",
            ),
        ]

        available_models: List[Tuple[str, Path]] = []
        for model_name, model_path, model_url in model_specs:
            if not model_path.exists() or model_path.stat().st_size == 0:
                logger.info(f"Downloading MediaPipe face detector model {model_name}: {model_url}")
                urllib.request.urlretrieve(model_url, str(model_path))
            available_models.append((model_name, model_path))

        return available_models

    def _detect_faces_with_opencv(
        self,
        image_np,
        image_width: int,
        image_height: int,
    ) -> List[Tuple[int, int, int, int]]:
        try:
            import cv2
        except Exception as exc:
            logger.warning(f"OpenCV import unavailable for fallback face blackout: {exc}")
            return []

        gray = cv2.cvtColor(image_np, cv2.COLOR_RGB2GRAY)
        cascade_specs = [
            ("frontal_default", Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"),
            ("frontal_alt", Path(cv2.data.haarcascades) / "haarcascade_frontalface_alt.xml"),
            ("frontal_alt2", Path(cv2.data.haarcascades) / "haarcascade_frontalface_alt2.xml"),
            ("profile", Path(cv2.data.haarcascades) / "haarcascade_profileface.xml"),
        ]
        available_cascades = [(name, path) for name, path in cascade_specs if path.exists()]
        if not available_cascades:
            logger.warning("OpenCV face cascade files are missing")
            return []

        raw_candidates: List[Dict[str, Union[int, str]]] = []
        mirrored_gray = cv2.flip(gray, 1)
        parameter_sets = [
            {"scaleFactor": 1.03, "minNeighbors": 3, "minSize": (16, 16)},
            {"scaleFactor": 1.06, "minNeighbors": 4, "minSize": (20, 20)},
        ]
        for cascade_name, cascade_path in available_cascades:
            detector = cv2.CascadeClassifier(str(cascade_path))
            for params in parameter_sets:
                for working_gray, mirrored in ((gray, False), (mirrored_gray, True)):
                    faces = detector.detectMultiScale(working_gray, **params)
                    for x, y, w, h in faces:
                        if mirrored:
                            x = image_width - (x + w)
                        raw_candidates.append({
                            "cascade": cascade_name,
                            "x": int(x),
                            "y": int(y),
                            "w": int(w),
                            "h": int(h),
                        })
            if raw_candidates:
                logger.info(
                    f"OpenCV face blackout detector matched {len(raw_candidates)} raw candidate box(es) after cascade {cascade_name}"
                )
        body_hints = self._detect_body_head_hints_with_opencv(image_np, image_width, image_height)
        return self._filter_opencv_face_candidates(raw_candidates, body_hints, image_np, image_width, image_height)

    def _detect_body_head_hints_with_opencv(
        self,
        image_np,
        image_width: int,
        image_height: int,
    ) -> List[Tuple[int, int, int, int]]:
        try:
            import cv2
        except Exception:
            return []

        gray = cv2.cvtColor(image_np, cv2.COLOR_RGB2GRAY)
        cascade_specs = [
            ("upperbody", Path(cv2.data.haarcascades) / "haarcascade_upperbody.xml"),
            ("fullbody", Path(cv2.data.haarcascades) / "haarcascade_fullbody.xml"),
        ]
        hints: List[Tuple[int, int, int, int]] = []
        for cascade_name, cascade_path in cascade_specs:
            if not cascade_path.exists():
                continue
            detector = cv2.CascadeClassifier(str(cascade_path))
            rects = detector.detectMultiScale(
                gray,
                scaleFactor=1.03,
                minNeighbors=3,
                minSize=(30, 30),
            )
            for x, y, w, h in rects:
                if cascade_name == "upperbody":
                    head_x = x + int(w * 0.28)
                    head_y = max(0, y - int(h * 0.20))
                    head_w = max(24, int(w * 0.42))
                    head_h = max(24, int(h * 0.40))
                else:
                    head_x = x + int(w * 0.18)
                    head_y = max(0, y - int(h * 0.08))
                    head_w = max(24, int(w * 0.64))
                    head_h = max(24, int(h * 0.34))

                box = self._rect_to_padded_box(
                    origin_x=head_x,
                    origin_y=head_y,
                    width=head_w,
                    height=head_h,
                    image_width=image_width,
                    image_height=image_height,
                )
                if box:
                    hints.append(box)
        return hints

    def _filter_opencv_face_candidates(
        self,
        candidates: List[Dict[str, Union[int, str]]],
        body_hints: List[Tuple[int, int, int, int]],
        image_np,
        image_width: int,
        image_height: int,
    ) -> List[Tuple[int, int, int, int]]:
        if not candidates:
            return []

        clusters: List[List[Dict[str, Union[int, str]]]] = []
        for candidate in candidates:
            assigned = False
            cx = int(candidate["x"]) + int(candidate["w"]) / 2.0
            cy = int(candidate["y"]) + int(candidate["h"]) / 2.0
            for cluster in clusters:
                anchor = cluster[0]
                ax = int(anchor["x"]) + int(anchor["w"]) / 2.0
                ay = int(anchor["y"]) + int(anchor["h"]) / 2.0
                distance_x = abs(cx - ax)
                distance_y = abs(cy - ay)
                threshold_x = max(int(candidate["w"]), int(anchor["w"])) * 1.2
                threshold_y = max(int(candidate["h"]), int(anchor["h"])) * 1.2
                if distance_x <= threshold_x and distance_y <= threshold_y:
                    cluster.append(candidate)
                    assigned = True
                    break
            if not assigned:
                clusters.append([candidate])

        filtered_boxes: List[Tuple[int, int, int, int]] = []
        for cluster in clusters:
            widths = [int(item["w"]) for item in cluster]
            heights = [int(item["h"]) for item in cluster]
            avg_size = (sum(widths) / len(widths) + sum(heights) / len(heights)) / 2.0
            unique_cascades = {str(item["cascade"]) for item in cluster}
            has_default_support = "frontal_default" in unique_cascades
            x1 = min(int(item["x"]) for item in cluster)
            y1 = min(int(item["y"]) for item in cluster)
            x2 = max(int(item["x"]) + int(item["w"]) for item in cluster)
            y2 = max(int(item["y"]) + int(item["h"]) for item in cluster)
            raw_box = (x1, y1, x2, y2)
            overlaps_body_hint = any(self._boxes_overlap(raw_box, hint) for hint in body_hints)

            keep_cluster = (
                has_default_support
                and (
                    (len(cluster) >= 2 and avg_size >= 36)
                    or (len(unique_cascades) >= 2 and avg_size >= 28)
                )
            )
            if not keep_cluster and overlaps_body_hint and avg_size >= 28:
                keep_cluster = True
            if not keep_cluster and unique_cascades == {"profile"} and avg_size >= 120 and y1 < int(image_height * 0.85):
                keep_cluster = True
            if not keep_cluster:
                continue

            skin_ratio = self._estimate_skin_ratio(image_np, x1, y1, x2, y2)
            if skin_ratio < 0.02 and not overlaps_body_hint:
                continue

            box = self._rect_to_padded_box(
                origin_x=x1,
                origin_y=y1,
                width=x2 - x1,
                height=y2 - y1,
                image_width=image_width,
                image_height=image_height,
            )
            if box:
                filtered_boxes.append(box)

        logger.info(
            f"OpenCV face blackout detector kept {len(filtered_boxes)} filtered face box(es) from {len(candidates)} raw candidate(s)"
        )
        return filtered_boxes

    def _estimate_skin_ratio(
        self,
        image_np,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
    ) -> float:
        try:
            import cv2
            import numpy as np
        except Exception:
            return 0.0

        region = image_np[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        if region.size == 0:
            return 0.0

        ycrcb = cv2.cvtColor(region, cv2.COLOR_RGB2YCrCb)
        lower = np.array([0, 133, 77], dtype=np.uint8)
        upper = np.array([255, 173, 127], dtype=np.uint8)
        mask = cv2.inRange(ycrcb, lower, upper)
        return float(mask.mean() / 255.0)

    def _rect_to_padded_box(
        self,
        origin_x: int,
        origin_y: int,
        width: int,
        height: int,
        image_width: int,
        image_height: int,
    ) -> Optional[Tuple[int, int, int, int]]:
        if width <= 0 or height <= 0:
            return None

        x1 = origin_x
        y1 = origin_y
        x2 = origin_x + width
        y2 = origin_y + height

        pad_x = max(8, int(width * 0.22))
        pad_y = max(8, int(height * 0.30))
        x1 = max(0, x1 - pad_x)
        y1 = max(0, y1 - pad_y)
        x2 = min(image_width, x2 + pad_x)
        y2 = min(image_height, y2 + pad_y)
        if x2 <= x1 or y2 <= y1:
            return None
        return (x1, y1, x2, y2)

    def _relative_face_box_to_pixels(
        self,
        xmin: float,
        ymin: float,
        box_width: float,
        box_height: float,
        image_width: int,
        image_height: int,
        mirrored: bool = False,
    ) -> Optional[Tuple[int, int, int, int]]:
        left = max(0.0, float(xmin))
        top = max(0.0, float(ymin))
        width = max(0.0, float(box_width))
        height = max(0.0, float(box_height))

        if width <= 0 or height <= 0:
            return None

        x1 = int(left * image_width)
        y1 = int(top * image_height)
        x2 = int((left + width) * image_width)
        y2 = int((top + height) * image_height)

        if mirrored:
            mirrored_x1 = image_width - x2
            mirrored_x2 = image_width - x1
            x1, x2 = mirrored_x1, mirrored_x2

        pad_x = max(8, int((x2 - x1) * 0.22))
        pad_y = max(8, int((y2 - y1) * 0.30))

        x1 = max(0, x1 - pad_x)
        y1 = max(0, y1 - pad_y)
        x2 = min(image_width, x2 + pad_x)
        y2 = min(image_height, y2 + pad_y)

        if x2 <= x1 or y2 <= y1:
            return None
        return (x1, y1, x2, y2)

    def _merge_face_boxes(
        self,
        boxes: List[Tuple[int, int, int, int]],
        image_width: int,
        image_height: int,
    ) -> List[Tuple[int, int, int, int]]:
        if not boxes:
            return []

        merged: List[Tuple[int, int, int, int]] = []
        for box in boxes:
            current = box
            updated: List[Tuple[int, int, int, int]] = []
            for existing in merged:
                if self._boxes_overlap(current, existing):
                    current = (
                        min(current[0], existing[0]),
                        min(current[1], existing[1]),
                        max(current[2], existing[2]),
                        max(current[3], existing[3]),
                    )
                else:
                    updated.append(existing)
            updated.append(current)
            merged = updated

        normalized: List[Tuple[int, int, int, int]] = []
        for x1, y1, x2, y2 in merged:
            normalized.append((
                max(0, min(image_width, x1)),
                max(0, min(image_height, y1)),
                max(0, min(image_width, x2)),
                max(0, min(image_height, y2)),
            ))
        return normalized

    def _boxes_overlap(
        self,
        first: Tuple[int, int, int, int],
        second: Tuple[int, int, int, int],
    ) -> bool:
        return not (
            first[2] < second[0]
            or second[2] < first[0]
            or first[3] < second[1]
            or second[3] < first[1]
        )
    
    def add_audio_to_video(
        self,
        video_path: str,
        audio_path: str,
        output_filename: str,
        work_dir: str = None,
    ) -> str:
        """
        为视频添加音频
        
        Args:
            video_path: 视频文件路径
            audio_path: 音频文件路径
            output_filename: 输出文件名
        
        Returns:
            输出视频路径
        """
        output_path = self._ensure_work_dir(work_dir) / output_filename
        
        cmd = [
            self._require_binary("ffmpeg"),
            "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            str(output_path)
        ]
        
        try:
            logger.info(f"Adding audio to video")
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info(f"Audio added successfully: {output_path}")
            return str(output_path)
            
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg error: {e.stderr}")
            raise
    
    def cleanup_temp_files(self, work_dir: str = None):
        """清理临时文件"""
        try:
            cleanup_dir = self._ensure_work_dir(work_dir)
            for file in cleanup_dir.glob("*"):
                if file.is_file():
                    file.unlink()
            logger.info("Temp files cleaned up")
        except Exception as e:
            logger.error(f"Cleanup error: {str(e)}")

# 全局服务实例
ffmpeg_service = FFmpegService()
