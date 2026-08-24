from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
from app.services.base import DetectionResult
from app.services.detection.bubbles import BubbleInstance
from app.utils.geometry import bbox_to_polygon


def group_text_regions_by_bubbles(
    regions: list[DetectionResult],
    bubbles: list[BubbleInstance],
    *,
    page_key: str = "",
    min_bubble_confidence: float = 0.35,
    min_containment: float = 0.55,
    min_core_containment: float = 0.8,
    ambiguity_margin: float = 0.12,
    max_second_containment: float = 0.45,
    mask_padding: int = 3,
) -> list[DetectionResult]:
    """Assign text polygons to balloon instances and merge only equal IDs.

    A missing or uncertain balloon assignment is intentionally never used as a
    grouping key. This keeps free-standing sound effects and overlapping text
    boxes outside balloons independent.
    """
    assigned = assign_text_to_bubbles(
        regions,
        bubbles,
        page_key=page_key,
        min_bubble_confidence=min_bubble_confidence,
        min_containment=min_containment,
        min_core_containment=min_core_containment,
        ambiguity_margin=ambiguity_margin,
        max_second_containment=max_second_containment,
        mask_padding=mask_padding,
    )
    grouped: dict[str, list[tuple[int, DetectionResult]]] = {}
    order: list[str] = []
    for index, region in enumerate(assigned):
        key = region.bubble_id or f"outside:{index}"
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append((index, region))
    return [_merge_balloon_group(grouped[key]) for key in order]


def assign_text_to_bubbles(
    regions: list[DetectionResult],
    bubbles: list[BubbleInstance],
    *,
    page_key: str = "",
    min_bubble_confidence: float = 0.35,
    min_containment: float = 0.55,
    min_core_containment: float = 0.8,
    ambiguity_margin: float = 0.12,
    max_second_containment: float = 0.45,
    mask_padding: int = 3,
) -> list[DetectionResult]:
    bubble_ids = {bubble.instance_id: _stable_bubble_id(page_key, bubble) for bubble in bubbles}
    output: list[DetectionResult] = []
    for region in regions:
        if not _valid_polygon(region.polygon):
            output.append(_with_balloon_assignment(region, "outside", None, reason="invalid_polygon"))
            continue
        centroid = _polygon_centroid(region.polygon)
        core = _scale_polygon(region.polygon, centroid, 0.7)
        candidates: list[dict[str, float | str]] = []
        for bubble in bubbles:
            if bubble.confidence < min_bubble_confidence:
                continue
            coverage = bubble.intersection_ratio(region.polygon, padding=mask_padding)
            core_coverage = bubble.intersection_ratio(core)
            center_inside = bubble.contains_point(centroid)
            valid = (
                center_inside
                and coverage >= min_containment
                and core_coverage >= min_core_containment
            ) or (coverage >= 0.82 and core_coverage >= 0.9)
            if not valid:
                continue
            score = 0.55 * coverage + 0.35 * core_coverage + 0.1 * bubble.confidence
            candidates.append(
                {
                    "instance_id": bubble.instance_id,
                    "coverage": coverage,
                    "core_coverage": core_coverage,
                    "score": score,
                    "balloon_confidence": bubble.confidence,
                }
            )
        candidates.sort(key=lambda item: float(item["score"]), reverse=True)
        if not candidates:
            output.append(_with_balloon_assignment(region, "outside", None, reason="no_matching_balloon"))
            continue
        best = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        ambiguous = second is not None and (
            float(best["score"]) - float(second["score"]) < ambiguity_margin
            or float(second["coverage"]) >= max_second_containment
        )
        if ambiguous:
            output.append(
                _with_balloon_assignment(
                    region,
                    "ambiguous",
                    None,
                    candidate=best,
                    second_score=float(second["score"]),
                )
            )
            continue
        instance_id = str(best["instance_id"])
        output.append(
            _with_balloon_assignment(
                region,
                "assigned",
                bubble_ids[instance_id],
                candidate=best,
                second_score=float(second["score"]) if second else None,
            )
        )
    return output


def _valid_polygon(polygon: list[list[float]]) -> bool:
    try:
        points = np.asarray(polygon, dtype=np.float64)
    except (TypeError, ValueError):
        return False
    return bool(
        len(points) >= 3
        and points.shape == (len(points), 2)
        and np.all(np.isfinite(points))
        and abs(cv2.contourArea(points.astype(np.float32))) > 1e-6
    )


def _stable_bubble_id(page_key: str, bubble: BubbleInstance) -> str:
    """Build a stable, page-scoped ID without persisting model-local indexes."""

    quantized_bbox = [round(float(value) / 4) * 4 for value in bubble.bbox]
    payload = f"{page_key}|{','.join(str(value) for value in quantized_bbox)}"
    digest = hashlib.blake2s(payload.encode("utf-8"), digest_size=6).hexdigest()
    return f"bubble-{digest}"


