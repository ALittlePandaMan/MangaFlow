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
    mask_centers = inside_centers[:1] if enclosed_background else centers

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
    if cv2.countNonZero(candidate) == 0:
        # Low-contrast edge case: retaining the old polygon behavior is safer than an empty mask.
        candidate = crop_region.copy()

    candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
    stylized_complex = (
        not uniform_background
        and not enclosed_background
        and box_width * box_height >= 750
        and min(box_width, box_height) >= 12
    )
    method = "local_color_distance"
    if uniform_background and not enclosed_background:
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

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), mask):
        raise ValueError(f"Cannot write mask: {output_path}")

    return {
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


def create_text_mask_union(
    image_path: Path,
    polygons: list[list[list[float]]],
    output_path: Path,
    *,
    expand: int = 2,
    color_threshold: float = 18.0,
    source_image: np.ndarray | None = None,
    source_lab: np.ndarray | None = None,
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
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined: np.ndarray | None = None
    component_metadata: list[dict[str, Any]] = []
    temporary_paths: list[Path] = []
    try:
        for index, polygon in enumerate(polygons):
            temporary = output_path.with_name(f".{output_path.stem}.source-{index}{output_path.suffix}")
            temporary_paths.append(temporary)
            component_metadata.append(
                create_text_mask(
                    image_path,
                    polygon,
                    temporary,
                    expand=expand,
                    color_threshold=color_threshold,
                    source_image=source_image,
                    source_lab=source_lab,
                )
            )
            component = cv2.imread(str(temporary), cv2.IMREAD_GRAYSCALE)
            if component is None:
                raise ValueError(f"Cannot read component mask: {temporary}")
            if combined is not None and component.shape != combined.shape:
                raise ValueError("Text mask components must have matching dimensions")
            combined = component if combined is None else cv2.bitwise_or(combined, component)
        if combined is None or not cv2.imwrite(str(output_path), combined):
            raise ValueError(f"Cannot write mask: {output_path}")
    finally:
        for temporary in temporary_paths:
            temporary.unlink(missing_ok=True)

    return {
        "method": "source_polygon_union",
        "source_count": len(polygons),
        "components": component_metadata,
        "suggested_region_type": "background_complex",
    }


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
