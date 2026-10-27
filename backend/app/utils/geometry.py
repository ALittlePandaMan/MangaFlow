from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from math import atan2, hypot, isfinite

import numpy as np


def validate_polygon(points):
    """Validate an ordered simple polygon, including valid concave outlines."""
    if not isinstance(points, list) or len(points) < 3 or len(points) > 2000 or any(
        not isinstance(p, (list, tuple)) or len(p) != 2 or not all(isfinite(v) for v in p) for p in points
    ):
        raise ValueError("Polygon needs 3–2000 finite [x,y] vertices")
    if len({tuple(p) for p in points}) != len(points):
        raise ValueError("Polygon vertices must be distinct (do not repeat the closing vertex)")

    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])

    edges = list(zip(points, points[1:] + points[:1], strict=True))
    if abs(sum(a[0]*b[1]-b[0]*a[1] for a, b in edges))/2 < 1:
        raise ValueError("Polygon is degenerate")
    for i, (a, b) in enumerate(edges):
        for j in range(i+1, len(edges)):
            if j == i+1 or (i == 0 and j == len(edges)-1):
                continue
            c, d = edges[j]
            if (max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0]))
                    and max(min(a[1], b[1]), min(c[1], d[1])) <= min(max(a[1], b[1]), max(c[1], d[1]))
                    and cross(a, b, c)*cross(a, b, d) <= 0 and cross(c, d, a)*cross(c, d, b) <= 0):
                raise ValueError("Polygon intersects itself")
    return points


def validate_region_frame(bbox, polygon, *, perspective=False):
    if (not isinstance(bbox, list) or len(bbox) != 4
            or not all(isfinite(v) for v in bbox) or min(bbox[2:]) <= 0):
        raise ValueError("Bounding box needs finite [x,y,width,height] with positive width/height")
    validate_polygon(polygon)
    if perspective and order_quadrilateral(polygon) is None:
        raise ValueError("Perspective requires a convex quadrilateral; set perspective_warp=false for other polygons")


def normalize_region_frames(values):
    """Canonical creation/import geometry; never accept an empty source frame.

    A missing counterpart can be derived, but explicit edits use the stricter
    frame validator so clearing a stored frame cannot silently recreate it.
    """
    bbox, polygon = deepcopy(values.get("bbox") or []), deepcopy(values.get("polygon") or [])
    if not bbox and polygon:
        bbox = polygon_to_bbox(polygon)
    if not polygon and bbox:
        polygon = bbox_to_polygon(bbox)
    validate_region_frame(bbox, polygon)
    translated_bbox = deepcopy(values.get("translated_bbox") or [])
    translated_polygon = deepcopy(values.get("translated_polygon") or [])
    if not translated_bbox:
        translated_bbox = polygon_to_bbox(translated_polygon) if translated_polygon else deepcopy(bbox)
    if not translated_polygon:
        translated_polygon = bbox_to_polygon(translated_bbox) if values.get("translated_bbox") else deepcopy(polygon)
    validate_region_frame(translated_bbox, translated_polygon, perspective=values.get("perspective_warp", False))
    return {"bbox": bbox, "polygon": polygon,
            "translated_bbox": translated_bbox, "translated_polygon": translated_polygon}


def polygon_to_bbox(points: Iterable[Iterable[float]]) -> list[float]:
    normalized = [(float(point[0]), float(point[1])) for point in points]
    if not normalized:
        return []
    xs = [point[0] for point in normalized]
    ys = [point[1] for point in normalized]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


def bbox_to_polygon(bbox: Iterable[float]) -> list[list[float]]:
    x, y, width, height = (float(value) for value in bbox)
    return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]


