from __future__ import annotations

from collections.abc import Iterable

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
