"""Compact comparison previews with explicit original-pixel panel mappings."""
import io

from app.storage import get_storage
from fastapi import HTTPException
from PIL import Image, ImageDraw

MAX_PREVIEW_BYTES = 2 * 1024**2


def png_bytes(picture):
    output = io.BytesIO()
    picture.save(output, "PNG", compress_level=3)
    return output.getvalue()


def comparison_preview(page, box, max_size):
    if not page.rendered_path:
        raise HTTPException(409, "rendered is unavailable/stale; run its processing stage first")
    crop = [box[0], box[1], box[2]-box[0], box[3]-box[1]]
    sources = []
    for path in (page.original_path, page.rendered_path):
        with Image.open(get_storage().absolute(path)) as source:
            if source.size != (page.width, page.height):
                raise HTTPException(409, "Preview dimensions differ from the page; regenerate the rendered result")
            picture = source.crop(box).convert("RGB")
            picture.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            sources.append(picture)
    # Portrait pages are adjacent; landscape crops stack to avoid tiny panels.
    horizontal = crop[2] <= crop[3]
    while True:
        gap, header = 12, 24
        bounds = ((max(1, (max_size-gap)//2), max(1, max_size-header)) if horizontal
                  else (max_size, max(1, (max_size-gap)//2-header)))
        pictures = [source.copy() for source in sources]
        for picture in pictures:
            picture.thumbnail(bounds, Image.Resampling.LANCZOS)
        width, height = pictures[0].size
        size = (width*2+gap, height+header) if horizontal else (width, (height+header)*2+gap)
        canvas = Image.new("RGB", size, "#f1f5f9")
        draw = ImageDraw.Draw(canvas)
        panels = []
        for index, (kind, picture) in enumerate(zip(("original", "rendered"), pictures, strict=True)):
            x, y = (index*(width+gap), 0) if horizontal else (0, index*(height+header+gap))
            draw.text((x+4, y+5), "ORIGINAL" if kind == "original" else "TRANSLATED", fill="#334155")
            canvas.paste(picture, (x, y+header))
            panels.append({"kind": kind, "crop": crop, "display_bounds": [x, y+header, width, height],
                "display_size": [width, height],
                "original_pixels_per_display_pixel": [crop[2]/width, crop[3]/height]})
        data = png_bytes(canvas)
        if len(data) <= MAX_PREVIEW_BYTES:
            return data, {"display_size": list(canvas.size), "panels": panels,
                "layout": "side_by_side" if horizontal else "stacked",
                "coordinate_space": "original_image_pixels"}
        max_size = max(64, max_size*3//4)