def _polygon_centroid(polygon: list[list[float]]) -> tuple[float, float]:
    points = np.asarray(polygon, dtype=np.float64)
    shifted = np.roll(points, -1, axis=0)
    cross = points[:, 0] * shifted[:, 1] - shifted[:, 0] * points[:, 1]
    signed_area = float(np.sum(cross) / 2)
    if abs(signed_area) < 1e-8:
        return float(np.mean(points[:, 0])), float(np.mean(points[:, 1]))
    scale = 1 / (6 * signed_area)
    return (
        float(np.sum((points[:, 0] + shifted[:, 0]) * cross) * scale),
        float(np.sum((points[:, 1] + shifted[:, 1]) * cross) * scale),
    )


def _scale_polygon(
    polygon: list[list[float]],
    center: tuple[float, float],
    scale: float,
) -> list[list[float]]:
    center_array = np.asarray(center, dtype=np.float64)
    points = np.asarray(polygon, dtype=np.float64)
    return (center_array + (points - center_array) * scale).tolist()


def _with_balloon_assignment(
    region: DetectionResult,
    status: str,
    bubble_id: str | None,
    *,
    candidate: dict[str, float | str] | None = None,
    second_score: float | None = None,
    reason: str | None = None,
) -> DetectionResult:
    assignment: dict[str, object] = {"status": status, "bubble_id": bubble_id}
    if candidate is not None:
        assignment.update(
            {
                "coverage": round(float(candidate["coverage"]), 4),
                "core_coverage": round(float(candidate["core_coverage"]), 4),
                "score": round(float(candidate["score"]), 4),
                "balloon_confidence": round(float(candidate["balloon_confidence"]), 4),
            }
        )
    if second_score is not None:
        assignment["second_score"] = round(float(second_score), 4)
    if reason:
        assignment["reason"] = reason
    return replace(
        region,
        bubble_id=bubble_id,
        metadata={**region.metadata, "balloon_assignment": assignment},
    )


def _merge_balloon_group(group: list[tuple[int, DetectionResult]]) -> DetectionResult:
    if len(group) == 1:
        return group[0][1]

    indexed_members = sorted(group, key=lambda item: item[0])
    members = [region for _, region in indexed_members]
    bubble_ids = {region.bubble_id for region in members}
    if None in bubble_ids or len(bubble_ids) != 1:
        raise ValueError("Only text regions assigned to one bubble can be merged")
    bubble_id = next(iter(bubble_ids))

    left = min(region.bbox[0] for region in members)
    top = min(region.bbox[1] for region in members)
    right = max(region.bbox[0] + region.bbox[2] for region in members)
    bottom = max(region.bbox[1] + region.bbox[3] for region in members)
    bbox = [left, top, right - left, bottom - top]

    orientation_weights: dict[str, float] = {}
    for region in members:
        weight = max(1.0, region.bbox[2] * region.bbox[3]) * max(0.01, region.confidence)
        orientation_weights[region.orientation] = orientation_weights.get(region.orientation, 0.0) + weight
    orientation = max(orientation_weights, key=orientation_weights.get)

    if orientation == "vertical":
        reading_members = sorted(
            indexed_members,
            key=lambda item: (-(item[1].bbox[0] + item[1].bbox[2] / 2), item[1].bbox[1]),
        )
    else:
        reading_members = sorted(
            indexed_members,
            key=lambda item: (item[1].bbox[1] + item[1].bbox[3] / 2, item[1].bbox[0]),
        )
    source_index_by_original = {original_index: source_index for source_index, (original_index, _) in enumerate(indexed_members)}
    member_order = [source_index_by_original[original_index] for original_index, _ in reading_members]

    source_assignments = [
        region.metadata.get("balloon_assignment", {})
        if isinstance(region.metadata.get("balloon_assignment"), dict)
        else {}
        for region in members
    ]
    grouped_assignment: dict[str, object] = {"status": "assigned", "bubble_id": bubble_id}
    for key in ("coverage", "core_coverage", "score", "balloon_confidence"):
        values = [float(item[key]) for item in source_assignments if isinstance(item.get(key), (int, float))]
        if values:
            grouped_assignment[key] = round(min(values), 4)

    metadata = dict(members[0].metadata)
    metadata["balloon_assignment"] = grouped_assignment
    metadata["line_grouping"] = {
        "method": "balloon_instance",
        "source_count": len(members),
        "source_boxes": [list(region.bbox) for region in members],
        "source_polygons": [[list(point) for point in region.polygon] for region in members],
        "source_confidences": [float(region.confidence) for region in members],
        "member_order": member_order,
    }
    return DetectionResult(
        polygon=bbox_to_polygon(bbox),
        bbox=bbox,
        confidence=min(region.confidence for region in members),
        orientation=orientation,
        region_type=members[0].region_type,
        bubble_id=bubble_id,
        metadata=metadata,
    )


