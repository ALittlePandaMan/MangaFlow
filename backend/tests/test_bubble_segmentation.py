from __future__ import annotations

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from app.services.detection import bubbles
from app.services.detection.bubbles import (
    MODEL_INPUT_SIZE,
    OnnxBubbleSegmenter,
    decode_bubble_outputs,
    ensure_bubble_model,
    prepare_bubble_input,
    resolve_bubble_model_path,
)


def _model_box(source_box: list[float], transform: bubbles.LetterboxTransform) -> list[float]:
    x, y, width, height = source_box
    left = x * transform.scale + transform.pad_left
    top = y * transform.scale + transform.pad_top
    right = (x + width) * transform.scale + transform.pad_left
    bottom = (y + height) * transform.scale + transform.pad_top
    return [(left + right) / 2, (top + bottom) / 2, right - left, bottom - top]


def _synthetic_outputs(
    transform: bubbles.LetterboxTransform,
) -> tuple[np.ndarray, np.ndarray]:
    proposals = np.zeros((1, 37, 21_504), dtype=np.float32)
    prototypes = np.zeros((1, 32, 256, 256), dtype=np.float32)
    prototypes[0, 0] = 1
    source_boxes = (
        ([20, 10, 80, 40], 0.92),
        ([22, 11, 80, 40], 0.75),  # Suppressed by NMS.
        ([120, 20, 60, 50], 0.81),
    )
    for index, (source_box, score) in enumerate(source_boxes):
        proposals[0, :4, index] = _model_box(source_box, transform)
        proposals[0, 4, index] = score
        proposals[0, 5, index] = 20
    return proposals, prototypes


def test_prepares_1024_rgb_letterbox_and_tracks_geometry() -> None:
    image = np.full((100, 200, 3), [10, 20, 30], dtype=np.uint8)

    tensor, transform = prepare_bubble_input(image)

    assert tensor.shape == (1, 3, MODEL_INPUT_SIZE, MODEL_INPUT_SIZE)
    assert tensor.dtype == np.float32
    assert transform.scale == pytest.approx(5.12)
    assert transform.resized_width == 1024
    assert transform.resized_height == 512
    assert transform.pad_left == 0
    assert transform.pad_top == 256
    assert tensor[0, :, 0, 0] == pytest.approx(np.array([114, 114, 114]) / 255)
    assert tensor[0, :, 256, 0] == pytest.approx(np.array([30, 20, 10]) / 255)


def test_decodes_nms_cropped_masks_and_maps_instances_to_source_page() -> None:
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    _, transform = prepare_bubble_input(image)
    proposals, prototypes = _synthetic_outputs(transform)

    instances = decode_bubble_outputs(proposals, prototypes, transform)

    assert [item.instance_id for item in instances] == ["bubble-001", "bubble-002"]
    assert [item.confidence for item in instances] == pytest.approx([0.92, 0.81])
    assert instances[0].bbox == pytest.approx([20, 10, 80, 40], abs=0.01)
    assert instances[1].bbox == pytest.approx([120, 20, 60, 50], abs=0.01)
    assert instances[0].mask.shape[0] < image.shape[0]
    assert instances[0].mask.shape[1] < image.shape[1]
    assert instances[0].contains_point((40, 25))
    assert not instances[0].contains_point((150, 40))
    assert instances[0].intersection_ratio([[30, 15], [90, 15], [90, 45], [30, 45]]) > 0.95
    assert instances[0].intersection_ratio([[130, 30], [160, 30], [160, 60], [130, 60]]) == 0
    assert np.count_nonzero(instances[0].full_mask()[:, 115:]) == 0
    assert np.count_nonzero(instances[1].full_mask()[:, :105]) == 0


