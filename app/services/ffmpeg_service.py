# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import json
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional
from pathlib import Path
from urllib.parse import urlparse
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

        # 归一化每个片段的音视频参数（编码/采样率/声道/像素格式/帧率），
        # 避免不同片段的 AAC 采样率或 profile 不一致导致 concat -c copy 后
        # 播放到后段时解码器报错（Web 播放中断、后半段无声音）。
        normalized_paths = self._normalize_segments_for_concat(processed_paths, work_path)

        # 创建concat文件列表
        concat_file = work_path / "concat_list.txt"
        with open(concat_file, 'w') as f:
            for path in normalized_paths:
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

    def _normalize_segments_for_concat(self, local_paths: List[str], work_path: Path) -> List[str]:
        """
        将各片段统一转码为一致的音视频参数，保证 concat -c copy 后的成片
        在整段范围内可连续解码播放。

        关键点：不同分镜片段的 AAC 采样率 / profile 可能不一致，直接 concat 复制流
        会让容器只声明首段参数，播放到后段时解码器报 "Sample rate index ... does not
        match" / "Prediction is not allowed in AAC-LC"，表现为 Web 播放中断、
        后半段无声。统一重编码为 H.264(yuv420p) + AAC 48kHz 立体声可根治此问题。
        """
        target_fps = str(config.get('merge.normalize_fps', 24) or 24)
        target_sample_rate = str(config.get('merge.normalize_audio_sample_rate', 48000) or 48000)
        normalized_paths: List[str] = []

        for index, input_path in enumerate(local_paths):
            output_path = work_path / f"scene_normalized_{index:03d}.mp4"
            cmd = [
                self._require_binary("ffmpeg"),
                "-y",
                "-i", input_path,
                "-map", "0:v:0",
                "-map", "0:a:0?",
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-r", target_fps,
                "-video_track_timescale", "12288",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", target_sample_rate,
                "-ac", "2",
                "-vsync", "cfr",
                str(output_path),
            ]

            try:
                logger.info(
                    f"Normalizing segment {Path(input_path).name} for concat "
                    f"(fps={target_fps}, audio={target_sample_rate}Hz/stereo)"
                )
                subprocess.run(cmd, capture_output=True, text=True, check=True)
                normalized_paths.append(str(output_path))
            except subprocess.CalledProcessError as e:
                logger.error(f"FFmpeg normalize error: {e.stderr}")
                raise Exception(f"视频片段归一化失败: {e.stderr}")

        return normalized_paths

    def convert_to_mp4(
        self,
        source: str,
        output_filename: str,
        work_dir: str = None,
    ) -> str:
        """
        将本地或远程视频（如 seedance 输出的 mov）转封装为 mp4。

        优先尝试无损转封装（stream copy，H.264/AAC 直接换 mp4 容器）；
        若源编码不兼容 mp4 容器则回退到重编码。

        Args:
            source: 本地文件路径（可含 file:// 前缀）或远程 URL
            output_filename: 目标 mp4 文件名（应以 .mp4 结尾）
            work_dir: 工作目录

        Returns:
            转换后的 mp4 本地路径
        """
        work_path = self._ensure_work_dir(work_dir)

        # 准备本地输入文件
        local_source = str(source or "").strip()
        parsed = urlparse(local_source)
        if parsed.scheme in {"http", "https"}:
            download_path = work_path / f"convert_source.{self._video_suffix()}"
            self._download_file(local_source, str(download_path))
            local_source = str(download_path)
        elif local_source.startswith("file://"):
            local_source = local_source[7:]

        if not Path(local_source).exists():
            raise FileNotFoundError(f"待转换视频不存在: {local_source}")

        output_path = work_path / output_filename

        # 若输入输出为同一路径，避免 ffmpeg 就地覆盖出错
        if Path(local_source).resolve() == output_path.resolve():
            renamed_source = work_path / f"convert_source_orig.{self._video_suffix()}"
            shutil.copy2(local_source, renamed_source)
            local_source = str(renamed_source)

        # 方法1：无损转封装（H.264/AAC -> mp4 容器）
        copy_cmd = [
            self._require_binary("ffmpeg"),
            "-y",
            "-i", local_source,
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]

        try:
            logger.info(f"Converting to mp4 (stream copy): {output_path}")
            subprocess.run(copy_cmd, capture_output=True, text=True, check=True)
            logger.info(f"MP4 stream-copy conversion successful: {output_path}")
            return str(output_path)
        except subprocess.CalledProcessError as e:
            logger.warning(
                f"MP4 stream-copy failed, falling back to re-encode: {e.stderr}"
            )

        # 方法2：重编码为 H.264/AAC 的 mp4
        encode_cmd = [
            self._require_binary("ffmpeg"),
            "-y",
            "-i", local_source,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(output_path),
        ]

        try:
            logger.info(f"Converting to mp4 (re-encode): {output_path}")
            subprocess.run(encode_cmd, capture_output=True, text=True, check=True)
            logger.info(f"MP4 re-encode conversion successful: {output_path}")
            return str(output_path)
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg mp4 conversion error: {e.stderr}")
            raise Exception(f"视频转 MP4 失败: {e.stderr}")

    def _prepare_trimmed_segments(self, local_paths: List[str], work_path: Path) -> List[str]:
        """按配置对各拼接点两侧片段执行临时裁帧。"""
        trim_previous_end_frames = max(0, int(config.get('merge.trim_previous_end_frames', 6)))
        trim_next_start_frames = max(0, int(config.get('merge.trim_next_start_frames', 2)))

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
