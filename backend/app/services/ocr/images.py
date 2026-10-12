from __future__ import annotations

import io
import math
from pathlib import Path

from app.services.base import ProviderError
from app.services.ocr.providers import _crop
from PIL import Image


def region_png(image_path: Path, bbox: list[float], padding: int = 4) -> bytes:
    """Encode only the detected region, with bounded padding and upload dimensions."""
    if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox) or bbox[2] <= 0 or bbox[3] <= 0:
        raise ProviderError("OCR 区域无效，请重新选择文本区域。")
    try:
        crop = _crop(image_path, bbox, max(0, min(padding, 64)))
        if not crop.width or not crop.height:
            raise ValueError("empty crop")
    except ValueError as exc:
        raise ProviderError("OCR 区域不在图片范围内。") from exc
    crop.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    return buffer.getvalue()
