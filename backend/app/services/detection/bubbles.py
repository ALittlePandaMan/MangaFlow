from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.request import urlopen

import cv2
import numpy as np
from app.core.config import get_settings
from app.services.base import ModelProvider, ProviderCapabilities, ProviderError

DEFAULT_MODEL_FILENAME = "manga109_segmentation_bubble_1024.onnx"
DEFAULT_MODEL_PATH = f"bubbles/{DEFAULT_MODEL_FILENAME}"
DEFAULT_MODEL_URL = (
    "https://huggingface.co/mednasserallah/manga109-segmentation-bubble-onnx/resolve/"
    "6839b4ea9d95be14f5bbd21ac675d92a7da95ed0/"
    f"{DEFAULT_MODEL_FILENAME}"
)
DEFAULT_MODEL_SHA256 = "593cae61f4c9ffc773fee34d55c28069390d19415c24dc04600f983311618ea3"
DEFAULT_CONFIDENCE_THRESHOLD = 0.25
DEFAULT_NMS_THRESHOLD = 0.5
DEFAULT_MASK_THRESHOLD = 0.5
MODEL_INPUT_SIZE = 1024
MODEL_MASK_CHANNELS = 32
MODEL_PROPOSAL_WIDTH = 4 + 1 + MODEL_MASK_CHANNELS


@dataclass(frozen=True, slots=True)
class LetterboxTransform:
    """Geometry needed to map a 1024-pixel letterbox back to the source page."""

    original_height: int
    original_width: int
    resized_height: int
    resized_width: int
    pad_top: int
    pad_left: int
    scale: float
    input_size: int = MODEL_INPUT_SIZE


@dataclass(slots=True)
class BubbleInstance:
    """One speech balloon in original-page coordinates.

    ``mask`` is a compact uint8 (0/1) crop rather than a full-page image. Its
    upper-left source coordinate is ``mask_origin``. This keeps memory usage
    proportional to balloon area instead of page area times balloon count.
    """

    instance_id: str
    bbox: list[float]
    confidence: float
    polygon: list[list[float]]
    mask: np.ndarray
    mask_origin: tuple[int, int]
    image_shape: tuple[int, int]

    def full_mask(self) -> np.ndarray:
        result = np.zeros(self.image_shape, dtype=np.uint8)
        x, y = self.mask_origin
        height, width = self.mask.shape
        result[y : y + height, x : x + width] = self.mask
        return result

    def contains_point(self, point: tuple[float, float] | list[float]) -> bool:
        x, y = point
        if not np.isfinite(x) or not np.isfinite(y):
            return False
        local_x = int(np.floor(x)) - self.mask_origin[0]
        local_y = int(np.floor(y)) - self.mask_origin[1]
        return bool(
            0 <= local_x < self.mask.shape[1]
            and 0 <= local_y < self.mask.shape[0]
            and self.mask[local_y, local_x]
        )

    def intersection_ratio(self, polygon: list[list[float]], *, padding: int = 0) -> float:
        """Return the fraction of a source polygon covered by this balloon."""

        if padding < 0:
            raise ValueError("Bubble mask padding cannot be negative")
        try:
            points = np.asarray(polygon, dtype=np.float32)
        except (TypeError, ValueError):
            return 0.0
        if points.shape != (len(polygon), 2) or len(points) < 3 or not np.all(np.isfinite(points)):
            return 0.0
        image_height, image_width = self.image_shape
        left = max(0, int(np.floor(points[:, 0].min())))
        top = max(0, int(np.floor(points[:, 1].min())))
        right = min(image_width, int(np.ceil(points[:, 0].max())) + 1)
        bottom = min(image_height, int(np.ceil(points[:, 1].max())) + 1)
        if left >= right or top >= bottom:
            return 0.0

        region_mask = np.zeros((bottom - top, right - left), dtype=np.uint8)
        local_points = np.rint(points - np.array([left, top], dtype=np.float32)).astype(np.int32)
        cv2.fillPoly(region_mask, [local_points], 1)
        region_area = int(np.count_nonzero(region_mask))
        if region_area == 0:
            return 0.0

        balloon_mask = self.mask
        mask_left, mask_top = self.mask_origin
        if padding:
            balloon_mask = cv2.copyMakeBorder(
                balloon_mask,
                padding,
                padding,
                padding,
                padding,
                cv2.BORDER_CONSTANT,
                value=0,
            )
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (padding * 2 + 1, padding * 2 + 1))
            balloon_mask = cv2.dilate(balloon_mask, kernel)
            mask_left -= padding
            mask_top -= padding
        mask_right = mask_left + balloon_mask.shape[1]
        mask_bottom = mask_top + balloon_mask.shape[0]
        overlap_left = max(left, mask_left)
        overlap_top = max(top, mask_top)
        overlap_right = min(right, mask_right)
        overlap_bottom = min(bottom, mask_bottom)
        if overlap_left >= overlap_right or overlap_top >= overlap_bottom:
            return 0.0

        region_crop = region_mask[
            overlap_top - top : overlap_bottom - top,
            overlap_left - left : overlap_right - left,
        ]
        balloon_crop = balloon_mask[
            overlap_top - mask_top : overlap_bottom - mask_top,
            overlap_left - mask_left : overlap_right - mask_left,
        ]
        return float(np.count_nonzero(region_crop & balloon_crop) / region_area)

    def coverage_ratio(self, polygon: list[list[float]], *, padding: int = 0) -> float:
        """Backward-readable alias for :meth:`intersection_ratio`."""

        return self.intersection_ratio(polygon, padding=padding)


