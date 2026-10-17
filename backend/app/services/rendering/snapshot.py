"""Visual inputs that bind a generated text layer to the editor's current state."""
from copy import deepcopy

from app.services.layout.font_sizing import uses_automatic_font_size

VISUAL_FIELDS = (
    "bbox", "polygon", "translated_bbox", "translated_polygon", "translated_text",
    "orientation", "font_size", "font_family", "font_weight", "text_color",
    "stroke_color", "stroke_width", "alignment", "line_spacing", "character_spacing",
    "rotation", "perspective_warp", "opacity", "visible", "reading_order",
)


def render_input(region, *, font_size=None) -> dict:
    # Lock, OCR text and review flags do not alter the already rendered pixels.
    snapshot = {name: deepcopy(getattr(region, name, None)) for name in VISUAL_FIELDS}
    snapshot["custom_font_path"] = (region.layout_data or {}).get("custom_font_path")
    snapshot["auto_font_size"] = uses_automatic_font_size(region)
    snapshot["text_rotation"] = deepcopy((region.layout_data or {}).get("text_rotation"))
    if font_size is not None:
        snapshot["font_size"] = font_size
    return snapshot
