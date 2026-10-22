from __future__ import annotations

import io
from pathlib import Path

from app.services.base import ProviderError
from app.services.ocr.crops import crop_region
from PIL import Image


def region_png(image_path: Path, bbox: list[float], padding: int = 4, *, polygon=None, orientation=None, crop_angle=None) -> bytes:
    """Encode only selected pixels, with white padding and bounded upload dimensions."""
    try:
        crop = crop_region(image_path, bbox, padding, polygon=polygon, orientation=orientation, crop_angle=crop_angle)
        if not crop.width or not crop.height:
            raise ValueError("empty crop")
    except ValueError as exc:
        raise ProviderError("OCR 区域不在图片范围内。") from exc
    crop.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    return buffer.getvalue()
