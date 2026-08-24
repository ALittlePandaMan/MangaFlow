from __future__ import annotations

from collections.abc import Iterable
from math import atan2, hypot, isfinite

import numpy as np

Point = tuple[float, float]
BBox = tuple[float, float, float, float]


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

    Valid editor polygons retain their semantic first corner, so moving a
    corner cannot suddenly rotate the text texture. Imported unordered points
    are canonicalized around their centroid with the top-left corner first.
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

    supplied_crosses = cross_products(normalized)
    if min(supplied_crosses) > 1e-6:
        # The editor already persists semantic TL/TR/BR/BL indices. Preserve
        # them so dragging a corner past a diagonal does not suddenly rotate
        # the text mapping by 90 degrees.
        ordered = normalized
    elif max(supplied_crosses) < -1e-6:
        ordered = [normalized[0], normalized[3], normalized[2], normalized[1]]
    else:
        # Imported unordered corners (including a bow-tie ordering) need one
        # canonicalization pass.
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


def clamp_bbox(bbox: Iterable[float], image_width: int, image_height: int) -> list[float]:
    x, y, width, height = (float(value) for value in bbox)
    left = min(max(0.0, x), float(image_width))
    top = min(max(0.0, y), float(image_height))
    right = min(max(left, x + width), float(image_width))
    bottom = min(max(top, y + height), float(image_height))
    return [left, top, right - left, bottom - top]


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