def test_optional_mask_padding_expands_assignment_without_mutating_mask() -> None:
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    _, transform = prepare_bubble_input(image)
    proposals, prototypes = _synthetic_outputs(transform)
    instance = decode_bubble_outputs(proposals, prototypes, transform)[0]
    original = instance.mask.copy()
    edge_polygon = [[16, 20], [18, 20], [18, 30], [16, 30]]

    without_padding = instance.intersection_ratio(edge_polygon)
    with_padding = instance.intersection_ratio(edge_polygon, padding=5)

    assert without_padding == 0
    assert with_padding > 0
    assert np.array_equal(instance.mask, original)
    with pytest.raises(ValueError, match="cannot be negative"):
        instance.intersection_ratio(edge_polygon, padding=-1)


def test_resolves_relative_model_under_settings_and_downloads_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"deterministic fake ONNX bytes"
    checksum = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(bubbles, "get_settings", lambda: SimpleNamespace(model_dir=tmp_path / "models"))
    monkeypatch.setattr(bubbles, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))
    config = {
        "model_path": "bubbles/model.onnx",
        "model_url": "https://example.invalid/model.onnx",
        "model_sha256": checksum,
    }

    assert resolve_bubble_model_path() == tmp_path / "models" / "bubbles" / bubbles.DEFAULT_MODEL_FILENAME
    resolved = resolve_bubble_model_path(config)
    downloaded = ensure_bubble_model(config)

    assert resolved == tmp_path / "models" / "bubbles" / "model.onnx"
    assert downloaded == resolved
    assert downloaded.read_bytes() == payload
    assert not list(downloaded.parent.glob(".*.tmp"))

    monkeypatch.setattr(
        bubbles,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("verified existing model should not be downloaded again"),
    )
    assert ensure_bubble_model(config) == downloaded


def test_checksum_failure_never_publishes_partial_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "model.onnx"
    target.write_bytes(b"previous corrupt file")
    expected = hashlib.sha256(b"expected content").hexdigest()
    monkeypatch.setattr(bubbles, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"wrong content"))

    with pytest.raises(ValueError, match="checksum mismatch"):
        ensure_bubble_model(
            {
                "model_path": str(target),
                "model_url": "https://example.invalid/model.onnx",
                "model_sha256": expected,
            }
        )

    assert target.read_bytes() == b"previous corrupt file"
    assert not list(tmp_path.glob(".*.tmp"))


class _FakeNetwork:
    def __init__(self, outputs: tuple[np.ndarray, np.ndarray]) -> None:
        self.outputs = outputs
        self.input: np.ndarray | None = None
        self.backend: int | None = None
        self.target: int | None = None

    def setPreferableBackend(self, backend: int) -> None:
        self.backend = backend

    def setPreferableTarget(self, target: int) -> None:
        self.target = target

    def setInput(self, value: np.ndarray) -> None:
        self.input = value

    def getUnconnectedOutLayersNames(self) -> list[str]:
        return ["output0", "output1"]

    def forward(self, _names: list[str]) -> list[np.ndarray]:
        proposals, prototypes = self.outputs
        return [prototypes, proposals]  # Output identification must not rely on ordering.


def test_segmenter_runs_opencv_network_without_onnxruntime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    _, transform = prepare_bubble_input(image)
    fake_network = _FakeNetwork(_synthetic_outputs(transform))
    model_bytes = b"verified fake model"
    model_path = tmp_path / "model.onnx"
    model_path.write_bytes(model_bytes)
    monkeypatch.setattr(cv2.dnn, "readNetFromONNX", lambda _path: fake_network)
    segmenter = OnnxBubbleSegmenter(
        {
            "model_path": str(model_path),
            "model_sha256": hashlib.sha256(model_bytes).hexdigest(),
        }
    )

    instances = segmenter.segment_array(image)

    assert len(instances) == 2
    assert fake_network.input is not None
    assert fake_network.input.shape == (1, 3, 1024, 1024)
    assert fake_network.backend == cv2.dnn.DNN_BACKEND_OPENCV
    assert fake_network.target == cv2.dnn.DNN_TARGET_CPU
