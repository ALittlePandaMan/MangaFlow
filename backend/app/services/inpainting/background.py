"""Local balloon colour evidence; deliberately independent of OCR text content."""

from __future__ import annotations

import cv2
import numpy as np


def dominant_background(samples: np.ndarray) -> tuple[np.ndarray, float]:
    """A bounded, deterministic colour mode, not an average of ink and paper."""
    samples = samples.reshape(-1, 3).astype(np.float32)
    if not len(samples):
        raise ValueError("Background estimation requires samples")
    samples = samples[:: max(1, len(samples) // 8192)]
    bins = np.clip(samples // 8, 0, 31).astype(np.int32)
    keys = bins[:, 0] * 1024 + bins[:, 1] * 32 + bins[:, 2]
    mode = int(np.argmax(np.bincount(keys, minlength=32768)))
    center = np.median(samples[keys == mode], axis=0)
    nearby = np.linalg.norm(samples - center, axis=1) <= 12
    return np.median(samples[nearby], axis=0), float(np.mean(nearby))


def balloon_background(
    image: np.ndarray,
    lab: np.ndarray,
    balloon: np.ndarray,
    exclusion: np.ndarray,
) -> dict | None:
    """Learn once per balloon, excluding every known text polygon and the wall.

    If text covers the whole balloon, interior pixels may still establish an
    ink/background contrast, but must never authorize flat-colour replacement.
    """
    points = cv2.findNonZero(balloon)
    if points is None:
        return None
    x, y, w, h = cv2.boundingRect(points)
    roi = np.s_[y : y + h, x : x + w]
    binary = (balloon[roi] > 0).astype(np.uint8)
    distance = cv2.distanceTransform(np.pad(binary, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    margin = max(3, min(12, round(min(w, h) * 0.06)))
    interior = distance > margin
    excluded = cv2.dilate(exclusion[roi], np.ones((5, 5), np.uint8)) > 0
    clean = interior & ~excluded
    count = int(np.count_nonzero(clean))
    enough_clean = count >= max(64, int(np.count_nonzero(interior) * 0.03))
    samples = lab[roi][clean if enough_clean else interior]
    if len(samples) < 32:
        return None
    center, support = dominant_background(samples)
    if support < 0.6:
        return None
    rgb_samples = image[roi][clean if enough_clean else interior][:, ::-1].astype(np.float32)
    near = np.linalg.norm(samples - center, axis=1) <= 12
    rgb = np.median(rgb_samples[near], axis=0)
    # Measure ALL clean samples against one colour. Multiple colour clusters
    # with individually small variance do not make a gradient/texture flat.
    spread = np.max(np.abs(rgb_samples - rgb), axis=1)
    p95, p99 = np.percentile(spread, [95, 99])
    uniform = enough_clean and p95 <= 4 and p99 <= 10
    return {
        "lab": center.tolist(),
        "color_rgb": np.rint(rgb).astype(int).tolist(),
        "support": round(support, 4),
        "sample_count": count,
        "uniform": bool(uniform),
        "spread_p95": round(float(p95), 3),
        "spread_p99": round(float(p99), 3),
    }


def outline_guard(lab: np.ndarray | None, distance: np.ndarray, base_margin: int) -> np.ndarray:
    """Protect long real ink strokes near a possibly imprecise model contour.

    This is a safety veto, not a new balloon segmentation: it can only remove
    repair pixels. Detached short glyph strokes are not treated as the wall.
    """
    guard = np.zeros(distance.shape, np.uint8)
    if lab is None:
        return guard
    deep = distance > max(base_margin + 2, min(12, float(distance.max()) * 0.25))
    if np.count_nonzero(deep) < 32:
        return guard
    center, support = dominant_background(lab[deep])
    if support < 0.6:
        return guard
    ink = (np.linalg.norm(lab - center, axis=2) > 24).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    height, width = distance.shape
    band = max(8, min(24, round(min(width, height) * 0.12)))
    near_wall = distance <= band
    near_counts = np.bincount(labels[near_wall], minlength=count)
    keep = np.zeros(count, dtype=np.uint8)
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        long_stroke = (
            stats[label, cv2.CC_STAT_WIDTH] >= width * 0.55 or stats[label, cv2.CC_STAT_HEIGHT] >= height * 0.55
        )
        if long_stroke and area >= 24 and near_counts[label] / area >= 0.2:
            keep[label] = 255
    guard = keep[labels]
    # Include the antialiased fringe of the outline, never paint it back later.
    return cv2.dilate(guard, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))


def paper_background(image: np.ndarray, lab: np.ndarray, ring: np.ndarray, region: np.ndarray) -> dict | None:
    """Authorize flat light paper only from clean exterior AND interior evidence.

    This does not infer a balloon. The mask remains inside the original text
    polygon; textured, coloured or poorly sampled surroundings stay on LaMa.
    """
    samples = lab[ring > 0]
    inside = lab[region > 0]
    if len(samples) < 64 or len(inside) < 32:
        return None
    center, support = dominant_background(samples)
    inner_center, inner_support = dominant_background(inside)
    if support < 0.5 or inner_support < 0.6 or np.linalg.norm(center - inner_center) > 6:
        return None
    # Straight panel rules are not a paper texture. Remove only verified long
    # structures from the samples; arbitrary dark outliers must still reject
    # flat filling (e.g. screen-tone dots and cross-hatching).
    guard = paper_structure_guard(lab, region, {"lab": center.tolist()}, 8)
    # Border antialiasing and the narrow drop shadow of an overlapping panel
    # are not samples of this text's paper. This halo is ONLY for sampling;
    # the actual repair still uses the tight line guard and original polygon.
    sample_guard = cv2.dilate(guard, np.ones((9, 9), np.uint8))
    clean = (ring > 0) & (sample_guard == 0)
    if np.count_nonzero(clean) < max(64, len(samples) * 0.2):
        return None
    samples = lab[clean]
    center, support = dominant_background(samples)
    if support < 0.95 or np.linalg.norm(center - inner_center) > 6:
        return None
    rgb_samples = image[clean][:, ::-1].astype(np.float32)
    color = np.median(rgb_samples, axis=0)
    spread = np.max(np.abs(rgb_samples - color), axis=1)
    p95, p99 = np.percentile(spread, [95, 99])
    if np.min(color) < 235 or np.ptp(color) > 6 or p95 > 6 or p99 > 16:
        return None
    return {
        "uniform": True,
        "scope": "local_paper",
        "lab": center.tolist(),
        "color_rgb": np.rint(color).astype(int).tolist(),
        "sample_count": len(samples),
        "support": round(support, 4),
        "spread_p95": round(float(p95), 3),
        "spread_p99": round(float(p99), 3),
        "structure_excluded_samples": int(np.count_nonzero((ring > 0) & (sample_guard > 0))),
    }


def paper_structure_guard(lab: np.ndarray, region: np.ndarray, background: dict, radius: int) -> np.ndarray:
    """Veto long lines continuing outside an OCR crop, such as panel borders."""
    x, y, w, h = cv2.boundingRect(region)
    pad = max(8, radius * 2)
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(region.shape[1], x + w + pad), min(region.shape[0], y + h + pad)
    roi = np.s_[y0:y1, x0:x1]
    ink = (np.linalg.norm(lab[roi] - np.asarray(background["lab"]), axis=2) > 24).astype(np.uint8)
    selected = np.zeros(ink.shape, np.uint8)
    # A rule can connect to a perpendicular border (or touch a glyph), making
    # its whole connected component non-slender. Detect the straight stroke
    # itself as well, without protecting the attached letters with it.
    for horizontal, length in ((True, max(20, round(w * 0.75))), (False, max(20, round(h * 0.8)))):
        kernel = np.ones((1, length) if horizontal else (length, 1), np.uint8)
        rules = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
        n, rule_labels, rule_stats, _ = cv2.connectedComponentsWithStats(rules, connectivity=8)
        exterior = np.bincount(rule_labels[region[roi] == 0], minlength=n)
        accepted = np.zeros(n, np.uint8)
        for label in range(1, n):
            left, top, width, height, area = rule_stats[label]
            touches_edge = (
                (left == 0 or left + width == x1 - x0) if horizontal else (top == 0 or top + height == y1 - y0)
            )
            if touches_edge and exterior[label] >= max(4, area * 0.03):
                accepted[label] = 255
        selected |= accepted[rule_labels]
    guard = np.zeros(region.shape, np.uint8)
    guard[roi] = cv2.dilate(selected, np.ones((3, 3), np.uint8))
    return guard
