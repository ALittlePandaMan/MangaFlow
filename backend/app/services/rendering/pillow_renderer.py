from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from app.core.config import get_settings
from app.services.base import ProviderCapabilities, Renderer
from app.services.layout import FontResolver, MangaLayoutEngine
from app.utils.image_metadata import load_rgb_with_metadata, save_png_with_metadata
from PIL import Image, ImageColor, ImageDraw, ImageFont


class PillowRenderer(Renderer):
    capabilities = ProviderCapabilities(
        name="pillow",
        provider_type="rendering",
        description="Non-destructive horizontal/true-vertical text renderer with font fitting",
        devices=["cpu"],
        extra={"vertical": True, "font_fallback": True, "transparent_layer": True},
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.layout_engine = MangaLayoutEngine(
            FontResolver([get_settings().data_dir / "fonts", get_settings().data_dir / "projects"]),
            min_font_size=int((config or {}).get("min_font_size", 10)),
        )

    def render(
        self,
        background_path: Path,
        regions: list[Any],
        output_path: Path,
        text_layer_path: Path,
    ) -> dict[str, Any]:
        self.ensure_loaded()
        background_rgb, metadata = load_rgb_with_metadata(background_path)
        background = background_rgb.convert("RGBA")
        layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
        region_layouts: dict[str, dict[str, Any]] = {}
        for region in regions:
            if not getattr(region, "visible", True) or not region.translated_text.strip():
                continue
            translated_bbox = getattr(region, "translated_bbox", None) or region.bbox
            translated_polygon = getattr(region, "translated_polygon", None) or region.polygon
            layout = self.layout_engine.layout(
                region.translated_text,
                translated_bbox,
                orientation=region.orientation,
                font_family=region.font_family,
                preferred_size=region.font_size,
                line_spacing=region.line_spacing,
                character_spacing=region.character_spacing,
                alignment=region.alignment,
                custom_font_path=(region.layout_data or {}).get("custom_font_path"),
                font_weight=region.font_weight,
                polygon=translated_polygon,
            )
            render_style = self._draw_region(layer, background, region, layout, translated_bbox)
            region_layouts[region.id] = {**layout.to_dict(), "render_style": render_style}
        composite = Image.alpha_composite(background, layer)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        text_layer_path.parent.mkdir(parents=True, exist_ok=True)
        layer.save(text_layer_path, "PNG")
        save_png_with_metadata(composite.convert("RGB"), output_path, metadata)
        return {"layouts": region_layouts, "output_path": output_path, "text_layer_path": text_layer_path}

    @staticmethod
    def _draw_region(
        layer: Image.Image,
        background: Image.Image,
        region: Any,
        layout: Any,
        bbox: list[float] | None = None,
    ) -> dict[str, Any]:
        x, y, width, height = bbox or getattr(region, "translated_bbox", None) or region.bbox
        # Render to a local tile so region rotation remains non-destructive.
        tile_width = max(1, round(width))
        tile_height = max(1, round(height))
        tile = Image.new("RGBA", (tile_width, tile_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tile)
        offset_x = max(0.0, (width - layout.width) / 2)
        offset_y = max(0.0, (height - layout.height) / 2)
        render_style = PillowRenderer._resolve_render_style(background, region, layout.font_size, [x, y, width, height])
        fill = ImageColor.getrgb(render_style["text_color"]) + (round(255 * region.opacity),)
        stroke_fill = ImageColor.getrgb(render_style["stroke_color"]) + (round(255 * region.opacity),)
        stroke_width = int(render_style["stroke_width"])
        for placement in layout.placements:
            font = ImageFont.truetype(placement.font_path, placement.font_size)
            position = (placement.x + offset_x, placement.y + offset_y)
            if placement.rotate:
                glyph_box = max(placement.font_size * 2, 8)
                glyph = Image.new("RGBA", (glyph_box, glyph_box), (0, 0, 0, 0))
                ImageDraw.Draw(glyph).text(
                    (glyph_box / 2, glyph_box / 2),
                    placement.text,
                    font=font,
                    fill=fill,
                    stroke_width=stroke_width,
                    stroke_fill=stroke_fill,
                    anchor="mm",
                )
                glyph = glyph.rotate(-placement.rotate, expand=False, resample=Image.Resampling.BICUBIC)
                tile.alpha_composite(glyph, (round(position[0] - glyph_box / 4), round(position[1] - glyph_box / 4)))
            else:
                draw.text(
                    position,
                    placement.text,
                    font=font,
                    fill=fill,
                    stroke_width=stroke_width,
                    stroke_fill=stroke_fill,
                )
        if abs(region.rotation) > 0.01:
            tile = tile.rotate(-region.rotation, expand=False, resample=Image.Resampling.BICUBIC)
        layer.alpha_composite(tile, (round(x), round(y)))
        return render_style

    @staticmethod
    def _resolve_render_style(
        background: Image.Image,
        region: Any,
        font_size: int,
        bbox: list[float] | None = None,
    ) -> dict[str, Any]:
        text_color = str(region.text_color)
        stroke_color = str(region.stroke_color)
        stroke_width = max(0, round(float(region.stroke_width)))
        x, y, width, height = bbox or getattr(region, "translated_bbox", None) or region.bbox
        left = max(0, int(np.floor(x)))
        top = max(0, int(np.floor(y)))
        right = min(background.width, int(np.ceil(x + width)))
        bottom = min(background.height, int(np.ceil(y + height)))
        if right <= left or bottom <= top:
            background_luminance = 1.0
        else:
            pixels = np.asarray(background.crop((left, top, right, bottom)).convert("RGB"), dtype=np.float32)
            normalized = pixels / 255.0
            linear = np.where(
                normalized <= 0.04045,
                normalized / 12.92,
                ((normalized + 0.055) / 1.055) ** 2.4,
            )
            luminance = linear[..., 0] * 0.2126 + linear[..., 1] * 0.7152 + linear[..., 2] * 0.0722
            background_luminance = float(np.median(luminance))
        text_luminance = PillowRenderer._color_luminance(text_color)
        contrast = (max(text_luminance, background_luminance) + 0.05) / (
            min(text_luminance, background_luminance) + 0.05
        )
        return {
            "text_color": text_color,
            "stroke_color": stroke_color,
            "stroke_width": stroke_width,
            # Explicit editor values are authoritative. In particular, zero
            # means no outline; contrast assistance must never silently turn
            # it into a visible stroke.
            "auto_contrast": False,
            "background_luminance": round(background_luminance, 4),
            "contrast_ratio": round(contrast, 3),
            "source_text_color": str(region.text_color),
            "source_stroke_color": str(region.stroke_color),
            "source_stroke_width": float(region.stroke_width),
        }

    @staticmethod
    def _color_luminance(color: str) -> float:
        normalized = np.asarray(ImageColor.getrgb(color), dtype=np.float32) / 255.0
        linear = np.where(
            normalized <= 0.04045,
            normalized / 12.92,
            ((normalized + 0.055) / 1.055) ** 2.4,
        )
        return float(linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722)
