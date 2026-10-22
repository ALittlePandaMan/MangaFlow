"""Shared OCR input: source pixels inside the selected frame, never its neighbours."""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from threading import RLock

import cv2
import numpy as np
from app.services.base import ProviderError
from app.utils.geometry import bbox_to_polygon, order_quadrilateral
from PIL import Image, ImageOps

_crop_lock = RLock()
# Invalidate request checkpoints when the geometry-to-pixels contract changes.
OCR_CROP_VERSION = 4
OCR_FRAME_KEY = "ocr_source_frame"


def source_crop_angle(region) -> float | None:
    """Use an angle only while the geometry it describes is authoritative."""
    layout = region.layout_data or {}
    frame = layout.get(OCR_FRAME_KEY) or {}
    if frame.get("polygon") == getattr(region, "polygon", None):
        angle = frame.get("angle")
        if isinstance(angle, (int, float)) and not isinstance(angle, bool) and math.isfinite(angle):
            return float(angle)
    if layout.get("manual"):
        return None
    angle = (layout.get("detection") or {}).get("text_angle")
    return float(angle) if isinstance(angle, (int, float)) and not isinstance(angle, bool) and math.isfinite(angle) else None


def _reading_quad(quad, orientation, angle):
    points = np.asarray(quad, dtype=np.float64)
    edges = np.roll(points, -1, axis=0) - points
    lengths = np.linalg.norm(edges, axis=1)
    if angle is not None:
        radians = math.radians(angle)
        direction = np.asarray([math.cos(radians), math.sin(radians)])
        # Detector angles also disambiguate tall horizontal paragraphs and
        # short vertical blocks: their longest edge need not be the reading axis.
        if abs(edges[1] @ direction / lengths[1]) > abs(edges[0] @ direction / lengths[0]):
            points = np.roll(points, -1, axis=0)
        if (points[1] - points[0]) @ direction < 0:
            points = np.roll(points, -2, axis=0)
    elif orientation in {"horizontal", "vertical", "rotated"}:
        # Upright multiline frames already have the correct axes. For oblique
        # frames without an angle hint, use the declared reading direction to
        # prevent the top-left-corner heuristic changing axes at 45 degrees.
        upright = min(abs(edges[0, 0]), abs(edges[0, 1])) / lengths[0] < math.sin(math.radians(2))
        width, height = (lengths[0] + lengths[2]) / 2, (lengths[1] + lengths[3]) / 2
        vertical = orientation == "vertical"
        if not upright and ((vertical and width > height) or (not vertical and width < height)):
            points = np.roll(points, -1, axis=0)
        reading = points[3] - points[0] if vertical else points[1] - points[0]
        if reading[1 if vertical else 0] < 0:
            points = np.roll(points, -2, axis=0)
    return points


def carry_source_frame(region, polygon) -> dict:
    """Carry the source x-axis through editor transforms, never text rotation.

    The editor preserves vertex correspondence when moving/rotating/resizing a
    frame. Remember that axis so a tall multiline block does not change axes
    when its rotation crosses 45 degrees. A geometry snapshot rejects stale
    hints after other operations replace the outline.
    """
    before = region.polygon
    if not before or not polygon or len(before) != 4 or len(polygon) != 4:
        return {}
    old, new = order_quadrilateral(before), order_quadrilateral(polygon)
    if old is None or new is None or not all(cv2.isContourConvex(np.asarray(points, dtype=np.float32)) for points in (before, polygon)):
        return {}
    ordered = _reading_quad(old, region.orientation, source_crop_angle(region))
    # A vertex-order-only rewrite does not rotate the actual frame.
    if {tuple(point) for point in before} == {tuple(point) for point in polygon}:
        vector = ordered[1] - ordered[0]
    else:
        first, second = (before.index(point.tolist()) for point in ordered[:2])
        vector = np.asarray(polygon[second]) - np.asarray(polygon[first])
    angle = math.degrees(math.atan2(float(vector[1]), float(vector[0])))
    return {"polygon": [list(point) for point in polygon], "angle": angle}


def _inside_mask(vertices, width, height):
    """Exact even-odd pixel-centre fill without a vertices × image-area scan.

    Retain every lasso vertex: simplification could include unselected pixels.
    A long outline costs O(vertices × rows), rather than multiplying by columns.
    """
    following = np.roll(vertices, -1, axis=0)
    nonhorizontal = vertices[:, 1] != following[:, 1]
    start, end = vertices[nonhorizontal], following[nonhorizontal]
    mask = np.zeros((height, width), dtype=bool)
    for row in range(height):
        y = row + .5
        crossing = (start[:, 1] > y) != (end[:, 1] > y)
        a, b = start[crossing], end[crossing]
        intersections = np.sort(a[:, 0] + (y - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1]))
        for left, right in zip(intersections[::2], intersections[1::2], strict=True):
            first = max(0, min(width, math.ceil(float(left) - .5)))
            last = max(0, min(width, math.ceil(float(right) - .5)))
            mask[row, first:last] = True
    return mask