def prepare_bubble_input(
    image: np.ndarray,
    *,
    input_size: int = MODEL_INPUT_SIZE,
) -> tuple[np.ndarray, LetterboxTransform]:
    """Letterbox a BGR/BGRA/grayscale page and return an RGB NCHW tensor."""

    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    elif image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Bubble segmentation expects a grayscale, BGR, or BGRA image")
    original_height, original_width = image.shape[:2]
    if original_height <= 0 or original_width <= 0:
        raise ValueError("Bubble segmentation cannot process an empty image")

    scale = min(input_size / original_width, input_size / original_height)
    resized_width = max(1, min(input_size, round(original_width * scale)))
    resized_height = max(1, min(input_size, round(original_height * scale)))
    pad_left = (input_size - resized_width) // 2
    pad_top = (input_size - resized_height) // 2
    resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((input_size, input_size, 3), 114, dtype=np.uint8)
    canvas[pad_top : pad_top + resized_height, pad_left : pad_left + resized_width] = resized
    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
    tensor = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
    return tensor, LetterboxTransform(
        original_height=original_height,
        original_width=original_width,
        resized_height=resized_height,
        resized_width=resized_width,
        pad_top=pad_top,
        pad_left=pad_left,
        scale=scale,
        input_size=input_size,
    )


