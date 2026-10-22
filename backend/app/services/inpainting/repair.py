"""Deterministic flat-balloon repair followed by one complex-mask model call."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from app.services.inpainting.masking import MASK_ALGORITHM_VERSION
from app.utils.image_metadata import load_rgb_with_metadata, save_png_with_metadata
from PIL import Image


def _fill_color(mask: np.ndarray, metadata: dict[str, Any]) -> list[int] | None:
    repair = metadata.get("repair", {})
    constraint = metadata.get("constraint", {})
    background = metadata.get("background", {})
    integrity = metadata.get("mask_integrity", {})
    if not all(isinstance(item, dict) for item in (repair, constraint, background, integrity)):
        return None
    trusted_balloon = metadata.get("method") in {"balloon_glyph", "source_polygon_union"} and constraint.get(
        "status"
    ) in {"applied", "relaxed"}
    verified_paper = metadata.get("method") == "conservative_glyph" and background.get("scope") == "local_paper"
    if (
        metadata.get("version") != MASK_ALGORITHM_VERSION
        or integrity.get("status") in {"partial", "uncertain"}
        or not (trusted_balloon or verified_paper)
        or not background.get("uniform")
        or repair.get("strategy") != "uniform_fill"
        or repair.get("mask_sha256") != hashlib.sha256(mask.tobytes()).hexdigest()
    ):
        return None
    color = repair.get("color_rgb")
    if not isinstance(color, list) or len(color) != 3 or not all(type(c) is int and 0 <= c <= 255 for c in color):
        return None
    return color


def repair_masks(
    provider: Any,
    source_path: Path,
    combined_path: Path,
    output_path: Path,
    regions: list[tuple[Path, dict[str, Any]]],
) -> Path:
    """Keep old/manual masks on the configured provider; fill only proven flats.

    Every fill is mask-specific. Overlapping complex masks and conflicting
    colours take precedence over fills, regardless of region order.
    """
    if not any(isinstance(meta.get("repair"), dict) for _, meta in regions):
        return provider.inpaint(source_path, combined_path, output_path, "background_complex")
    source, metadata = load_rgb_with_metadata(source_path)
    combined = cv2.imread(str(combined_path), cv2.IMREAD_GRAYSCALE)
    shape = (source.height, source.width)
    if combined is None or combined.shape != shape:
        raise ValueError("Combined mask dimensions must match the image")
    fill_area = np.zeros(shape, bool)
    complex_area = np.zeros(shape, bool)
    accounted = np.zeros(shape, bool)
    colors = np.zeros((*shape, 3), np.uint8)
    for path, generation in regions:
        mask = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if mask is None or mask.shape != shape:
            raise ValueError("Region mask dimensions must match the image")
        area = (mask > 0) & (combined > 0)
        accounted |= area
        color = _fill_color(mask, generation)
        if color is None:
            complex_area |= area
            continue
        complex_area |= area & fill_area & np.any(colors != color, axis=2)
        colors[area] = color
        fill_area |= area
    complex_area |= (combined > 0) & ~accounted
    fill_area &= ~complex_area
    if not np.any(fill_area):
        return provider.inpaint(source_path, combined_path, output_path, "background_complex")
    filled = np.asarray(source).copy()
    filled[fill_area] = colors[fill_area]
    prepared = Image.fromarray(filled)
    if not np.any(complex_area):
        save_png_with_metadata(prepared, output_path, metadata)
        return output_path
    temporary_source = output_path.with_name(f".{output_path.stem}.flat-source.png")
    temporary_mask = output_path.with_name(f".{output_path.stem}.complex-mask.png")
    try:
        save_png_with_metadata(prepared, temporary_source, metadata, compress_level=1)
        if not cv2.imwrite(str(temporary_mask), np.where(complex_area, 255, 0).astype(np.uint8)):
            raise ValueError("Cannot write complex repair mask")
        provider.inpaint(temporary_source, temporary_mask, output_path, "background_complex")
        generated, _ = load_rgb_with_metadata(output_path)
        if generated.size != source.size:
            raise ValueError("Inpainting changed page dimensions")
        # In particular, the model must not reintroduce grey seams in a flat
        # balloon or change unselected pixels in an incremental repair.
        result = Image.composite(generated, prepared, Image.fromarray(np.where(complex_area, 255, 0).astype(np.uint8)))
        save_png_with_metadata(result, output_path, metadata)
    finally:
        temporary_source.unlink(missing_ok=True)
        temporary_mask.unlink(missing_ok=True)
    return output_path
