from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from app.services.base import DetectionResult
from app.utils.geometry import bbox_to_polygon


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