def group_text_lines(
    regions: list[DetectionResult],
    image_path: Path | None = None,
    *,
    max_background_residual: float = 16.0,
) -> list[DetectionResult]:
    """Merge co-oriented detector line boxes that form one visual text block."""
    if len(regions) < 2:
        return regions
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR) if image_path else None
    if image_path and image is None:
        raise ValueError(f"Cannot read grouping image: {image_path}")

    parents = list(range(len(regions)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            parents[second_root] = first_root

    for first in range(len(regions)):
        for second in range(first + 1, len(regions)):
            if _same_text_block(regions[first], regions[second]) and (
                image is None
                or _background_residual(image, regions[first].bbox, regions[second].bbox)
                <= max_background_residual
            ):
                union(first, second)

    groups: dict[int, list[tuple[int, DetectionResult]]] = {}
    for index, region in enumerate(regions):
        groups.setdefault(find(index), []).append((index, region))

    merged = [_merge_group(group) for group in groups.values()]
    merged.sort(key=lambda item: item[0])
    return [item[1] for item in merged]


def _same_text_block(first: DetectionResult, second: DetectionResult) -> bool:
    if first.orientation != second.orientation:
        return False
    ax, ay, aw, ah = first.bbox
    bx, by, bw, bh = second.bbox
    if min(aw, ah, bw, bh) <= 0:
        return False

    if first.orientation == "vertical":
        thickness_ratio = max(aw, bw) / min(aw, bw)
        length_ratio = max(ah, bh) / min(ah, bh)
        column_gap = _interval_gap(ax, ax + aw, bx, bx + bw)
        length_overlap = _interval_overlap(ay, ay + ah, by, by + bh) / min(ah, bh)
        center_distance = abs((ay + ah / 2) - (by + bh / 2))
        return (
            thickness_ratio <= 2.0
            and length_ratio <= 2.2
            and column_gap <= max(12.0, max(aw, bw) * 0.75)
            and length_overlap >= 0.25
            and center_distance <= max(ah, bh) * 0.7
        )

    thickness_ratio = max(ah, bh) / min(ah, bh)
    row_gap = _interval_gap(ay, ay + ah, by, by + bh)
    length_overlap = _interval_overlap(ax, ax + aw, bx, bx + bw) / min(aw, bw)
    center_distance = abs((ax + aw / 2) - (bx + bw / 2))
    return (
        thickness_ratio <= 2.2
        and row_gap <= max(12.0, max(ah, bh) * 0.75)
        and length_overlap >= 0.25
        and center_distance <= max(aw, bw) * 0.7
    )


def _merge_group(group: list[tuple[int, DetectionResult]]) -> tuple[int, DetectionResult]:
    first_index = min(index for index, _ in group)
    if len(group) == 1:
        return first_index, group[0][1]
    members = [region for _, region in group]
    left = min(region.bbox[0] for region in members)
    top = min(region.bbox[1] for region in members)
    right = max(region.bbox[0] + region.bbox[2] for region in members)
    bottom = max(region.bbox[1] + region.bbox[3] for region in members)
    bbox = [left, top, right - left, bottom - top]
    source_boxes = [list(region.bbox) for _, region in sorted(group)]
    return first_index, DetectionResult(
        polygon=bbox_to_polygon(bbox),
        bbox=bbox,
        confidence=min(region.confidence for region in members),
        orientation=members[0].orientation,
        region_type=members[0].region_type,
        metadata={
            "line_grouping": {
                "method": "co_oriented_geometry",
                "source_count": len(members),
                "source_boxes": source_boxes,
            }
        },
    )


def _interval_gap(first_start: float, first_end: float, second_start: float, second_end: float) -> float:
    return max(0.0, max(first_start, second_start) - min(first_end, second_end))


def _interval_overlap(first_start: float, first_end: float, second_start: float, second_end: float) -> float:
    return max(0.0, min(first_end, second_end) - max(first_start, second_start))


def _background_residual(image: np.ndarray, first: list[float], second: list[float]) -> float:
    height, width = image.shape[:2]
    left = max(0, int(np.floor(min(first[0], second[0]))))
    top = max(0, int(np.floor(min(first[1], second[1]))))
    right = min(width, int(np.ceil(max(first[0] + first[2], second[0] + second[2]))))
    bottom = min(height, int(np.ceil(max(first[1] + first[3], second[1] + second[3]))))
    crop = image[top:bottom, left:right]
    if crop.size == 0:
        return float("inf")
    samples = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    cluster_count = max(1, min(4, len(samples) // 80))
    if cluster_count == 1:
        distances = np.linalg.norm(samples - np.median(samples, axis=0), axis=1)
    else:
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.2)
        _, labels, centers = cv2.kmeans(samples, cluster_count, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
        distances = np.linalg.norm(samples - centers[labels.ravel()], axis=1)
    return float(np.percentile(distances, 80))