def decode_bubble_outputs(
    proposals: np.ndarray,
    prototypes: np.ndarray,
    transform: LetterboxTransform,
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    nms_threshold: float = DEFAULT_NMS_THRESHOLD,
    mask_threshold: float = DEFAULT_MASK_THRESHOLD,
    max_detections: int = 100,
) -> list[BubbleInstance]:
    """Decode the two raw YOLO11-seg tensors into source-page instances."""

    rows, prototype_maps = _normalize_outputs(proposals, prototypes)
    scores = rows[:, 4]
    selected = np.flatnonzero(np.isfinite(scores) & (scores >= confidence_threshold))
    if not len(selected):
        return []

    rows = rows[selected]
    scores = rows[:, 4]
    boxes = _cxcywh_to_xyxy(rows[:, :4])
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, transform.input_size)
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, transform.input_size)
    valid = (
        np.all(np.isfinite(boxes), axis=1)
        & ((boxes[:, 2] - boxes[:, 0]) >= 1)
        & ((boxes[:, 3] - boxes[:, 1]) >= 1)
    )
    boxes = boxes[valid]
    scores = scores[valid]
    coefficients = rows[valid, 5:]
    if not len(boxes):
        return []

    flattened_prototypes = prototype_maps.reshape(prototype_maps.shape[0], -1)
    keep = _mask_aware_nms(
        boxes,
        scores,
        coefficients,
        flattened_prototypes,
        prototype_maps.shape[1:],
        nms_threshold,
        mask_threshold=mask_threshold,
        input_size=transform.input_size,
        max_detections=max_detections,
    )
    instances: list[BubbleInstance] = []
    for index in keep:
        model_box = boxes[index]
        logits = coefficients[index] @ flattened_prototypes
        probabilities = _sigmoid(logits).reshape(prototype_maps.shape[1:])
        probabilities = _crop_mask_to_box(probabilities, model_box, transform.input_size)
        model_mask = cv2.resize(
            probabilities,
            (transform.input_size, transform.input_size),
            interpolation=cv2.INTER_LINEAR,
        )
        unpadded = model_mask[
            transform.pad_top : transform.pad_top + transform.resized_height,
            transform.pad_left : transform.pad_left + transform.resized_width,
        ]
        source_mask = cv2.resize(
            unpadded,
            (transform.original_width, transform.original_height),
            interpolation=cv2.INTER_LINEAR,
        )
        binary = (source_mask > mask_threshold).astype(np.uint8)
        compact = _compact_largest_component(binary)
        if compact is None:
            continue
        mask, mask_origin, polygon = compact
        source_box = _model_box_to_source(model_box, transform)
        instances.append(
            BubbleInstance(
                instance_id=f"bubble-{len(instances) + 1:03d}",
                bbox=[
                    float(source_box[0]),
                    float(source_box[1]),
                    float(source_box[2] - source_box[0]),
                    float(source_box[3] - source_box[1]),
                ],
                confidence=float(scores[index]),
                polygon=polygon,
                mask=mask,
                mask_origin=mask_origin,
                image_shape=(transform.original_height, transform.original_width),
            )
        )
    return instances


def resolve_bubble_model_path(config: dict[str, Any] | None = None) -> Path:
    normalized = _bubble_config(config)
    configured = Path(str(normalized.get("model_path") or DEFAULT_MODEL_PATH)).expanduser()
    return configured if configured.is_absolute() else get_settings().model_dir / configured


