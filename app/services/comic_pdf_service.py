# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import io
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont

from app.models.schemas import GeneratedImage, VideoProject
from app.services.tos_service import tos_service
from app.utils.logger import get_logger

logger = get_logger("comic_pdf_service")


class ComicPDFService:
    """Generate a comic-style storyboard PDF and upload it to TOS."""

    PAGE_SIZE = (1240, 1754)
    MARGIN = 92
    TITLE_COLOR = (31, 41, 55)
    TEXT_COLOR = (55, 65, 81)
    MUTED_COLOR = (107, 114, 128)
    ACCENT_COLOR = (24, 144, 255)
    BORDER_COLOR = (229, 231, 235)

    FONT_CANDIDATES = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    def generate_and_upload(self, project: VideoProject) -> str:
        if not getattr(project, "script", None):
            raise ValueError("Cannot generate comic PDF without script")

        temp_dir = Path(tempfile.mkdtemp(prefix=f"comic-pdf-{project.project_id}-"))
        filename = self._build_pdf_filename(getattr(project.script, "title", "") or project.project_id)
        local_path = temp_dir / filename

        pages = self._build_pages(project)
        if not pages:
            raise RuntimeError("No PDF pages generated")

        pages[0].save(
            local_path,
            "PDF",
            resolution=144.0,
            save_all=True,
            append_images=pages[1:],
        )
        logger.info(f"Comic PDF generated locally: {local_path}")

        uploaded_url = tos_service.upload_file(
            str(local_path),
            custom_filename=filename,
            project_id=project.project_id,
            category="documents/comics",
        )
        if not str(uploaded_url).startswith("file://"):
            shutil.rmtree(temp_dir, ignore_errors=True)
        return uploaded_url

    def _build_pages(self, project: VideoProject) -> List[Image.Image]:
        script = project.script
        return [
            self._build_cover_page(script),
            self._build_characters_page(project),
            *[
                self._build_scene_page(project, scene, index + 3)
                for index, scene in enumerate(getattr(script, "scenes", []) or [])
            ],
        ]

    def _build_cover_page(self, script: Any) -> Image.Image:
        page, draw = self._new_page()
        title = str(getattr(script, "title", "") or "未命名剧本").strip()
        era = str(getattr(script, "era", "") or "未指定").strip()
        background = str(getattr(script, "background", "") or "未指定").strip()

        title_font = self._font(54)
        body_font = self._font(28)
        label_font = self._font(30)

        y = 360
        for line in self._wrap_text(draw, title, title_font, self.PAGE_SIZE[0] - self.MARGIN * 2):
            bbox = draw.textbbox((0, 0), line, font=title_font)
            x = (self.PAGE_SIZE[0] - (bbox[2] - bbox[0])) // 2
            self._draw_text(draw, (x, y), line, title_font, self.TITLE_COLOR)
            y += 72

        y += 110
        self._draw_text(draw, (self.MARGIN, y), "时代", label_font, self.ACCENT_COLOR)
        y = self._draw_wrapped_text(
            draw,
            era,
            (self.MARGIN, y + 48),
            body_font,
            self.PAGE_SIZE[0] - self.MARGIN * 2,
            line_spacing=14,
        )

        y += 72
        self._draw_text(draw, (self.MARGIN, y), "背景", label_font, self.ACCENT_COLOR)
        self._draw_wrapped_text(
            draw,
            background,
            (self.MARGIN, y + 48),
            body_font,
            self.PAGE_SIZE[0] - self.MARGIN * 2,
            line_spacing=14,
            max_lines=12,
        )
        self._draw_footer(draw, 1)
        return page

    def _build_characters_page(self, project: VideoProject) -> Image.Image:
        page, draw = self._new_page()
        title_font = self._font(38)
        name_font = self._font(26)
        small_font = self._font(22)
        self._draw_text(draw, (self.MARGIN, 72), "主要角色", title_font, self.TITLE_COLOR)
        self._draw_rule(draw, 128)

        characters = list(getattr(project.script, "characters", []) or [])
        character_images = list(getattr(project, "character_reference_images", []) or [])
        if not characters and character_images:
            characters = character_images

        cols = 2
        card_w = (self.PAGE_SIZE[0] - self.MARGIN * 2 - 34) // cols
        card_h = 360
        start_y = 170
        for index, character in enumerate(characters[:8]):
            col = index % cols
            row = index // cols
            x = self.MARGIN + col * (card_w + 34)
            y = start_y + row * (card_h + 34)
            if y + card_h > self.PAGE_SIZE[1] - 120:
                break

            self._draw_card(draw, (x, y, x + card_w, y + card_h))
            name = str(getattr(character, "name", "") or getattr(character, "name", "") or f"角色{index + 1}").strip()
            image = self._find_character_image(project, name, index)
            if image:
                self._paste_fitted(page, image, (x + 24, y + 24, x + 210, y + 250))
            else:
                self._draw_placeholder(draw, (x + 24, y + 24, x + 210, y + 250), "无图片")

            text_x = x + 236
            self._draw_wrapped_text(draw, name, (text_x, y + 28), name_font, card_w - 260, max_lines=2)
            desc_parts = [
                str(getattr(character, "gender", "") or "").strip(),
                str(getattr(character, "age", "") or "").strip(),
                str(getattr(character, "personality", "") or "").strip(),
            ]
            desc = " / ".join([part for part in desc_parts if part]) or "角色设定"
            self._draw_wrapped_text(draw, desc, (text_x, y + 112), small_font, card_w - 260, max_lines=5)

        self._draw_footer(draw, 2)
        return page

    def _build_scene_page(self, project: VideoProject, scene: Any, page_number: int) -> Image.Image:
        page, draw = self._new_page()
        title_font = self._font(34)
        label_font = self._font(25)
        body_font = self._font(24)

        scene_number = int(getattr(scene, "scene_number", page_number - 2) or (page_number - 2))
        title = f"分镜 {scene_number}"
        scene_name = str(getattr(scene, "scene_name", "") or "").strip()
        if scene_name:
            title = f"{title}：{scene_name}"

        self._draw_text(draw, (self.MARGIN, 62), title, title_font, self.TITLE_COLOR)
        self._draw_rule(draw, 118)

        image = self._find_storyboard_image(project, scene_number)
        image_box = (self.MARGIN, 152, self.PAGE_SIZE[0] - self.MARGIN, 1060)
        if image:
            self._paste_fitted(page, image, image_box)
        else:
            self._draw_placeholder(draw, image_box, "暂无故事版图片")

        text_y = 1115
        self._draw_text(draw, (self.MARGIN, text_y), "场景描述", label_font, self.ACCENT_COLOR)
        text_y = self._draw_wrapped_text(
            draw,
            str(getattr(scene, "description", "") or "无").strip(),
            (self.MARGIN, text_y + 42),
            body_font,
            self.PAGE_SIZE[0] - self.MARGIN * 2,
            max_lines=7,
        )
        text_y += 30
        self._draw_text(draw, (self.MARGIN, text_y), "对话/旁白", label_font, self.ACCENT_COLOR)
        self._draw_wrapped_text(
            draw,
            str(getattr(scene, "dialogue", "") or "无").strip(),
            (self.MARGIN, text_y + 42),
            body_font,
            self.PAGE_SIZE[0] - self.MARGIN * 2,
            max_lines=8,
        )

        self._draw_footer(draw, page_number)
        return page

    def _find_character_image(self, project: VideoProject, character_name: str, fallback_index: int) -> Optional[Image.Image]:
        normalized = self._normalize_name(character_name)
        images = list(getattr(project, "character_reference_images", []) or [])
        for item in images:
            if self._normalize_name(getattr(item, "name", "")) == normalized:
                return self._load_image(getattr(item, "url", ""))
        if fallback_index < len(images):
            return self._load_image(getattr(images[fallback_index], "url", ""))
        return None

    def _find_storyboard_image(self, project: VideoProject, scene_number: int) -> Optional[Image.Image]:
        for item in list(getattr(project, "storyboard_images", []) or []):
            if int(getattr(item, "scene_number", 0) or 0) == scene_number:
                return self._load_image(getattr(item, "url", ""))
        return None

    def _load_image(self, url: str) -> Optional[Image.Image]:
        source = str(url or "").strip()
        if not source:
            return None
        try:
            if source.startswith("file://"):
                return Image.open(source[7:]).convert("RGB")
            response = requests.get(source, timeout=60)
            response.raise_for_status()
            return Image.open(io.BytesIO(response.content)).convert("RGB")
        except Exception as e:
            logger.warning(f"Failed to load image for comic PDF: {source}, error={str(e)}")
            return None

    def _paste_fitted(self, page: Image.Image, image: Image.Image, box: Tuple[int, int, int, int]) -> None:
        x1, y1, x2, y2 = box
        target_w = max(1, x2 - x1)
        target_h = max(1, y2 - y1)
        image = image.copy()
        image.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
        paste_x = x1 + (target_w - image.width) // 2
        paste_y = y1 + (target_h - image.height) // 2
        page.paste(image, (paste_x, paste_y))
        draw = ImageDraw.Draw(page)
        draw.rounded_rectangle(box, radius=18, outline=self.BORDER_COLOR, width=3)

    def _new_page(self) -> Tuple[Image.Image, ImageDraw.ImageDraw]:
        page = Image.new("RGB", self.PAGE_SIZE, "white")
        return page, ImageDraw.Draw(page)

    def _font(self, size: int) -> ImageFont.ImageFont:
        for path in self.FONT_CANDIDATES:
            if Path(path).exists():
                try:
                    return ImageFont.truetype(path, size=size)
                except Exception:
                    continue
        return ImageFont.load_default()

    def _wrap_text(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
        lines: List[str] = []
        for paragraph in str(text or "").splitlines() or [""]:
            current = ""
            for char in paragraph:
                candidate = f"{current}{char}"
                bbox = draw.textbbox((0, 0), candidate, font=font)
                if current and bbox[2] - bbox[0] > max_width:
                    lines.append(current)
                    current = char
                else:
                    current = candidate
            lines.append(current)
        return lines

    def _draw_wrapped_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        xy: Tuple[int, int],
        font: ImageFont.ImageFont,
        max_width: int,
        *,
        line_spacing: int = 10,
        max_lines: Optional[int] = None,
    ) -> int:
        x, y = xy
        lines = self._wrap_text(draw, text, font, max_width)
        if max_lines is not None and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = f"{lines[-1].rstrip()}..."
        for line in lines:
            self._draw_text(draw, (x, y), line, font, self.TEXT_COLOR)
            bbox = draw.textbbox((0, 0), line or "A", font=font)
            y += max(28, bbox[3] - bbox[1]) + line_spacing
        return y

    def _draw_text(
        self,
        draw: ImageDraw.ImageDraw,
        xy: Tuple[int, int],
        text: str,
        font: ImageFont.ImageFont,
        fill: Tuple[int, int, int],
    ) -> None:
        try:
            draw.text(xy, str(text or ""), font=font, fill=fill)
        except UnicodeEncodeError:
            draw.text(xy, str(text or "").encode("ascii", "replace").decode("ascii"), font=font, fill=fill)

    def _draw_rule(self, draw: ImageDraw.ImageDraw, y: int) -> None:
        draw.line((self.MARGIN, y, self.PAGE_SIZE[0] - self.MARGIN, y), fill=self.ACCENT_COLOR, width=4)

    def _draw_card(self, draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int]) -> None:
        draw.rounded_rectangle(box, radius=18, fill=(249, 250, 251), outline=self.BORDER_COLOR, width=2)

    def _draw_placeholder(self, draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], text: str) -> None:
        draw.rounded_rectangle(box, radius=18, fill=(243, 244, 246), outline=self.BORDER_COLOR, width=2)
        font = self._font(24)
        bbox = draw.textbbox((0, 0), text, font=font)
        x = box[0] + ((box[2] - box[0]) - (bbox[2] - bbox[0])) // 2
        y = box[1] + ((box[3] - box[1]) - (bbox[3] - bbox[1])) // 2
        self._draw_text(draw, (x, y), text, font, self.MUTED_COLOR)

    def _draw_footer(self, draw: ImageDraw.ImageDraw, page_number: int) -> None:
        footer = f"{page_number}"
        font = self._font(18)
        bbox = draw.textbbox((0, 0), footer, font=font)
        x = (self.PAGE_SIZE[0] - (bbox[2] - bbox[0])) // 2
        self._draw_text(draw, (x, self.PAGE_SIZE[1] - 64), footer, font, self.MUTED_COLOR)

    def _build_pdf_filename(self, title: str) -> str:
        safe = re.sub(r"\s+", "_", str(title or "").strip())
        safe = "".join(c for c in safe if c.isalnum() or c in "_-.")
        safe = safe.strip("._-") or "comic_storyboard"
        return f"{safe}.pdf"

    def _normalize_name(self, value: str) -> str:
        normalized = re.sub(r"\s+", "", str(value or "").strip().lower())
        return re.sub(r"[^0-9a-z\u4e00-\u9fff_-]+", "", normalized)


comic_pdf_service = ComicPDFService()
