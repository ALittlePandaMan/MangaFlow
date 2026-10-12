"""Bounded residual-text inspection. Model scores are evidence, not truth."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from app.services.base import ProviderError
from app.services.inpainting.repair import _fill_color
from app.services.inpainting.structure import retry_structure_loss
from app.utils.image_metadata import load_rgb_with_metadata, save_png_with_metadata
from PIL import Image


def residual_mask(probability: np.ndarray, before: np.ndarray, allowed: np.ndarray) -> np.ndarray:
    if probability.shape != allowed.shape or before.shape != allowed.shape:
        raise ValueError("Repair verification shape mismatch")
    if not np.isfinite(probability).all() or not np.isfinite(before).all():
        raise ValueError("Non-finite repair verification output")
    signal = ((probability >= 0.55) & (before >= 0.25) & (allowed > 0)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(signal, connectivity=8)
    keep = np.zeros(n, np.uint8)
    for label in range(1, n):
        if stats[label, cv2.CC_STAT_AREA] >= 3:
            keep[label] = 255
    return keep[labels]


def verify_and_retry(
    provider: Any,
    source: Path,
    output: Path,
    regions: list[tuple[Path, dict]],
    predict: Callable[[Path], np.ndarray | None],
    *,
    auto_retry: bool = True,
    check_interruption: Callable[[], None] = lambda: None,
    confirm: Callable[[Path, np.ndarray], np.ndarray] | None = None,
    inspection_masks: list[np.ndarray] | None = None,
) -> list[dict]:
    """Inspect once and attempt at most one mask-bounded correction.

    Failed or non-improving retries never replace the first successful repair.
    Manual masks do not authorize automatic retries. Undetected text outside
    the original mask is explicitly left for review instead of growing it.
    """
    check_interruption()
    before = predict(source)
    after = predict(output)
    if before is None or after is None:
        return [{"status": "unavailable", "reason": "text_segmentation_unavailable"} for _ in regions]
    masks = []
    inspections = []
    reports = []
    retry = np.zeros(before.shape, np.uint8)
    protected = np.zeros(before.shape, np.uint8)
    if inspection_masks is not None and len(inspection_masks) != len(regions):
        raise ValueError("Inspection region count mismatch")
    for index, (path, _metadata) in enumerate(regions):
        mask = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if mask is None or mask.shape != before.shape:
            raise ValueError("Invalid verification mask")
        masks.append(mask)
        inspection = (inspection_masks[index] if inspection_masks is not None
                      else cv2.dilate(mask, np.ones((9, 9), np.uint8)))
        if inspection.shape != mask.shape:
            raise ValueError("Inspection mask shape mismatch")
        inspections.append(cv2.bitwise_or(inspection, mask))
    inspection_union = np.zeros(before.shape, np.uint8)
    for inspection in inspections:
        inspection_union |= inspection

    def inspect(path, probability):
        raw = residual_mask(probability, before, inspection_union)
        confirmed = raw if confirm is None or not cv2.countNonZero(raw) else confirm(path, raw)
        if confirmed.shape != raw.shape:
            raise ValueError("Local verification shape mismatch")
        # Local evidence cannot introduce candidates absent from the inspection.
        return raw, cv2.bitwise_and(raw, confirmed)

    raw, confirmed = inspect(output, after)

    def counts(mask, inspection, raw, confirmed):
        inside = cv2.bitwise_and(confirmed, mask)
        outside = (confirmed > 0) & (inspection > 0) & (mask == 0)
        uncertain = (raw > 0) & (inspection > 0) & (confirmed == 0)
        return cv2.countNonZero(inside), int(np.count_nonzero(outside)), int(np.count_nonzero(uncertain))

    def status(inside, outside, uncertain):
        return "residual" if inside or outside else "uncertain" if uncertain else "passed"

    for mask, inspection, (_, metadata) in zip(masks, inspections, regions, strict=True):
        residual = cv2.bitwise_and(confirmed, mask)
        pixels = cv2.countNonZero(residual)
        _, outside, uncertain = counts(mask, inspection, raw, confirmed)
        automatic = (
            metadata.get("version", 0) >= 2
            and metadata.get("method") != "manual_mask"
            and metadata.get("text_segmentation", {}).get("status") != "manual"
            and metadata.get("automatic_mask_sha256") == hashlib.sha256(mask.tobytes()).hexdigest()
        )
        if not automatic:
            protected |= mask
        reports.append(
            {
                "status": status(pixels, outside, uncertain),
                "residual_pixels": pixels,
                "outside_mask_pixels": outside,
                "uncertain_pixels": uncertain,
                "initial_residual_pixels": pixels,
                "retry": "not_needed" if not pixels else "disabled",
                "scope": "mask_and_neighbourhood",
                "retry_scope": "existing_mask_only",
                "local_confirmation": confirm is not None,
                "model": "comic-text-detector",
            }
        )
        if pixels and automatic and auto_retry:
            grown = cv2.dilate(residual, np.ones((3, 3), np.uint8))
            retry |= cv2.bitwise_and(grown, mask)
    retry &= cv2.bitwise_not(protected)
    if not cv2.countNonZero(retry):
        return reports
    attempted = [bool(cv2.countNonZero(cv2.bitwise_and(retry, mask))) for mask in masks]
    retry_mask = output.with_name(f".{output.stem}.retry-mask.png")
    retry_output = output.with_name(f".{output.stem}.retry.png")
    prepared_path = output.with_name(f".{output.stem}.retry-source.png")
    try:
        check_interruption()
        initial, image_metadata = load_rgb_with_metadata(output)
        prepared = np.asarray(initial).copy()
        complex_retry = retry.copy()
        # Reuse exact-mask fill decisions; complex overlap always wins.
        flat_area = np.zeros(retry.shape, bool)
        complex_area = np.zeros(retry.shape, bool)
        for mask, (_, metadata) in zip(masks, regions, strict=True):
            color = _fill_color(mask, metadata)
            area = (mask > 0) & (retry > 0)
            if color is None:
                complex_area |= area
            else:
                complex_area |= area & flat_area & np.any(prepared != color, axis=2)
                prepared[area] = color
                flat_area |= area
        flat_area &= ~complex_area
        prepared[~flat_area] = np.asarray(initial)[~flat_area]
        complex_retry[flat_area] = 0
        save_png_with_metadata(Image.fromarray(prepared), prepared_path, image_metadata)
        if cv2.countNonZero(complex_retry):
            if not cv2.imwrite(str(retry_mask), complex_retry):
                raise ValueError("Cannot save retry mask")
            try:
                provider.inpaint(prepared_path, retry_mask, retry_output, "background_complex")
            except RuntimeError as exc:
                # Local model failures must not discard an already valid first pass.
                # Interruption checks remain outside this provider-only guard.
                raise ProviderError("Repair retry inference failed") from exc
        else:
            save_png_with_metadata(Image.fromarray(prepared), retry_output, image_metadata)
        check_interruption()
        predicted = predict(retry_output)
        if predicted is None:
            raise ValueError("Retry verification unavailable")
        candidate, _ = load_rgb_with_metadata(retry_output)
        if candidate.size != initial.size:
            raise ValueError("Retry changed image dimensions")
        pixels = np.asarray(candidate)
        if not np.array_equal(pixels[retry == 0], np.asarray(initial)[retry == 0]):
            # Providers are not allowed to alter any unselected pixel.
            candidate = Image.composite(candidate, initial, Image.fromarray(retry))
            save_png_with_metadata(candidate, retry_output, image_metadata)
            predicted = predict(retry_output)
            if predicted is None:
                raise ValueError("Retry verification unavailable")
        final_raw, final_confirmed = inspect(retry_output, predicted)
        final_stats = [counts(mask, inspection, final_raw, final_confirmed)
                       for mask, inspection in zip(masks, inspections, strict=True)]
        final_counts = [item[0] for item in final_stats]
        initial_count = sum(item["residual_pixels"] for item in reports)
        improved = initial_count - sum(final_counts) >= max(3, int(np.ceil(initial_count * .2)))
        no_region_regression = all(
            inside <= report["residual_pixels"] and outside <= report["outside_mask_pixels"]
            and uncertain <= report["uncertain_pixels"]
            for (inside, outside, uncertain), report in zip(final_stats, reports, strict=True)
        )
        structure = retry_structure_loss(np.asarray(initial), np.asarray(candidate), retry)
        for report, touched in zip(reports, attempted, strict=True):
            if touched:
                report["structure_check"] = structure
        if improved and no_region_regression and not structure["damaged"]:
            save_png_with_metadata(candidate, retry_output, image_metadata)
            retry_output.replace(output)
            for (count, outside, uncertain), report, touched in zip(final_stats, reports, attempted, strict=True):
                report.update(
                    status=status(count, outside, uncertain),
                    residual_pixels=count,
                    outside_mask_pixels=outside,
                    uncertain_pixels=uncertain,
                    retry="accepted" if touched and report["initial_residual_pixels"] else report["retry"],
                )
        else:
            for report, touched in zip(reports, attempted, strict=True):
                if touched and report["initial_residual_pixels"]:
                    report["retry"] = "rejected_structure_change" if structure["damaged"] else "rejected_no_improvement"
    except (OSError, ValueError, cv2.error, ProviderError) as exc:
        for report, touched in zip(reports, attempted, strict=True):
            if touched and report["initial_residual_pixels"]:
                report.update(retry="failed", retry_error=type(exc).__name__)
    finally:
        retry_mask.unlink(missing_ok=True)
        retry_output.unlink(missing_ok=True)
        prepared_path.unlink(missing_ok=True)
    return reports
