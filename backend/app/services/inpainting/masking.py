from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw


def load_text_mask_source(image_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Decode and convert a page once for a complete mask-generation pass."""
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot read image: {image_path}")
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    return image, lab


def create_region_mask(
    width: int,
    height: int,
    polygon: list[list[float]],
    output_path: Path,
    *,
    expand: int = 2,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mask = Image.new("L", (width, height), 0)
    points = [(round(point[0]), round(point[1])) for point in polygon]
    if len(points) >= 3:
        ImageDraw.Draw(mask).polygon(points, fill=255)
    mask.save(output_path)
    if expand > 0:
        process_mask(output_path, output_path, "dilate", expand)
    return output_path


def create_text_mask(
    image_path: Path,
    polygon: list[list[float]],
    output_path: Path,
    *,
    expand: int = 2,
    color_threshold: float = 18.0,
    source_image: np.ndarray | None = None,
    source_lab: np.ndarray | None = None,
    balloon_mask: np.ndarray | None = None,
    balloon_context: dict[str, Any] | None = None,
    conservative: bool = False,
    glyph_output_path: Path | None = None,
) -> dict[str, Any]:
    """Create a glyph-level mask by learning colors around a detected text line."""
    if source_image is None:
        image, lab = load_text_mask_source(image_path)
    else:
        image = source_image
        lab = source_lab if source_lab is not None else cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    height, width = image.shape[:2]
    points = np.asarray(
        [[np.clip(round(point[0]), 0, width - 1), np.clip(round(point[1]), 0, height - 1)] for point in polygon],
        dtype=np.int32,
    )
    if len(points) < 3:
        raise ValueError("Text mask polygon needs at least three points")

    region = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(region, [points], 255)
    x, y, box_width, box_height = cv2.boundingRect(points)
    ring_radius = max(4, min(12, round(min(box_width, box_height) * 0.2)))
    ring_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ring_radius * 2 + 1, ring_radius * 2 + 1))
    outside_ring = cv2.subtract(cv2.dilate(region, ring_kernel, iterations=1), region)

    samples = lab[outside_ring > 0]
    if len(samples) < 16:
        samples = lab[region > 0]
    centers, counts = _dominant_colors(samples)
    ring_distances = np.min(
        np.linalg.norm(samples[:, None, :] - centers[None, :, :], axis=2),
        axis=1,
    )
    dominant_ratio = float(np.max(counts) / max(1, np.sum(counts)))
    background_residual = float(np.percentile(ring_distances, 80))
    uniform_background = background_residual <= 13.0

    inside_samples = lab[region > 0]
    inside_centers, inside_counts = _dominant_colors(inside_samples)
    inside_dominant_ratio = float(inside_counts[0] / max(1, len(inside_samples)))
    inside_outside_distance = float(
        np.min(np.linalg.norm(inside_centers[0][None, :] - centers, axis=1))
    )
    enclosed_background = inside_dominant_ratio >= 0.42 and inside_outside_distance >= 24.0
    # A speech-balloon outline can appear in the sampling ring as a secondary
    # dark "background" cluster. Using that cluster would make black glyphs
    # invisible to the color-distance detector, so constrained balloon repair
    # learns only the dominant local fill color.
    mask_centers = inside_centers[:1] if enclosed_background else centers
    if balloon_mask is not None and not enclosed_background:
        mask_centers = centers[:1]

    crop_lab = lab[y : y + box_height, x : x + box_width]
    crop_region = region[y : y + box_height, x : x + box_width]
    distances = np.min(
        np.linalg.norm(crop_lab[:, :, None, :] - mask_centers[None, None, :, :], axis=3),
        axis=2,
    )
    region_distances = distances[crop_region > 0]
    adaptive_threshold = max(float(color_threshold), float(np.percentile(region_distances, 72)))
    candidate = np.where((distances >= adaptive_threshold) & (crop_region > 0), 255, 0).astype(np.uint8)
    candidate = _filter_text_components(candidate, int(cv2.countNonZero(crop_region)))

    coverage = cv2.countNonZero(candidate) / max(1, cv2.countNonZero(crop_region))
    if coverage > 0.48:
        stricter = max(adaptive_threshold, float(np.percentile(region_distances, 86)))
        candidate = np.where((distances >= stricter) & (crop_region > 0), 255, 0).astype(np.uint8)
        candidate = _filter_text_components(candidate, int(cv2.countNonZero(crop_region)))
        adaptive_threshold = stricter
    glyph_evidence = candidate.copy()
    if cv2.countNonZero(candidate) == 0 and not conservative:
        # Low-contrast edge case: retaining the old polygon behavior is safer than an empty mask.
        candidate = crop_region.copy()

    candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
    stylized_complex = (
        not conservative
        and not uniform_background
        and not enclosed_background
        and box_width * box_height >= 750
        and min(box_width, box_height) >= 12
    )
    method = "conservative_glyph" if conservative else "local_color_distance"
    if uniform_background and not enclosed_background and not conservative:
        # The detector already returns a tight text-line polygon. On a locally
        # uniform bubble/background, filling the complete polygon reliably
        # removes anti-aliasing, furigana and outlines without harming detail.
        candidate = crop_region.copy()
        method = "uniform_background_polygon"
    elif expand > 0:
        dynamic_expand = max(int(expand), min(6, round(min(box_width, box_height) * 0.065)))
        size = dynamic_expand * 2 + 1
        candidate = cv2.dilate(
            candidate,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)),
            iterations=1,
        )
        if not enclosed_background:
            close_size = max(3, min(11, dynamic_expand * 2 + 1))
            candidate = cv2.morphologyEx(
                candidate,
                cv2.MORPH_CLOSE,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size)),
            )
            candidate = _fill_holes(candidate)
        else:
            method = "enclosed_background_color"
    candidate = cv2.bitwise_and(candidate, crop_region)
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[y : y + box_height, x : x + box_width] = candidate
    mask_expansion = 1.0
    if stylized_complex:
        # Large display lettering commonly contains shadows, strokes and
        # multi-color outlines. A color-selected glyph mask leaves those
        # decorations behind, while LaMa performs much better when it can
        # rebuild the complete, slightly padded title area in one pass.
        dynamic_expand = max(int(expand), 5, min(10, round(min(box_width, box_height) * 0.1)))
        size = dynamic_expand * 2 + 1
        mask = cv2.dilate(
            region,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)),
            iterations=1,
        )
        method = "stylized_text_polygon"
        mask_expansion = cv2.countNonZero(mask) / max(1, cv2.countNonZero(region))

    full_glyph_evidence = np.zeros((height, width), dtype=np.uint8)
    full_glyph_evidence[y : y + box_height, x : x + box_width] = glyph_evidence
    if glyph_output_path is not None:
        glyph_output_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(glyph_output_path), full_glyph_evidence):
            raise ValueError(f"Cannot write glyph evidence: {glyph_output_path}")

    constraint_metadata: dict[str, Any] | None = None
    if balloon_mask is not None:
        mask, constraint_metadata = apply_balloon_constraint(
            mask,
            balloon_mask,
            glyph_evidence=full_glyph_evidence,
            source_lab=lab,
            source_polygons=[polygon],
            context=balloon_context,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), mask):
        raise ValueError(f"Cannot write mask: {output_path}")

    metadata = {
        "method": method,
        "coverage": round(
            cv2.countNonZero(cv2.bitwise_and(mask, region)) / max(1, cv2.countNonZero(region)),
            4,
        ),
        "mask_expansion": round(mask_expansion, 4),
        "threshold": round(adaptive_threshold, 2),
        "background_dominant_ratio": round(dominant_ratio, 3),
        "background_residual": round(background_residual, 2),
        "enclosed_background": enclosed_background,
        "inside_dominant_ratio": round(inside_dominant_ratio, 3),
        "suggested_region_type": "background_complex",
    }
    if constraint_metadata is not None:
        metadata["constraint"] = constraint_metadata
    return metadata


def create_text_mask_union(
    image_path: Path,
    polygons: list[list[list[float]]],
    output_path: Path,
    *,
    expand: int = 2,
    color_threshold: float = 18.0,
    source_image: np.ndarray | None = None,
    source_lab: np.ndarray | None = None,
    balloon_mask: np.ndarray | None = None,
    balloon_context: dict[str, Any] | None = None,
    conservative: bool = False,
) -> dict[str, Any]:
    """Create and union per-line masks without filling their enclosing box."""

    if not polygons:
        raise ValueError("Text mask union needs at least one polygon")
    if len(polygons) == 1:
        return create_text_mask(
            image_path,
            polygons[0],
            output_path,
            expand=expand,
            color_threshold=color_threshold,
            source_image=source_image,
            source_lab=source_lab,
            balloon_mask=balloon_mask,
            balloon_context=balloon_context,
            conservative=conservative,
        )

    resolved_source_image = source_image
    resolved_source_lab = source_lab
    if resolved_source_image is None:
        resolved_source_image, resolved_source_lab = load_text_mask_source(image_path)
    elif resolved_source_lab is None:
        resolved_source_lab = cv2.cvtColor(resolved_source_image, cv2.COLOR_BGR2LAB).astype(np.float32)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined: np.ndarray | None = None
    combined_glyphs: np.ndarray | None = None
    component_metadata: list[dict[str, Any]] = []
    temporary_paths: list[Path] = []
    try:
        for index, polygon in enumerate(polygons):
            temporary = output_path.with_name(f".{output_path.stem}.source-{index}{output_path.suffix}")
            temporary_glyphs = output_path.with_name(
                f".{output_path.stem}.source-{index}.glyphs{output_path.suffix}"
            )
            temporary_paths.extend((temporary, temporary_glyphs))
            component_metadata.append(
                create_text_mask(
                    image_path,
                    polygon,
                    temporary,
                    expand=expand,
                    color_threshold=color_threshold,
                    source_image=resolved_source_image,
                    source_lab=resolved_source_lab,
                    # A grouped region shares one balloon instance. Applying
                    # its full-page distance transform for every source line
                    # is needlessly expensive, so constrain the union once.
                    balloon_mask=None,
                    balloon_context=None,
                    conservative=conservative,
                    glyph_output_path=temporary_glyphs,
                )
            )
            component = cv2.imread(str(temporary), cv2.IMREAD_GRAYSCALE)
            component_glyphs = cv2.imread(str(temporary_glyphs), cv2.IMREAD_GRAYSCALE)
            if component is None or component_glyphs is None:
                raise ValueError(f"Cannot read component mask: {temporary}")
            if combined is not None and component.shape != combined.shape:
                raise ValueError("Text mask components must have matching dimensions")
            combined = component if combined is None else cv2.bitwise_or(combined, component)
            combined_glyphs = (
                component_glyphs
                if combined_glyphs is None
                else cv2.bitwise_or(combined_glyphs, component_glyphs)
            )
        if combined is None:
            raise ValueError(f"Cannot create mask: {output_path}")
        constraint_metadata: dict[str, Any] | None = None
        if balloon_mask is not None:
            combined, constraint_metadata = apply_balloon_constraint(
                combined,
                balloon_mask,
                glyph_evidence=combined_glyphs,
                source_lab=resolved_source_lab,
                source_polygons=polygons,
                context=balloon_context,
            )
        if not cv2.imwrite(str(output_path), combined):
            raise ValueError(f"Cannot write mask: {output_path}")
    finally:
        for temporary in temporary_paths:
            temporary.unlink(missing_ok=True)

    metadata: dict[str, Any] = {
        "method": "source_polygon_union",
        "source_count": len(polygons),
        "components": component_metadata,
        "suggested_region_type": "background_complex",
    }
    if constraint_metadata is not None:
        metadata["constraint"] = constraint_metadata
    return metadata


def apply_balloon_constraint(
    mask: np.ndarray,
    balloon_mask: np.ndarray,
    *,
    glyph_evidence: np.ndarray | None = None,
    source_lab: np.ndarray | None = None,
    source_polygons: list[list[list[float]]] | None = None,
    context: dict[str, Any] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Clip a repair mask to an adaptive, shape-independent balloon interior."""

    if mask.ndim != 2 or balloon_mask.ndim != 2 or mask.shape != balloon_mask.shape:
        raise ValueError("Text and balloon masks must be equally sized 2D arrays")
    full_binary_mask = np.where(mask > 0, 255, 0).astype(np.uint8)
    full_binary_balloon = np.where(balloon_mask > 0, 255, 0).astype(np.uint8)
    raw_area = cv2.countNonZero(full_binary_mask)
    balloon_area = cv2.countNonZero(full_binary_balloon)
    if raw_area == 0:
        return full_binary_mask, {
            "version": 1,
            "status": "fallback",
            "reason": "empty_text_mask",
        }
    if balloon_area == 0:
        # A missing boundary must fail closed. Returning the original repair
        # rectangle here would erase arbitrary artwork while claiming it was
        # balloon-constrained.
        return np.zeros_like(full_binary_mask), {
            "version": 1,
            "status": "fallback",
            "reason": "empty_balloon_mask",
            "raw_mask_area": raw_area,
            "final_mask_area": 0,
            "outside_pixels_after": 0,
        }

    page_height, page_width = full_binary_mask.shape
    balloon_points = cv2.findNonZero(full_binary_balloon)
    if balloon_points is None:  # pragma: no cover - guarded by balloon_area
        raise ValueError("Balloon constraint mask cannot be empty")
    offset_x, offset_y, roi_width, roi_height = cv2.boundingRect(balloon_points)
    roi = np.s_[offset_y : offset_y + roi_height, offset_x : offset_x + roi_width]
    binary_mask = full_binary_mask[roi]
    binary_balloon = full_binary_balloon[roi]

    # Geometry artifacts are compact instances. Keeping the expensive distance
    # and LAB calculations inside that compact bounding box makes runtime scale
    # with the balloon rather than with the whole manga page. The zero border
    # preserves correct distances when an instance touches its crop edge.
    padded_balloon = np.pad((binary_balloon > 0).astype(np.uint8), 1, mode="constant")
    distance = cv2.distanceTransform(padded_balloon, cv2.DIST_L2, 5)[1:-1, 1:-1]
    peak_distance = float(distance.max())
    scale = float(np.sqrt((page_height * page_width) / 1_000_000))
    base_margin = int(np.clip(round(1.5 * scale), 1, 4))
    maximum_margin = max(1, min(int(round(12 * scale)), int(np.floor(peak_distance * 0.25))))
    source_lab_roi = (
        source_lab[roi]
        if source_lab is not None and source_lab.shape[:2] == full_binary_mask.shape
        else None
    )
    shifted_polygons: list[list[list[float]]] = []
    source_polygon_points: list[np.ndarray] = []
    for polygon in source_polygons or []:
        try:
            original = np.asarray(
                [[float(point[0]), float(point[1])] for point in polygon],
                dtype=np.float32,
            )
            shifted = (original - np.asarray([offset_x, offset_y], dtype=np.float32)).tolist()
        except (TypeError, ValueError, IndexError):
            continue
        if len(shifted) >= 3 and all(np.isfinite(point).all() for point in np.asarray(shifted)):
            shifted_polygons.append(shifted)
            source_polygon_points.append(original)
    outline_width, outline_reliable = _estimate_balloon_outline_width(
        distance,
        binary_balloon,
        source_lab_roi,
        shifted_polygons,
        base_margin=base_margin,
        probe_radius=int(np.clip(round(10 * scale), 6, 20)),
    )
    requested_margin = int(np.clip(max(base_margin, outline_width), 1, maximum_margin))

    full_raw_evidence = (
        np.where(glyph_evidence > 0, 255, 0).astype(np.uint8)
        if glyph_evidence is not None and glyph_evidence.shape == full_binary_mask.shape
        else np.zeros_like(full_binary_mask)
    )
    raw_evidence_area = cv2.countNonZero(full_raw_evidence)
    raw_evidence = full_raw_evidence[roi]
    outside_before = cv2.countNonZero(
        cv2.bitwise_and(full_binary_mask, cv2.bitwise_not(full_binary_balloon))
    )

    def restore(compact: np.ndarray) -> np.ndarray:
        restored = np.zeros_like(full_binary_mask)
        restored[roi] = compact
        return restored
    # Candidate pixels on or outside the first protected band are commonly the
    # balloon outline picked up by the rectangular OCR crop. They are not safe
    # evidence for reducing the margin. Only interior glyph seeds participate
    # in the retention test; the final mask still covers all candidate pixels
    # that lie inside the selected safe interior.
    base_interior = np.where(distance > base_margin, 255, 0).astype(np.uint8)
    evidence = cv2.bitwise_and(raw_evidence, base_interior)
    evidence_area = cv2.countNonZero(evidence)
    # OCR polygons often touch or cross a speech-balloon outline. Dark pixels
    # in that outer strip are much more likely to be the outline than glyphs,
    # so they must not turn an otherwise safe automatic repair into a manual
    # review. Use the same 70% polygon core as balloon assignment and compare
    # those candidates against the exact instance (not the eroded safe area).
    component_evidence: list[tuple[np.ndarray, int]] = []
    for polygon in shifted_polygons:
        polygon_mask = np.zeros_like(binary_balloon)
        points = np.rint(np.asarray(polygon, dtype=np.float32)).astype(np.int32)
        cv2.fillPoly(polygon_mask, [points], 255)
        raw_component = cv2.bitwise_and(raw_evidence, polygon_mask)
        if cv2.countNonZero(raw_component) == 0:
            continue
        component = cv2.bitwise_and(raw_component, base_interior)
        component_evidence.append((component, cv2.countNonZero(component)))
    chosen_margin: int | None = None
    glyph_retention = 0.0
    component_retentions: list[float] = []
    for margin in range(requested_margin, base_margin - 1, -1):
        safe = np.where(distance > margin, 255, 0).astype(np.uint8)
        if component_evidence:
            retentions = [
                cv2.countNonZero(cv2.bitwise_and(component, safe)) / area if area else 0.0
                for component, area in component_evidence
            ]
            if min(retentions) < 0.9:
                continue
            retention = min(retentions)
        elif evidence_area:
            retention = cv2.countNonZero(cv2.bitwise_and(evidence, safe)) / evidence_area
            if retention < 0.9:
                continue
        else:
            retention = cv2.countNonZero(cv2.bitwise_and(binary_mask, safe)) / raw_area
            if retention < 0.8:
                continue
        chosen_margin = margin
        glyph_retention = float(retention)
        component_retentions = retentions if component_evidence else []
        break

    if chosen_margin is None:
        # The segmentation and the text evidence disagree. Never reintroduce
        # the unsafe full rectangle: keep only detected glyph evidence that is
        # actually inside the instance, and surface the disagreement for QA.
        conservative = cv2.bitwise_and(
            evidence if evidence_area else binary_mask,
            base_interior,
        )
        return restore(conservative), {
            "version": 1,
            "status": "fallback",
            "reason": "glyph_retention_too_low",
            "source": "detection_artifact",
            "raw_mask_area": raw_area,
            "final_mask_area": cv2.countNonZero(conservative),
            "glyph_area": raw_evidence_area,
            "interior_glyph_area": evidence_area,
            "glyph_safe_retention": round(
                cv2.countNonZero(cv2.bitwise_and(evidence, base_interior)) / evidence_area,
                4,
            )
            if evidence_area
            else 0.0,
            "outside_pixels_before": outside_before,
            "outside_pixels_after": 0,
        }

    safe_interior = np.where(distance > chosen_margin, 255, 0).astype(np.uint8)
    constrained = cv2.bitwise_and(binary_mask, safe_interior)
    final_area = cv2.countNonZero(constrained)
    if final_area == 0:
        conservative = cv2.bitwise_and(
            evidence if evidence_area else binary_mask,
            base_interior,
        )
        return restore(conservative), {
            "version": 1,
            "status": "fallback",
            "reason": "constraint_removed_entire_mask",
            "raw_mask_area": raw_area,
            "final_mask_area": cv2.countNonZero(conservative),
            "glyph_area": raw_evidence_area,
            "interior_glyph_area": evidence_area,
            "outside_pixels_after": 0,
        }

    outside_after = cv2.countNonZero(cv2.bitwise_and(constrained, cv2.bitwise_not(safe_interior)))
    margin_status = "applied" if chosen_margin == requested_margin else "relaxed"
    full_glyph_core_mask = np.zeros_like(full_binary_balloon)
    for points in source_polygon_points:
        center = np.mean(points, axis=0)
        core_points = np.rint(center + (points - center) * 0.7).astype(np.int32)
        cv2.fillPoly(full_glyph_core_mask, [core_points], 255)
    core_glyph_evidence = (
        cv2.bitwise_and(full_raw_evidence, full_glyph_core_mask)
        if source_polygon_points
        else full_raw_evidence
    )
    core_glyph_area = cv2.countNonZero(core_glyph_evidence)
    glyph_balloon_retention = (
        cv2.countNonZero(cv2.bitwise_and(core_glyph_evidence, full_binary_balloon)) / core_glyph_area
        if core_glyph_area
        else 1.0
    )
    partial_glyph_coverage = bool(core_glyph_area and glyph_balloon_retention < 0.85)
    metadata: dict[str, Any] = {
        "version": 1,
        "status": "partial" if partial_glyph_coverage else margin_status,
        "margin_status": margin_status,
        "source": "detection_artifact",
        "base_margin_px": base_margin,
        "outline_width_px": outline_width,
        "outline_width_reliable": outline_reliable,
        "requested_margin_px": requested_margin,
        "safe_margin_px": chosen_margin,
        "raw_mask_area": raw_area,
        "final_mask_area": final_area,
        "raw_mask_retention": round(final_area / raw_area, 4),
        "glyph_area": raw_evidence_area,
        "glyph_core_area": core_glyph_area,
        "interior_glyph_area": evidence_area,
        "glyph_balloon_retention": round(glyph_balloon_retention, 4),
        "glyph_safe_retention": round(glyph_retention, 4),
        "component_glyph_retentions": [round(value, 4) for value in component_retentions],
        "outside_pixels_before": outside_before,
        "outside_pixels_after": outside_after,
    }
    if partial_glyph_coverage:
        metadata["reason"] = "core_glyph_evidence_outside_balloon"
    if context:
        metadata.update(
            {
                key: value
                for key, value in context.items()
                if key
                in {
                    "bubble_id",
                    "bubble_confidence",
                    "assignment_coverage",
                    "core_coverage",
                }
            }
        )
    return restore(constrained), metadata


def _estimate_balloon_outline_width(
    distance: np.ndarray,
    balloon_mask: np.ndarray,
    source_lab: np.ndarray | None,
    source_polygons: list[list[list[float]]],
    *,
    base_margin: int,
    probe_radius: int,
) -> tuple[int, bool]:
    """Estimate outline thickness from successive inward color bands."""

    if source_lab is None or source_lab.shape[:2] != balloon_mask.shape:
        return base_margin, False
    exclusion = np.zeros_like(balloon_mask)
    for polygon in source_polygons:
        try:
            points = np.rint(np.asarray(polygon, dtype=np.float32)).astype(np.int32)
        except (TypeError, ValueError):
            continue
        if len(points) >= 3:
            cv2.fillPoly(exclusion, [points], 255)

    peak = float(distance.max())
    deep_threshold = min(float(probe_radius), max(float(base_margin + 2), peak * 0.35))
    deep = (distance >= deep_threshold) & (exclusion == 0)
    if np.count_nonzero(deep) < 32:
        deep = (distance > base_margin) & (exclusion == 0)
    if np.count_nonzero(deep) < 16:
        return base_margin, False
    centers, _ = _dominant_colors(source_lab[deep])
    background = centers[0]
    residual = np.linalg.norm(source_lab - background[None, None, :], axis=2)
    deep_residual = residual[deep]
    color_limit = max(14.0, float(np.percentile(deep_residual, 90)) + 4.0)

    ratios: list[float] = []
    for ring in range(1, probe_radius + 1):
        pixels = (distance > ring - 1) & (distance <= ring) & (exclusion == 0)
        count = int(np.count_nonzero(pixels))
        ratios.append(float(np.count_nonzero(residual[pixels] > color_limit) / count) if count else 0.0)
    if len(ratios) < 2:
        return base_margin, False
    low_ratio = max(0.08, ratios[0] * 0.18)
    for index in range(len(ratios) - 1):
        if ratios[index] <= low_ratio and ratios[index + 1] <= low_ratio:
            return max(base_margin, index + 1), True
    return base_margin, False


def _dominant_colors(samples: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    compact = samples.reshape(-1, 3).astype(np.float32)
    cluster_count = max(1, min(4, len(compact) // 80))
    if cluster_count == 1:
        return np.median(compact, axis=0, keepdims=True), np.asarray([len(compact)], dtype=np.int32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.2)
    _, labels, centers = cv2.kmeans(compact, cluster_count, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=cluster_count)
    order = np.argsort(counts)[::-1]
    counts = counts[order]
    centers = centers[order]
    minimum_count = max(4, round(len(compact) * 0.08))
    keep = counts >= minimum_count
    if not np.any(keep):
        keep[0] = True
    return centers[keep], counts[keep]


def _filter_text_components(mask: np.ndarray, region_area: int) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    output = np.zeros_like(mask)
    maximum_area = max(64, round(region_area * 0.42))
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if 1 <= area <= maximum_area:
            output[labels == label] = 255
    return output


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    flooded = padded.copy()
    cv2.floodFill(flooded, None, (0, 0), 255)
    holes = cv2.bitwise_not(flooded)[1:-1, 1:-1]
    return cv2.bitwise_or(mask, holes)


def process_mask(source: Path, destination: Path, operation: str, amount: int = 3) -> Path:
    mask = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise ValueError(f"Cannot read mask: {source}")
    amount = max(1, int(amount))
    if operation in {"dilate", "expand"}:
        size = amount * 2 + 1 if operation == "expand" else amount
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        result = cv2.dilate(mask, kernel, iterations=1)
    elif operation == "erode":
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (amount, amount))
        result = cv2.erode(mask, kernel, iterations=1)
    elif operation == "blur":
        size = amount if amount % 2 == 1 else amount + 1
        result = cv2.GaussianBlur(mask, (size, size), 0)
    elif operation == "clear":
        result = np.zeros_like(mask)
    else:
        raise ValueError(f"Unsupported mask operation: {operation}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), result):
        raise ValueError(f"Cannot write mask: {destination}")
    return destination


def mask_is_empty(path: Path | None) -> bool:
    if path is None or not path.exists():
        return True
    mask = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    return mask is None or cv2.countNonZero(mask) == 0