@lru_cache(maxsize=2)
def decoded_page(path: str) -> Image.Image:
    # Uploaded originals are immutable; do not mutate the shared decoded page.
    with Image.open(path) as source:
        return source.convert("RGB")


def crop_region(image_path: Path, bbox: list[float], padding: int = 4,
                *, polygon: list[list[float]] | None = None,
                orientation: str | None = None, crop_angle: float | None = None) -> Image.Image:
    """Mask before rectifying, so interpolation cannot sample outside the frame.

    The source polygon is authoritative (not the translated frame). Missing
    legacy polygons use the bbox, but malformed polygons must fail closed rather
    than silently upload a larger rectangle. Padding adds WHITE pixels only.
    """
    if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                             or not math.isfinite(v) for v in bbox) or min(bbox[2:]) <= 0):
        raise ProviderError("OCR 区域无效，请重新选择文本区域。")
    points = bbox_to_polygon(bbox) if polygon is None or polygon == [] else polygon
    if (not isinstance(points, (list, tuple)) or len(points) < 3
            or any(not isinstance(point, (list, tuple)) or len(point) != 2
                   or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 1e9
                          for v in point) for point in points)):
        raise ProviderError("OCR 文本框轮廓无效，请重新选择文本区域。")
    contour = np.asarray(points, dtype=np.float32)
    # Only rectify a convex outline in its original winding. Reordering a
    # crossed quad into a convex hull would reveal pixels the user did not select.
    # Concave, crossed and very narrow frames use the same exact mask as other
    # polygons instead of failing merely because perspective mapping is unsafe.
    quad = order_quadrilateral(points) if len(points) == 4 and cv2.isContourConvex(contour) else None
    if crop_angle is not None and (isinstance(crop_angle, bool) or not isinstance(crop_angle, (int, float)) or not math.isfinite(crop_angle)):
        raise ProviderError("OCR 文字角度无效。")
    vertices = _reading_quad(quad, orientation, crop_angle) if quad is not None else np.asarray(points, dtype=np.float64)
    # A self-crossing outline can have zero signed area and still contain text.
    # Reject only a collapsed envelope here; the pixel mask below checks whether
    # any of the actual selected area intersects the image.
    if cv2.contourArea(cv2.convexHull(contour)) < 1:
        raise ProviderError("OCR 文本框轮廓无效，请重新选择文本区域。")
    padding = max(0, min(int(padding), 64))
    with _crop_lock:
        image = decoded_page(str(image_path.resolve()))
        left = max(0, math.floor(float(vertices[:, 0].min())))
        top = max(0, math.floor(float(vertices[:, 1].min())))
        right = min(image.width, math.ceil(float(vertices[:, 0].max())))
        bottom = min(image.height, math.ceil(float(vertices[:, 1].max())))
        if right <= left or bottom <= top:
            raise ProviderError("OCR 区域不在图片范围内。")
        pixels = np.array(image.crop((left, top, right, bottom)))
        page_diagonal = math.ceil(math.hypot(image.width, image.height))
        # A partially visible quad must not be rescaled using its potentially
        # enormous off-page dimensions. Keep its visible selected pixels at
        # native resolution instead of rectifying them into a subpixel sliver.
        if quad is not None and (np.any(vertices < 0) or np.any(vertices[:, 0] > image.width) or np.any(vertices[:, 1] > image.height)):
            quad = None
    local = vertices - (left, top)
    # Test pixel centres instead of rounding vertices outwards. This also
    # handles concave frames without revealing their rectangular surroundings.
    mask = _inside_mask(local, pixels.shape[1], pixels.shape[0])
    if not mask.any():
        raise ProviderError("OCR 区域不在图片范围内。")
    pixels[~mask] = 255
    if quad is not None:
        # Keep horizontal/vertical text orientation; remove the frame's slant
        # and perspective only. Bound output even for partially off-page frames.
        width = max(1, min(page_diagonal, math.ceil(max(np.linalg.norm(vertices[1] - vertices[0]),
                                                       np.linalg.norm(vertices[2] - vertices[3])))))
        height = max(1, min(page_diagonal, math.ceil(max(np.linalg.norm(vertices[3] - vertices[0]),
                                                        np.linalg.norm(vertices[2] - vertices[1])))))
        # Coordinates describe pixel edges; OpenCV samples pixel centres.
        target = np.asarray([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32) - .5
        matrix = cv2.getPerspectiveTransform((local - .5).astype(np.float32), target)
        pixels = cv2.warpPerspective(pixels, matrix, (width, height), flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    return ImageOps.expand(Image.fromarray(pixels), border=padding, fill="white")
