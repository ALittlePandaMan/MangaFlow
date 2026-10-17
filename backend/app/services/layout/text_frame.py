"""Local layout coordinates for a slanted rectangular text frame.

Like Koharu's geometry_frame, undo the text angle before measuring the frame.
Only a rectangle aligned to those axes qualifies: an independently authored
angle or irregular polygon must retain the existing conservative fitting path.
"""
from math import cos, isfinite, radians, sin


def aligned_text_frame(polygon: list[list[float]], angle: float) -> list[float] | None:
    if not isfinite(angle) or abs(angle) <= .01 or len(polygon) != 4:
        return None
    if any(len(point) < 2 or not all(isfinite(v) for v in point[:2]) for point in polygon):
        return None
    cx = sum(point[0] for point in polygon) / 4
    cy = sum(point[1] for point in polygon) / 4
    cosine, sine = cos(radians(angle)), sin(radians(angle))
    points = [((x-cx)*cosine+(y-cy)*sine, -(x-cx)*sine+(y-cy)*cosine) for x, y, *_ in polygon]
    left, top = min(x for x, _ in points), min(y for _, y in points)
    right, bottom = max(x for x, _ in points), max(y for _, y in points)
    width, height = right-left, bottom-top
    if min(width, height) < 2:
        return None
    tolerance = max(width, height) * 1e-6
    corners = set()
    for x, y in points:
        if min(abs(x-left), abs(x-right)) > tolerance or min(abs(y-top), abs(y-bottom)) > tolerance:
            return None
        corners.add((abs(x-right) < abs(x-left), abs(y-bottom) < abs(y-top)))
    if len(corners) != 4:
        return None
    # Stabilize integer font-search boundaries after floating-point unrotation.
    return [round(value, 6) for value in (cx-width/2, cy-height/2, width, height)]
