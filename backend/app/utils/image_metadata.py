from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image


def load_rgb_with_metadata(path: Path) -> tuple[Image.Image, dict[str, Any]]:
    """Load RGB pixels and the display metadata needed to preserve their colors."""
    with Image.open(path) as source:
        metadata = {
            key: source.info[key]
            for key in ("icc_profile", "dpi")
            if source.info.get(key) is not None
        }
        return source.convert("RGB"), metadata


def save_png_with_metadata(image: Image.Image, path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", **metadata)