def order_quadrilateral(points: Iterable[Iterable[float]]) -> list[list[float]] | None:
    """Validate a convex quad and normalize its winding for perspective mapping.

    OCR and manually edited polygons may start at any corner, even when their
    winding is already valid. Canonicalize a render-only copy to TL/TR/BR/BL
    so the text's reading direction never depends on the stored first vertex.
    """

    normalized: list[tuple[float, float]] = []
    for point in points:
        values = list(point)
        if len(values) != 2:
            return None
        x, y = float(values[0]), float(values[1])
        if not isfinite(x) or not isfinite(y):
            return None
        normalized.append((x, y))
    if len(normalized) != 4 or len(set(normalized)) != 4:
        return None

    def cross_products(sequence: list[tuple[float, float]]) -> list[float]:
        output = []
        for index in range(4):
            previous = sequence[index - 1]
            current = sequence[index]
            following = sequence[(index + 1) % 4]
            output.append(
                (current[0] - previous[0]) * (following[1] - current[1])
                - (current[1] - previous[1]) * (following[0] - current[0])
            )
        return output

    center_x = sum(point[0] for point in normalized) / 4
    center_y = sum(point[1] for point in normalized) / 4
    ordered = sorted(normalized, key=lambda point: atan2(point[1] - center_y, point[0] - center_x))
    start = min(range(4), key=lambda index: (sum(ordered[index]), ordered[index][1], ordered[index][0]))
    ordered = ordered[start:] + ordered[:start]

    cross_values = cross_products(ordered)
    if min(cross_values) <= 1e-6:
        # Concave, collinear and self-intersecting quads do not define a
        # stable perspective transform. Callers deliberately fall back to the
        # existing rectangular renderer for these inputs.
        return None
    edge_lengths = [
        hypot(
            ordered[(index + 1) % 4][0] - ordered[index][0],
            ordered[(index + 1) % 4][1] - ordered[index][1],
        )
        for index in range(4)
    ]
    if min(edge_lengths) < 2.0:
        return None
    area = abs(
        sum(
            ordered[index][0] * ordered[(index + 1) % 4][1]
            - ordered[(index + 1) % 4][0] * ordered[index][1]
            for index in range(4)
        )
    ) / 2
    bbox_area = (max(point[0] for point in ordered) - min(point[0] for point in ordered)) * (
        max(point[1] for point in ordered) - min(point[1] for point in ordered)
    )
    normalized_crosses = [
        cross_values[index] / (edge_lengths[index - 1] * edge_lengths[index]) for index in range(4)
    ]
    if area < 4.0 or bbox_area <= 0 or area / bbox_area < 0.01 or min(normalized_crosses) < 1e-3:
        return None
    return [[point[0], point[1]] for point in ordered]


def perspective_coefficients(
    destination: Iterable[Iterable[float]],
    source: Iterable[Iterable[float]],
) -> tuple[float, ...]:
    """Return Pillow inverse perspective coefficients for destination -> source."""

    destination_points = [tuple(float(value) for value in point) for point in destination]
    source_points = [tuple(float(value) for value in point) for point in source]
    if len(destination_points) != 4 or len(source_points) != 4:
        raise ValueError("A perspective transform requires four source and destination points")
    matrix: list[list[float]] = []
    targets: list[float] = []
    for (x, y), (u, v) in zip(destination_points, source_points, strict=True):
        if not all(isfinite(value) for value in (x, y, u, v)):
            raise ValueError("Perspective points must be finite")
        matrix.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
        matrix.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y])
        targets.extend((u, v))
    try:
        coefficients = np.linalg.solve(np.asarray(matrix, dtype=np.float64), np.asarray(targets, dtype=np.float64))
    except np.linalg.LinAlgError as exc:
        raise ValueError("Perspective quadrilateral is degenerate") from exc
    return tuple(float(value) for value in coefficients)


def intersection_area(first: Iterable[float], second: Iterable[float]) -> float:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    width = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    height = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    return width * height


def reading_order_japanese(regions: list[dict[str, object]], column_tolerance: float = 0.45) -> list[int]:
    """Return indices in Japanese manga order: right-to-left columns, top-to-bottom.

    Regions with heavily overlapping horizontal ranges are treated as a column. This
    deliberately uses geometry only, making it deterministic and independently testable.
    """
    if not regions:
        return []
    items: list[dict[str, float | int]] = []
    for index, region in enumerate(regions):
        bbox = list(region.get("bbox", []))  # type: ignore[arg-type]
        if len(bbox) != 4:
            continue
        x, y, width, height = (float(value) for value in bbox)
        items.append({"index": index, "x": x, "y": y, "w": width, "h": height, "cx": x + width / 2})
    items.sort(key=lambda item: float(item["cx"]), reverse=True)
    columns: list[list[dict[str, float | int]]] = []
    for item in items:
        best_column: list[dict[str, float | int]] | None = None
        best_distance = float("inf")
        for column in columns:
            center = sum(float(existing["cx"]) for existing in column) / len(column)
            typical_width = max(float(existing["w"]) for existing in column)
            distance = abs(float(item["cx"]) - center)
            if distance <= max(typical_width, float(item["w"])) * column_tolerance and distance < best_distance:
                best_column, best_distance = column, distance
        if best_column is None:
            columns.append([item])
        else:
            best_column.append(item)
    columns.sort(key=lambda column: sum(float(item["cx"]) for item in column) / len(column), reverse=True)
    ordered: list[int] = []
    for column in columns:
        column.sort(key=lambda item: (float(item["y"]), -float(item["cx"])))
        ordered.extend(int(item["index"]) for item in column)
    return ordered
