# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import os
import json
from typing import List
from datetime import datetime
from urllib.parse import urlparse
from app.config import config
from app.services.ffmpeg_service import ffmpeg_service
from app.services.tos_service import tos_service
from app.utils.logger import get_logger
from app.utils.task_paths import ensure_project_temp_subdir
from app.models.schemas import Script, GeneratedVideo

logger = get_logger("merge_agent")

class MergeAgent:
    """视频合成输出Agent"""
    
    def __init__(self):
        self.cleanup_enabled = config.get('cleanup.enabled', True)
        self.retain_hours = config.get('cleanup.retain_hours', 0)

    def _is_temporary_edge_trim_enabled(self) -> bool:
        trim_switch = str(config.get('merge.temporary_edge_trim', 'off')).strip().lower()
        return trim_switch in {"on", "true", "1", "yes"}

    def _is_remote_url(self, value: str) -> bool:
        parsed = urlparse(str(value or "").strip())
        return parsed.scheme in {"http", "https"}

    def _upload_or_copy_merged_output(self, source: str, output_filename: str, project_id: str) -> str:
        """将合并结果统一落到 videos/final 下，兼容本地文件路径和远程 URL。"""
        if self._is_remote_url(source):
            logger.info("Merged output is a remote URL; copying into final output directory")
            return tos_service.copy_url_to_tos(
                source_url=source,
                target_filename=output_filename,
                project_id=project_id,
                category="videos/final",
            )

        local_path = str(source or "").strip()
        if local_path.startswith("file://"):
            local_path = local_path[7:]

        return tos_service.upload_file(
            local_path=local_path,
            custom_filename=output_filename,
            project_id=project_id,
            category="videos/final",
        )
    
    def merge_videos(
        self,
        script: Script,
        videos: List[GeneratedVideo],
        project_id: str
    ) -> str:
        """
        合成所有视频片段为最终视频
        
        Args:
            script: 剧本对象
            videos: 视频片段列表
            project_id: 项目ID
        
        Returns:
            最终视频的URL
        """
        logger.log_agent_call("MergeAgent", "merge_videos", {
            "num_videos": len(videos),
            "project_id": project_id
        })
        temporary_edge_trim_enabled = self._is_temporary_edge_trim_enabled()
        
        # 按场景编号排序
        sorted_videos = sorted(videos, key=lambda v: v.scene_number)
        
        # 提取视频URL
        video_urls = [v.url for v in sorted_videos]
        
        logger.info(f"Merging {len(video_urls)} videos")
        logger.info(
            "Temporary edge trim is %s (trim_previous_end_frames=%s, trim_next_start_frames=%s)",
            "enabled" if temporary_edge_trim_enabled else "disabled",
            config.get('merge.trim_previous_end_frames', 6),
            config.get('merge.trim_next_start_frames', 2),
        )
        
        # 生成输出文件名：FFmpeg 合成阶段沿用生成模型输出格式（默认 mov），
        # 随后统一转封装为 mp4 供 Web 前端播放。
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        video_ext = str(config.get('video_generation.output_format', 'mov')).strip().lstrip('.').lower() or "mov"
        merge_filename = f"merged_{project_id}_{timestamp}.{video_ext}"
        mp4_filename = f"final_video_{project_id}_{timestamp}.mp4"
        
        try:
            merge_temp_dir = ensure_project_temp_subdir(project_id, "merge")
            # 使用FFmpeg合并视频
            local_path = ffmpeg_service.merge_videos(
                video_urls=video_urls,
                output_filename=merge_filename,
                temporary_edge_trim_enabled=temporary_edge_trim_enabled,
                work_dir=str(merge_temp_dir),
            )
            
            logger.info(f"Video merged locally: {local_path}")
            
            # 将合成结果（mov）转封装为 mp4；单分镜时 local_path 可能为远程场景 URL，
            # convert_to_mp4 会自动下载后再转换。
            mp4_local_path = ffmpeg_service.convert_to_mp4(
                source=local_path,
                output_filename=mp4_filename,
                work_dir=str(merge_temp_dir),
            )
            
            logger.info(f"Video converted to mp4: {mp4_local_path}")
            
            # 上传 mp4 到 TOS
            final_url = self._upload_or_copy_merged_output(
                source=mp4_local_path,
                output_filename=mp4_filename,
                project_id=project_id,
            )
            
            logger.info(f"Final video uploaded: {final_url}")
            
            # 清理临时文件
            if self.cleanup_enabled and self.retain_hours == 0:
                self._cleanup_temp_files(project_id)
            
            return final_url
            
        except Exception as e:
            logger.error(f"Video merge failed: {str(e)}")
            raise
    
    def save_project_metadata(
        self,
        project_id: str,
        script: Script,
        videos: List[GeneratedVideo],
        final_video_url: str
    ) -> str:
        """
        保存项目元数据到TOS
        
        Args:
            project_id: 项目ID
            script: 剧本对象
            videos: 视频片段列表
            final_video_url: 最终视频URL
        
        Returns:
            元数据文件URL
        """
        logger.log_agent_call("MergeAgent", "save_project_metadata")
        
        metadata = {
            "project_id": project_id,
            "created_at": datetime.now().isoformat(),
            "script": script.dict(),
            "videos": [v.dict() for v in videos],
            "final_video_url": final_video_url
        }
        
        # 转换为JSON，处理datetime对象
        json_content = json.dumps(metadata, ensure_ascii=False, indent=2, default=str)
        
        # 生成文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"project_{project_id}_{timestamp}.json"
        
        # 上传到TOS
        url = tos_service.upload_bytes(
            data=json_content.encode('utf-8'),
            filename=filename,
            content_type="application/json",
            project_id=project_id,
            category="metadata",
        )
        
        logger.info(f"Project metadata saved: {url}")
        
        return url
    
    def _cleanup_temp_files(self, project_id: str):
        """清理临时文件"""
        logger.info("Cleaning up temporary files")
        
        try:
            ffmpeg_service.cleanup_temp_files(str(ensure_project_temp_subdir(project_id, "merge")))
            logger.info("Temporary files cleaned up")
        except Exception as e:
            logger.error(f"Cleanup failed: {str(e)}")
    
    def get_video_info(self, video_url: str) -> dict:
        """
        获取视频信息
        
        Args:
            video_url: 视频URL
        
        Returns:
            视频信息字典
        """
        # 最终成片统一为 mp4
        return {
            "url": video_url,
            "format": "mp4"
        }
