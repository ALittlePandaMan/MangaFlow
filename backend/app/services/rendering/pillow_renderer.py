from __future__ import annotations

from functools import lru_cache
from math import hypot
from pathlib import Path
from typing import Any

import numpy as np
from app.core.config import get_settings
from app.services.base import ProviderCapabilities, Renderer
from app.services.layout import FontResolver, MangaLayoutEngine
from app.utils.geometry import order_quadrilateral, perspective_coefficients
from app.utils.image_metadata import load_rgb_with_metadata, save_png_with_metadata
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont

RENDER_OUTPUT_VERSION = 2


@lru_cache(maxsize=256)
def _render_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


class PillowRenderer(Renderer):
    _PERSPECTIVE_SUPERSAMPLE = 4.0
    _PERSPECTIVE_TILE_PIXEL_BUDGET = 16_000_000

    capabilities = ProviderCapabilities(
        name="pillow",
        provider_type="rendering",
        description="Non-destructive horizontal/true-vertical text renderer with font fitting",
        devices=["cpu"],
        extra={"vertical": True, "font_fallback": True, "transparent_layer": True, "perspective_warp": True},
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
            warp_quad = (
                order_quadrilateral(translated_polygon) if getattr(region, "perspective_warp", False) else None
            )
            tile_size = self._rectified_size(warp_quad) if warp_quad else None
            layout_bbox = [0.0, 0.0, tile_size[0], tile_size[1]] if tile_size else translated_bbox
            layout = self.layout_engine.layout(
                region.translated_text,
                layout_bbox,
                orientation=region.orientation,
                font_family=region.font_family,
                preferred_size=region.font_size,
                line_spacing=region.line_spacing,
                character_spacing=region.character_spacing,
                alignment=region.alignment,
                custom_font_path=(region.layout_data or {}).get("custom_font_path"),
                font_weight=region.font_weight,
                # Perspective mode lays text out in the normal rectangular
                # bbox first; the complete transparent tile is warped below.
                polygon=None if warp_quad else translated_polygon,
            )
            render_style = self._draw_region(
                layer,
                background,
                region,
                layout,
                translated_bbox,
                translated_polygon=translated_polygon,
                tile_size=tile_size,
            )
            region_layouts[region.id] = {
                **layout.to_dict(),
                "render_output_version": RENDER_OUTPUT_VERSION,
                "perspective_warp_applied": render_style["perspective_warp_applied"],
                "render_style": render_style,
            }
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
        *,
        translated_polygon: list[list[float]] | None = None,
        tile_size: tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        x, y, target_width, target_height = bbox or getattr(region, "translated_bbox", None) or region.bbox
        width, height = tile_size or (target_width, target_height)
        warp_quad = (
            order_quadrilateral(translated_polygon or []) if getattr(region, "perspective_warp", False) else None
        )
        raster_scale = PillowRenderer._perspective_raster_scale(
            width,
            height,
            target_width,
            target_height,
        ) if warp_quad else 1.0
        # Render to a local tile so region rotation remains non-destructive.
        # Perspective projection necessarily rasterizes the glyphs. Drawing
        # that source tile above the final resolution preserves edge detail
        # through rotation and the subsequent projective resampling.
        tile_width = max(1, round(width * raster_scale))
        tile_height = max(1, round(height * raster_scale))
        tile = Image.new("RGBA", (tile_width, tile_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tile)
        offset_x = max(0.0, (width - layout.width) / 2)
        offset_y = max(0.0, (height - layout.height) / 2)
        render_style = PillowRenderer._resolve_render_style(
            background,
            region,
            layout.font_size,
            [x, y, target_width, target_height],
        )
        fill = ImageColor.getrgb(render_style["text_color"]) + (round(255 * region.opacity),)
        stroke_fill = ImageColor.getrgb(render_style["stroke_color"]) + (round(255 * region.opacity),)
        stroke_width = max(0, round(float(render_style["stroke_width"]) * raster_scale))
        for placement in layout.placements:
            font = _render_font(placement.font_path, max(1, round(placement.font_size * raster_scale)))
            position = (
                (placement.x + offset_x) * raster_scale,
                (placement.y + offset_y) * raster_scale,
            )
            if placement.rotate:
                glyph_box = max(round(placement.font_size * 2 * raster_scale), 8)
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
            tile = PillowRenderer._unpremultiply_rgba(
                PillowRenderer._premultiply_rgba(tile).rotate(
                    -region.rotation,
                    expand=False,
                    resample=Image.Resampling.BICUBIC,
                )
            )
        perspective_warp_applied = bool(
            warp_quad and PillowRenderer._alpha_composite_warped(
                layer,
                tile,
                warp_quad,
                output_scale=raster_scale,
            )
        )
        if not perspective_warp_applied:
            if raster_scale > 1.0:
                tile = PillowRenderer._unpremultiply_rgba(
                    PillowRenderer._premultiply_rgba(tile).resize(
                        (max(1, round(width)), max(1, round(height))),
                        resample=Image.Resampling.LANCZOS,
                    )
                )
            layer.alpha_composite(tile, (round(x), round(y)))
        render_style["perspective_warp_requested"] = bool(getattr(region, "perspective_warp", False))
        render_style["perspective_warp_applied"] = perspective_warp_applied
        render_style["perspective_raster_scale"] = round(raster_scale, 3)
        return render_style

    @staticmethod
    def _perspective_raster_scale(
        width: float,
        height: float,
        target_width: float | None = None,
        target_height: float | None = None,
    ) -> float:
        """Return a bounded supersampling ratio for a perspective text tile."""

        area = max(
            1.0,
            float(width) * float(height),
            float(target_width or width) * float(target_height or height),
        )
        budget_scale = (PillowRenderer._PERSPECTIVE_TILE_PIXEL_BUDGET / area) ** 0.5
        return max(1.0, min(PillowRenderer._PERSPECTIVE_SUPERSAMPLE, budget_scale))

    @staticmethod
    def _rectified_size(quadrilateral: list[list[float]]) -> tuple[float, float]:
        """Estimate the unwarped rectangle from opposite edge lengths."""

        top, right, bottom, left = (
            hypot(
                quadrilateral[(index + 1) % 4][0] - quadrilateral[index][0],
                quadrilateral[(index + 1) % 4][1] - quadrilateral[index][1],
            )
            for index in range(4)
        )
        return max(2.0, top, bottom), max(2.0, left, right)

    @staticmethod
    def _alpha_composite_warped(
        layer: Image.Image,
        tile: Image.Image,
        quadrilateral: list[list[float]],
        *,
        output_scale: float = 1.0,
    ) -> bool:
        """Project a local RGBA tile into a validated page-space quadrilateral."""

        if tile.width < 2 or tile.height < 2:
            return False
        left = max(0, int(np.floor(min(point[0] for point in quadrilateral))))
        top = max(0, int(np.floor(min(point[1] for point in quadrilateral))))
        right = min(layer.width, int(np.ceil(max(point[0] for point in quadrilateral))))
        bottom = min(layer.height, int(np.ceil(max(point[1] for point in quadrilateral))))
        if right <= left or bottom <= top:
            return False

        final_size = (right - left, bottom - top)
        scale = max(1.0, float(output_scale))
        warped_size = (
            max(1, round(final_size[0] * scale)),
            max(1, round(final_size[1] * scale)),
        )
        destination = [
            [(point[0] - left) * scale, (point[1] - top) * scale]
            for point in quadrilateral
        ]
        source = [
            [0.0, 0.0],
            [float(tile.width - 1), 0.0],
            [float(tile.width - 1), float(tile.height - 1)],
            [0.0, float(tile.height - 1)],
        ]
        try:
            coefficients = perspective_coefficients(destination, source)
        except ValueError:
            return False
        warped = PillowRenderer._premultiply_rgba(tile).transform(
            warped_size,
            Image.Transform.PERSPECTIVE,
            coefficients,
            resample=Image.Resampling.BICUBIC,
            fillcolor=(0, 0, 0, 0),
        )
        warped = PillowRenderer._unpremultiply_rgba(warped)
        clip_mask = Image.new("L", warped.size, 0)
        ImageDraw.Draw(clip_mask).polygon(
            [(point[0], point[1]) for point in destination],
            fill=255,
        )
        warped.putalpha(ImageChops.multiply(warped.getchannel("A"), clip_mask))
        if warped.size != final_size:
            warped = PillowRenderer._premultiply_rgba(warped).resize(
                final_size,
                resample=Image.Resampling.LANCZOS,
            )
            warped = PillowRenderer._unpremultiply_rgba(warped)
        layer.alpha_composite(warped, (left, top))
        return True

    @staticmethod
    def _premultiply_rgba(image: Image.Image) -> Image.Image:
        pixels = np.asarray(image.convert("RGBA"), dtype=np.uint16).copy()
        alpha = pixels[..., 3:4]
        pixels[..., :3] = (pixels[..., :3] * alpha + 127) // 255
        return Image.fromarray(pixels.astype(np.uint8), "RGBA")

    @staticmethod
    def _unpremultiply_rgba(image: Image.Image) -> Image.Image:
        pixels = np.asarray(image.convert("RGBA"), dtype=np.uint16).copy()
        alpha = pixels[..., 3]
        visible = alpha > 0
        colors = pixels[..., :3]
        colors[visible] = np.minimum(
            255,
            (colors[visible] * 255 + alpha[visible, None] // 2) // alpha[visible, None],
        )
        colors[~visible] = 0
        return Image.fromarray(pixels.astype(np.uint8), "RGBA")

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