def ensure_bubble_model(config: dict[str, Any] | None = None) -> Path:
    """Return a verified model path, downloading to a temporary file if needed."""

    normalized = _bubble_config(config)
    model_path = resolve_bubble_model_path(normalized)
    expected_sha256 = str(normalized.get("model_sha256") or DEFAULT_MODEL_SHA256).strip().lower()
    model_url = str(normalized.get("model_url") or DEFAULT_MODEL_URL).strip()
    if len(expected_sha256) != 64 or any(character not in "0123456789abcdef" for character in expected_sha256):
        raise ValueError("bubble model_sha256 must be a 64-character hexadecimal digest")
    if model_path.is_file() and _file_sha256(model_path) == expected_sha256:
        return model_path
    if not model_url:
        raise ValueError(f"Bubble model is missing or corrupt and no model_url is configured: {model_path}")

    model_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = model_path.with_name(f".{model_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    digest = hashlib.sha256()
    try:
        with urlopen(model_url, timeout=120) as response, temporary.open("wb") as destination:
            while chunk := response.read(1024 * 1024):
                destination.write(chunk)
                digest.update(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"Bubble model checksum mismatch: expected {expected_sha256}, downloaded {actual_sha256}"
            )
        os.replace(temporary, model_path)
        return model_path
    finally:
        temporary.unlink(missing_ok=True)


class OnnxBubbleSegmenter(ModelProvider[list[BubbleInstance]]):
    """CPU speech-balloon instance segmentation using YOLO11n and OpenCV DNN."""

    capabilities = ProviderCapabilities(
        name="manga109-yolo11n-bubbles",
        provider_type="bubble-segmentation",
        description="Lightweight Manga109 YOLO11n ONNX speech-balloon instance segmentation",
        devices=["cpu"],
        supports_batch=False,
        extra={"instance_masks": True, "input_size": MODEL_INPUT_SIZE},
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        normalized = _bubble_config(config)
        super().__init__(normalized)
        self._network: Any | None = None
        self._inference_lock = RLock()

    def load(self) -> None:
        model_path = ensure_bubble_model(self.config)
        try:
            network = cv2.dnn.readNetFromONNX(str(model_path))
            network.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            network.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        except cv2.error as exc:
            raise ProviderError(f"Cannot load bubble segmentation model {model_path}: {exc}") from exc
        self._network = network

    def unload(self) -> None:
        with self._inference_lock:
            self._network = None
        super().unload()

    def segment(self, image_path: Path) -> list[BubbleInstance]:
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Cannot read bubble segmentation image: {image_path}")
        return self.segment_array(image)

    def segment_array(self, image: np.ndarray) -> list[BubbleInstance]:
        self.ensure_loaded()
        tensor, transform = prepare_bubble_input(image)
        with self._inference_lock:
            if self._network is None:
                raise ProviderError("Bubble segmentation model is not loaded")
            self._network.setInput(tensor)
            output_names = self._network.getUnconnectedOutLayersNames()
            raw_outputs = self._network.forward(output_names)
        proposals, prototypes = _identify_outputs(raw_outputs)
        return decode_bubble_outputs(
            proposals,
            prototypes,
            transform,
            confidence_threshold=float(self.config.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD)),
            nms_threshold=float(self.config.get("nms_threshold", DEFAULT_NMS_THRESHOLD)),
            mask_threshold=float(self.config.get("mask_threshold", DEFAULT_MASK_THRESHOLD)),
            max_detections=int(self.config.get("max_detections", 100)),
        )


def _bubble_config(config: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(config or {})
    nested = normalized.get("bubble_grouping")
    return dict(nested) if isinstance(nested, dict) else normalized


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _identify_outputs(outputs: Any) -> tuple[np.ndarray, np.ndarray]:
    values = [np.asarray(item) for item in (outputs if isinstance(outputs, (list, tuple)) else [outputs])]
    proposals = next((item for item in values if item.ndim == 3 and MODEL_PROPOSAL_WIDTH in item.shape), None)
    prototypes = next(
        (item for item in values if item.ndim == 4 and item.shape[1] == MODEL_MASK_CHANNELS),
        None,
    )
    if proposals is None or prototypes is None:
        shapes = [tuple(item.shape) for item in values]
        raise ValueError(f"Unexpected bubble model outputs: {shapes}")
    return proposals, prototypes


def _normalize_outputs(proposals: np.ndarray, prototypes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    proposal_array = np.asarray(proposals, dtype=np.float32)
    prototype_array = np.asarray(prototypes, dtype=np.float32)
    if proposal_array.ndim != 3 or proposal_array.shape[0] != 1:
        raise ValueError(f"Expected bubble proposals with shape (1, 37, anchors), got {proposal_array.shape}")
    if proposal_array.shape[1] == MODEL_PROPOSAL_WIDTH:
        rows = proposal_array[0].T
    elif proposal_array.shape[2] == MODEL_PROPOSAL_WIDTH:
        rows = proposal_array[0]
    else:
        raise ValueError(f"Expected 37 values per bubble proposal, got {proposal_array.shape}")
    if prototype_array.ndim != 4 or prototype_array.shape[:2] != (1, MODEL_MASK_CHANNELS):
        raise ValueError(f"Expected bubble prototypes with shape (1, 32, H, W), got {prototype_array.shape}")
    if rows.shape[1] != 5 + prototype_array.shape[1]:
        raise ValueError("Bubble proposal mask coefficients do not match prototype channels")
    return rows, prototype_array[0]


def _cxcywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    converted = np.empty_like(boxes, dtype=np.float32)
    converted[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    converted[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    converted[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    converted[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    return converted


def _mask_aware_nms(
    boxes: np.ndarray,
    scores: np.ndarray,
    coefficients: np.ndarray,
    flattened_prototypes: np.ndarray,
    prototype_shape: tuple[int, int],
    threshold: float,
    *,
    mask_threshold: float,
    input_size: int,
    max_detections: int,
) -> list[int]:
    """Suppress duplicate instances without discarding distinct overlapping masks."""

    areas = np.maximum(0, boxes[:, 2] - boxes[:, 0]) * np.maximum(0, boxes[:, 3] - boxes[:, 1])
    order = np.argsort(scores)[::-1]
    keep: list[int] = []
    mask_cache: dict[int, np.ndarray] = {}

    def candidate_mask(index: int) -> np.ndarray:
        cached = mask_cache.get(index)
        if cached is not None:
            return cached
        probabilities = _sigmoid(coefficients[index] @ flattened_prototypes).reshape(prototype_shape)
        cropped = _crop_mask_to_box(probabilities, boxes[index], input_size)
        binary = cropped > mask_threshold
        mask_cache[index] = binary
        return binary

    for current_value in order:
        if len(keep) >= max_detections:
            break
        current = int(current_value)
        if not np.any(candidate_mask(current)):
            continue
        if not keep:
            keep.append(current)
            continue
        kept = np.asarray(keep, dtype=np.int64)
        left = np.maximum(boxes[current, 0], boxes[kept, 0])
        top = np.maximum(boxes[current, 1], boxes[kept, 1])
        right = np.minimum(boxes[current, 2], boxes[kept, 2])
        bottom = np.minimum(boxes[current, 3], boxes[kept, 3])
        intersection = np.maximum(0, right - left) * np.maximum(0, bottom - top)
        union = areas[current] + areas[kept] - intersection
        iou = np.divide(intersection, union, out=np.zeros_like(intersection), where=union > 0)
        possible_duplicates = kept[iou > threshold]
        current_mask = candidate_mask(current)
        suppress = False
        for previous in possible_duplicates:
            previous_mask = candidate_mask(int(previous))
            mask_intersection = int(np.count_nonzero(current_mask & previous_mask))
            mask_union = int(np.count_nonzero(current_mask | previous_mask))
            if mask_union and mask_intersection / mask_union > threshold:
                suppress = True
                break
        if not suppress:
            keep.append(current)
    return keep


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -80, 80)
    return 1 / (1 + np.exp(-clipped))


def _crop_mask_to_box(mask: np.ndarray, box: np.ndarray, input_size: int) -> np.ndarray:
    height, width = mask.shape
    left = max(0, min(width, int(np.floor(box[0] * width / input_size))))
    top = max(0, min(height, int(np.floor(box[1] * height / input_size))))
    right = max(0, min(width, int(np.ceil(box[2] * width / input_size))))
    bottom = max(0, min(height, int(np.ceil(box[3] * height / input_size))))
    cropped = np.zeros_like(mask)
    cropped[top:bottom, left:right] = mask[top:bottom, left:right]
    return cropped


def _model_box_to_source(box: np.ndarray, transform: LetterboxTransform) -> np.ndarray:
    source = np.asarray(box, dtype=np.float32).copy()
    source[[0, 2]] = (source[[0, 2]] - transform.pad_left) / transform.scale
    source[[1, 3]] = (source[[1, 3]] - transform.pad_top) / transform.scale
    source[[0, 2]] = np.clip(source[[0, 2]], 0, transform.original_width)
    source[[1, 3]] = np.clip(source[[1, 3]], 0, transform.original_height)
    return source


def _compact_largest_component(
    binary: np.ndarray,
) -> tuple[np.ndarray, tuple[int, int], list[list[float]]] | None:
    component_count, labels, statistics, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if component_count <= 1:
        return None
    largest_label = 1 + int(np.argmax(statistics[1:, cv2.CC_STAT_AREA]))
    left = int(statistics[largest_label, cv2.CC_STAT_LEFT])
    top = int(statistics[largest_label, cv2.CC_STAT_TOP])
    width = int(statistics[largest_label, cv2.CC_STAT_WIDTH])
    height = int(statistics[largest_label, cv2.CC_STAT_HEIGHT])
    compact = (labels[top : top + height, left : left + width] == largest_label).astype(np.uint8)
    contours, _ = cv2.findContours(compact, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    epsilon = max(0.5, cv2.arcLength(contour, True) * 0.002)
    approximated = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
    polygon = [[float(point[0] + left), float(point[1] + top)] for point in approximated]
    return compact, (left, top), polygon


__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_MODEL_FILENAME",
    "DEFAULT_MODEL_PATH",
    "DEFAULT_MODEL_SHA256",
    "DEFAULT_MODEL_URL",
    "DEFAULT_NMS_THRESHOLD",
    "MODEL_INPUT_SIZE",
    "BubbleInstance",
    "LetterboxTransform",
    "OnnxBubbleSegmenter",
    "decode_bubble_outputs",
    "ensure_bubble_model",
    "prepare_bubble_input",
    "resolve_bubble_model_path",
]
