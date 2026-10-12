"""Bounded, ID-stable scene context for translation; images are explicit opt-in."""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image


def page_context(regions, *, character_budget: int = 12000) -> list[dict]:
    remaining = max(0, min(30000, character_budget))
    items = []
    for region in sorted(regions, key=lambda item: item.reading_order):
        if remaining <= 0 or len(items) >= 200:
            break
        source = (region.source_text or "")[: min(remaining, 2000)]
        remaining -= len(source)
        translation = (region.translated_text or "")[: min(remaining, 2000)]
        remaining -= len(translation)
        items.append(
            {
                "id": region.region_key,
                "source": source,
                "translation": translation,
                "order": region.reading_order,
                "panel_id": region.panel_id,
                "bubble_id": region.bubble_id,
                "bbox": region.bbox,
                "orientation": region.orientation,
            }
        )
    return items


def page_image_data_url(path: Path, max_edge: int = 1536) -> str:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
    rgb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    # Re-encoding deliberately drops EXIF and other private image metadata.
    rgb.save(buffer, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def attach_page_image(payload: dict, protocol: str, data_url: str) -> None:
    if not data_url.startswith("data:image/jpeg;base64,"):
        raise ValueError("Unsupported translation context image")
    if protocol == "responses":
        text = payload["input"]
        payload["input"] = [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": text}, {"type": "input_image", "image_url": data_url}],
            }
        ]
    else:
        message = payload["messages"][-1]
        content = [{"type": "text", "text": message["content"]}]
        if protocol == "anthropic":
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": data_url.split(",", 1)[1]},
                }
            )
        else:
            content.append({"type": "image_url", "image_url": {"url": data_url, "detail": "high"}})
        message["content"] = content
