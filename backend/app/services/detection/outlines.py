from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np
from app.services.base import DetectionResult
from app.services.detection.bubbles import BubbleInstance
from app.utils.geometry import bbox_to_polygon

MIN_OUTLINE_VERIFIED_CONFIDENCE = 0.6


def refine_bubble_outlines(
    bubbles: list[BubbleInstance], regions: list[DetectionResult], image: np.ndarray, *, split: bool = True
) -> list[BubbleInstance]:
    """Verify weak proposals and separate visible interiors before assignment.

    Model masks are only search windows. No overlap-only text grouping or global
    confidence reduction is used. A divider must disconnect substantial areas,
    have ink outside the text cores, and have text on both sides. Uncertain text
    crossing the divider is subsequently rejected by normal containment scoring.
    """
    output = []
    for bubble in bubbles:
        if not np.isfinite(bubble.confidence) or bubble.confidence < MIN_OUTLINE_VERIFIED_CONFIDENCE:
            output.append(bubble)
            continue
        x, y = bubble.mask_origin
        h, w = bubble.mask.shape
        gray = image[y:y + h, x:x + w]
        if gray.shape != bubble.mask.shape or not bubble.mask.any():
            output.append(bubble)
            continue
        mask = (bubble.mask > 0).astype(np.uint8)
        cores = _text_cores(regions, bubble)
        tolerance = max(2, min(12, round(min(mask.shape) * .1)))
        left, top = max(0, x - tolerance), max(0, y - tolerance)
        right, bottom = min(image.shape[1], x + w + tolerance), min(image.shape[0], y + h + tolerance)
        padding = ((y - top, bottom - y - h), (x - left, right - x - w))
        evidence = _outline_evidence(image[top:bottom, left:right], np.pad(mask, padding),
                                     [np.pad(core, padding) for core in cores], tolerance=tolerance)
        verified = replace(bubble, outline_evidence=evidence)
        children = _visible_interiors(gray, mask, cores) if split else None
        if children is None:
            output.append(verified)
            continue
        for index, child in enumerate(children):
            cx, cy, cw, ch = cv2.boundingRect(child)
            output.append(replace(
                verified,
                instance_id=f"{bubble.instance_id}:outline-{index}",
                bbox=[float(x + cx), float(y + cy), float(cw), float(ch)],
                polygon=bbox_to_polygon([x + cx, y + cy, cw, ch]),
                mask=child[cy:cy + ch, cx:cx + cw].copy(),
                mask_origin=(x + cx, y + cy),
                split_details={"method": "visible_interiors", "parent_instance_id": bubble.instance_id,
                               "child_index": index, "child_count": len(children)},
            ))
    return output


def _text_cores(regions: list[DetectionResult], bubble: BubbleInstance) -> list[np.ndarray]:
    cores = []
    origin = np.asarray(bubble.mask_origin)
    for region in regions:
        rx, ry, rw, rh = region.bbox
        x, y = bubble.mask_origin
        h, w = bubble.mask.shape
        if rx + rw <= x or ry + rh <= y or rx >= x + w or ry >= y + h:
            continue
        try:
            points = np.asarray(region.polygon, dtype=np.float64)
        except (ValueError, TypeError):
            continue
        if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or not np.isfinite(points).all():
            continue
        if bubble.intersection_ratio(region.polygon) < .4:
            continue
        center = points.mean(axis=0)
        points = np.rint(center + (points - center) * .7 - origin).astype(np.int32)
        core = np.zeros_like(bubble.mask, dtype=np.uint8)
        cv2.fillPoly(core, [points], 1)
        if core.any():
            cores.append(core)
    return cores


def _outline_evidence(
    gray: np.ndarray, mask: np.ndarray, cores: list[np.ndarray], *, tolerance: int
) -> dict | None:
    """Require actual ink along multiple arcs, allowing intentional outline gaps."""
    edge = mask - cv2.erode(np.pad(mask, 1), np.ones((3, 3), np.uint8))[1:-1, 1:-1]
    for core in cores:
        edge[core > 0] = 0
    # The caller supplies real neighboring pixels beyond the compact mask.
    dark = (gray < 100).astype(np.uint8)
    if (not dark.any() or np.count_nonzero(edge) < 32
            or np.mean(dark[mask > 0]) > .3):
        return None
    for core in cores:
        dark[core > 0] = 0
    distance = cv2.distanceTransform(1 - dark, cv2.DIST_L2, 5)
    supported = (distance <= tolerance) & (edge > 0)
    support = np.count_nonzero(supported) / np.count_nonzero(edge)
    ys, xs = np.nonzero(edge)
    cy, cx = np.mean(ys), np.mean(xs)
    sectors = [(ys < cy) & (xs < cx), (ys < cy) & (xs >= cx),
               (ys >= cy) & (xs < cx), (ys >= cy) & (xs >= cx)]
    hits = supported[ys, xs]
    supported_sectors = sum(int(np.count_nonzero(s) >= 8 and np.mean(hits[s]) >= .35) for s in sectors)
    if support < .75 or supported_sectors < 3:
        return None
    return {"method": "visible_outline", "support": round(support, 4),
            "supported_sectors": supported_sectors, "tolerance": tolerance}


def _visible_interiors(gray: np.ndarray, mask: np.ndarray, cores: list[np.ndarray]) -> list[np.ndarray] | None:
    if len(cores) < 2:
        return None
    radius = max(1, min(3, round(min(mask.shape) / 120)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    walls = cv2.morphologyEx((gray < 100).astype(np.uint8), cv2.MORPH_CLOSE, kernel)
    barrier = cv2.dilate(walls, np.ones((3, 3), np.uint8))
    distance = cv2.distanceTransform(np.pad(mask, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    free = ((mask > 0) & (barrier == 0) & (distance > 1)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=8)
    minimum_area = max(32, np.count_nonzero(mask) * .12)
    significant = [label for label in range(1, n) if stats[label, cv2.CC_STAT_AREA] >= minimum_area]
    if len(significant) < 2:
        return None
    # Small glyph cavities and unoccupied lobes cannot create new balloons.
    occupied = set()
    for core in cores:
        counts = np.bincount(labels[core > 0], minlength=n)
        area = np.count_nonzero(core)
        best = max(significant, key=lambda label: counts[label])
        if counts[best] >= area * .55 and sum(counts[k] for k in significant if k != best) <= area * .15:
            occupied.add(best)
    if len(occupied) < 2:
        return None
    seeds = [(labels == label).astype(np.uint8) for label in sorted(occupied)]
    distances = np.stack([cv2.distanceTransform(1 - seed, cv2.DIST_L2, 5) for seed in seeds])
    owners = np.argmin(distances, axis=0)
    # Require a substantial real divider outside lettering. Text that touches
    # the outline can disconnect whitespace too; that is not a balloon wall.
    text = np.zeros_like(mask)
    for core in cores:
        text |= core
    separator = np.zeros_like(mask)
    for index in range(len(seeds)):
        child = ((owners == index) & (mask > 0)).astype(np.uint8)
        edge = cv2.dilate(child, np.ones((5, 5), np.uint8)) & (mask > 0) & (owners != index)
        separator |= edge.astype(np.uint8)
    divider = (separator > 0) & (walls > 0) & (distance > 2)
    total = np.count_nonzero(divider)
    visible = np.count_nonzero(divider & (text == 0))
    if total < max(16, min(mask.shape) * .2) or visible < total * .4:
        return None
    return [((owners == index) & (mask > 0)).astype(np.uint8) for index in range(len(seeds))]
