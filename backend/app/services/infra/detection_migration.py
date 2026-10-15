"""Migrate retired detector settings without touching existing page content."""

from __future__ import annotations

from typing import Any

from app.models import ModelConfig
from sqlalchemy import select
from sqlalchemy.orm import Session


def unified_detection_config(config: dict[str, Any]) -> dict[str, Any]:
    # Paddle detector thresholds and YOLO grouping options have no equivalent
    # in the native layout model. Keep only explicit runtime preferences.
    result = {
        "text_threshold": 0.25,
        "bubble_threshold": 0.5,
        "panel_threshold": 0.5,
        "containment_threshold": 0.9,
    }
    device = config.get("device")
    if device is not None:
        result["device"] = "cuda:0" if str(device).startswith("gpu") else device
    options = config.get("text_classification")
    executable = config.get("python_executable")
    if executable is None and isinstance(options, dict):
        executable = options.get("python_executable")
    if executable:
        result["python_executable"] = executable
    return result


def migrate_retired_detection_configs(db: Session) -> int:
    """Preserve config IDs/defaults; do not run detection or invalidate results."""
    items = list(db.scalars(select(ModelConfig).where(
        ModelConfig.kind == "detection", ModelConfig.provider == "paddleocr",
    )))
    names = set(db.scalars(select(ModelConfig.name).where(ModelConfig.kind == "detection")))
    for item in items:
        name = f"RF-DETR（迁移 · {item.id[:8]}）"
        while name in names:
            name += " ·"
        names.add(name)
        item.name = name
        item.provider = "rfdetr"
        item.config = unified_detection_config(dict(item.config or {}))
    if items:
        db.commit()
    return len(items)
