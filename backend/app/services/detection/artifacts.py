from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from app.storage.files import StorageManager
from PIL import Image

BUBBLE_GEOMETRY_KEY = "bubble_geometry"
BUBBLE_GEOMETRY_SCHEMA_VERSION = 1
_SAFE_INSTANCE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def persist_balloon_mask(
    storage: StorageManager,
    page_directory: Path,
    *,
    instance_id: str,
    mask: Any,
    origin: tuple[int, int] | None,
    image_shape: tuple[int, int],
    confidence: float | None,
    parent_instance_id: str | None,
) -> dict[str, Any]:
    """Persist one exact compact instance mask and return its JSON manifest entry."""

    array = np.asarray(mask)
    if array.ndim != 2 or array.size == 0:
        raise ValueError("Balloon constraint mask must be a non-empty 2D array")
    binary = np.where(array > 0, 255, 0).astype(np.uint8)
    if cv2.countNonZero(binary) == 0:
        raise ValueError("Balloon constraint mask cannot be empty")
    if origin is None or len(origin) != 2:
        raise ValueError("Balloon constraint mask needs a two-dimensional origin")
    origin_x, origin_y = int(origin[0]), int(origin[1])
    page_height, page_width = image_shape
    height, width = binary.shape
    if (
        origin_x < 0
        or origin_y < 0
        or origin_x + width > page_width
        or origin_y + height > page_height
    ):
        raise ValueError("Balloon constraint mask escapes the source image")

    safe_id = _SAFE_INSTANCE_ID.sub("_", instance_id).strip("._")
    if not safe_id:
        raise ValueError("Balloon constraint instance ID is invalid")
    directory = (page_directory / "bubbles").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    encoded_ok, encoded = cv2.imencode(".png", binary)
    if not encoded_ok:
        raise ValueError("Cannot encode balloon constraint as PNG")
    payload = encoded.tobytes()
    digest = hashlib.sha256(payload).hexdigest()
    # Content-addressed filenames prevent a failed forced-detection transaction
    # from overwriting geometry still referenced by the previous DB manifest.
    destination = directory / f"{safe_id}-{digest[:16]}.png"
    temporary = directory / f".{safe_id}.{uuid.uuid4().hex}.tmp.png"
    try:
        if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            temporary.write_bytes(payload)
            temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "path": storage.relative(destination),
        "origin": [origin_x, origin_y],
        "size": [width, height],
        "bbox": [float(origin_x), float(origin_y), float(width), float(height)],
        "confidence": round(float(confidence or 0.0), 4),
        "kind": "split_child" if parent_instance_id else "instance",
        "parent_bubble_id": parent_instance_id,
        "sha256": digest,
    }


def load_balloon_mask(
    storage: StorageManager,
    page_directory: Path,
    entry: dict[str, Any],
    *,
    image_shape: tuple[int, int],
) -> np.ndarray:
    """Load and validate a page-sized binary mask from a manifest entry."""

    path_value = entry.get("path")
    origin_value = entry.get("origin")
    size_value = entry.get("size")
    if not isinstance(path_value, str) or not path_value:
        raise ValueError("Balloon constraint path is missing")
    if not isinstance(origin_value, list) or len(origin_value) != 2:
        raise ValueError("Balloon constraint origin is invalid")
    if not isinstance(size_value, list) or len(size_value) != 2:
        raise ValueError("Balloon constraint size is invalid")

    width, height = int(size_value[0]), int(size_value[1])
    origin_x, origin_y = int(origin_value[0]), int(origin_value[1])
    page_height, page_width = image_shape
    if (
        width <= 0
        or height <= 0
        or origin_x < 0
        or origin_y < 0
        or origin_x + width > page_width
        or origin_y + height > page_height
    ):
        raise ValueError("Balloon constraint mask escapes the source image")

    path = storage.absolute(path_value)
    bubble_directory = (page_directory / "bubbles").resolve()
    if path.parent != bubble_directory:
        raise ValueError("Balloon constraint path does not belong to this page")
    if not path.is_file():
        raise ValueError("Balloon constraint file is missing")
    expected_sha256 = entry.get("sha256")
    if isinstance(expected_sha256, str) and expected_sha256:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError("Balloon constraint checksum mismatch")

    try:
        with Image.open(path) as image:
            decoded_size = image.size
    except Exception as exc:
        raise ValueError("Balloon constraint file cannot be decoded") from exc
    if decoded_size != (width, height):
        raise ValueError("Balloon constraint dimensions do not match its manifest")

    compact = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if compact is None:
        raise ValueError("Balloon constraint file cannot be decoded")
    if compact.shape != (height, width):
        raise ValueError("Balloon constraint dimensions do not match its manifest")
    if cv2.countNonZero(compact) == 0:
        raise ValueError("Balloon constraint file is empty")
    full = np.zeros((page_height, page_width), dtype=np.uint8)
    full[origin_y : origin_y + height, origin_x : origin_x + width] = np.where(
        compact > 0,
        255,
        0,
    ).astype(np.uint8)
    return full


def bubble_geometry_items(metadata: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    manifest = (metadata or {}).get(BUBBLE_GEOMETRY_KEY)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != BUBBLE_GEOMETRY_SCHEMA_VERSION:
        return {}
    items = manifest.get("instances")
    if not isinstance(items, dict):
        return {}
    return {key: value for key, value in items.items() if isinstance(key, str) and isinstance(value, dict)}


__all__ = [
    "BUBBLE_GEOMETRY_KEY",
    "BUBBLE_GEOMETRY_SCHEMA_VERSION",
    "bubble_geometry_items",
    "load_balloon_mask",
    "persist_balloon_mask",
]
