from __future__ import annotations

from math import isfinite
from typing import Any


def automatic_font_size(layout_data: dict | None, font_size: float) -> bool:
    """Track a user adjustment, not a cap derived from the last computed size.

    Legacy snapshots can prove a manual size edit after rendering. Otherwise
    old automatically generated regions retain the default automatic behavior.
    """
    data = layout_data or {}
    explicit = data.get("auto_font_size")
    if isinstance(explicit, bool):
        return explicit
    snapshot = data.get("render_input")
    previous = snapshot.get("font_size") if isinstance(snapshot, dict) else data.get("font_size")
    return not (isinstance(previous, (int, float)) and isfinite(previous)
                and abs(previous - font_size) > .01)


def uses_automatic_font_size(region: Any) -> bool:
    explicit = getattr(region, "auto_font_size", None)
    if isinstance(explicit, bool):
        return explicit
    return automatic_font_size(getattr(region, "layout_data", None), getattr(region, "font_size", 24))
