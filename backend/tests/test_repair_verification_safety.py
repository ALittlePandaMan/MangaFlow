import hashlib
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from app.services.inpainting import local_text, text_segmentation
from app.services.inpainting.local_text import (
    LocalResidualConfirmer,
    focus_windows,
    inspection_area,
    refine_probability,
)
from app.services.inpainting.structure import retry_structure_loss
from app.services.inpainting.verification import verify_and_retry
from PIL import Image


def setup_repair(tmp_path, image, mask):
    source, output, mask_path = [tmp_path / name for name in ("source.png", "clean.png", "mask.png")]
    Image.fromarray(image).save(source)
    Image.fromarray(image).save(output)
    cv2.imwrite(str(mask_path), mask)
    metadata = {"version": 2, "automatic_mask_sha256": hashlib.sha256(mask.tobytes()).hexdigest()}
    return source, output, [(mask_path, metadata)]


def predict(path):
    return (np.asarray(Image.open(path).convert("L")) < 100).astype(np.float32)


class Erase:
    calls = 0

    def inpaint(self, source, mask, output, kind):
        self.calls += 1
        image = np.asarray(Image.open(source)).copy()
        image[cv2.imread(str(mask), 0) > 0] = 255
        Image.fromarray(image).save(output)


def test_neighbourhood_residual_is_reported_but_never_erased(tmp_path):
    image = np.full((80, 100, 3), 255, np.uint8)
    image[30:36, 41:44] = 0  # Just outside old selected mask.
    mask = np.zeros(image.shape[:2], np.uint8)
    mask[20:50, 20:40] = 255
    source, output, regions = setup_repair(tmp_path, image, mask)
    provider = Erase()
    report = verify_and_retry(provider, source, output, regions, predict)[0]
    assert report["outside_mask_pixels"] == 18
    assert report["residual_pixels"] == 0 and report["status"] == "residual"
    assert provider.calls == 0
    assert np.array_equal(np.asarray(Image.open(output)), image)


def test_inspect_original_polygon_finds_strokes_far_from_old_mask(tmp_path):
    image = np.full((100, 100, 3), 255, np.uint8)
    image[60:65, 60:65] = 0
    mask = np.zeros(image.shape[:2], np.uint8)
    mask[20:30, 20:30] = 255
    source, output, regions = setup_repair(tmp_path, image, mask)
    inspection = inspection_area(mask.shape, [[[10, 10], [80, 10], [80, 80], [10, 80]]])
    report = verify_and_retry(Erase(), source, output, regions, predict, inspection_masks=[inspection])[0]
    assert report["outside_mask_pixels"] == 25
    assert report["retry_scope"] == "existing_mask_only"


def test_local_disagreement_is_uncertain_not_passed_or_erased(tmp_path):
    image = np.full((80, 100, 3), 255, np.uint8)
    image[30:36, 31:34] = 0
    mask = np.zeros(image.shape[:2], np.uint8)
    mask[20:50, 20:40] = 255
    source, output, regions = setup_repair(tmp_path, image, mask)
    provider = Erase()
    report = verify_and_retry(provider, source, output, regions, predict,
                              confirm=lambda path, raw: np.zeros_like(raw))[0]
    assert report["status"] == "uncertain" and report["uncertain_pixels"] == 18
    assert provider.calls == 0


def test_matching_manual_mask_fingerprint_still_never_retries(tmp_path):
    image = np.zeros((30, 30, 3), np.uint8)
    mask = np.full(image.shape[:2], 255, np.uint8)
    source, output, regions = setup_repair(tmp_path, image, mask)
    regions[0][1]["method"] = "manual_mask"
    provider = Erase()
    verify_and_retry(provider, source, output, regions, predict)
    assert provider.calls == 0


def test_retry_cannot_replace_artwork_with_lower_text_score(tmp_path):
    image = np.full((100, 160, 3), 255, np.uint8)
    image[49:52, 5:155] = 0  # Long border crossing through selected text area.
    image[55:65, 70:80] = 0
    mask = np.zeros(image.shape[:2], np.uint8)
    mask[40:70, 50:100] = 255
    source, output, regions = setup_repair(tmp_path, image, mask)
    report = verify_and_retry(Erase(), source, output, regions, predict)[0]
    assert report["retry"] == "rejected_structure_change"
    assert report["structure_check"]["lost_pixels"] > 4
    assert np.array_equal(np.asarray(Image.open(output)), image)


def test_tiny_score_improvement_does_not_replace_first_result(tmp_path):
    image = np.zeros((30, 30, 3), np.uint8)
    mask = np.full(image.shape[:2], 255, np.uint8)
    source, output, regions = setup_repair(tmp_path, image, mask)

    def almost_same(path):
        result = np.ones(mask.shape, np.float32)
        if path not in (source, output):
            result[0, 0] = 0
        return result

    report = verify_and_retry(Erase(), source, output, regions, almost_same)[0]
    assert report["retry"] == "rejected_no_improvement"
    assert np.array_equal(np.asarray(Image.open(output)), image)


def test_structure_check_allows_removing_contained_strokes():
    image = np.full((100, 160, 3), 255, np.uint8)
    image[49:52, 70:85] = 0
    mask = np.zeros(image.shape[:2], np.uint8)
    mask[40:70, 50:100] = 255
    assert not retry_structure_loss(image, np.full_like(image, 255), mask)["damaged"]


def test_focus_windows_are_bounded_coalesced_and_clipped():
    boxes = [[i * 450, j * 450, 15, 30] for i in range(5) for j in range(5)]
    windows = focus_windows((2200, 2200), boxes)
    assert len(windows) == 6
    assert all(0 <= x <= 1688 and 0 <= y <= 1688 and w == h == 512 for x, y, w, h in windows)
    assert len(focus_windows((600, 600), [[10, 10, 10, 10], [15, 15, 10, 10]])) == 1
    assert focus_windows((40, 60), [[0, 0, 10, 20]]) == [(0, 0, 60, 40)]
    assert focus_windows((40, 60), [[-100, -100, 2, 2], [0, 0, -1, 20]]) == []
    assert focus_windows((40, 60), [[0, 0, 10, 20]], limit=0) == []


def test_refinement_maps_crops_to_page_and_never_adds_evidence_outside_text_boxes(monkeypatch):
    calls = []

    def local(path, model, cache, *, window, backend=None):
        calls.append(window)
        return np.ones((window[3], window[2]), np.float32), {}

    monkeypatch.setattr(local_text, "text_probability", local)
    base = np.zeros((1800, 1200), np.float32)
    result, meta = refine_probability(Path("source"), Path("models"), base, [[1050, 1500, 40, 16]])
    assert meta["windows"] == 1 and calls == [(688, 1252, 512, 512)]
    assert np.all(result[1500:1516, 1050:1090] == 1)
    assert result[1450, 1000] == 0 and result[1550, 1100] == 0
    assert not np.any(base)
    _, meta = refine_probability(Path("source"), Path("models"), base, [[0, 0, 100, 100]])
    assert meta["status"] == "not_needed"


def test_local_confirmation_reuses_fixed_windows_and_original_maps(monkeypatch):
    calls = []
    def local(path, model, cache, *, window, backend=None):
        calls.append((path, window))
        return np.ones((window[3], window[2]), np.float32), {}
    monkeypatch.setattr(local_text, "text_probability", local)
    confirm = LocalResidualConfirmer(Path("source"), Path("models"))
    raw = np.zeros((1800, 1200), np.uint8)
    raw[20:25, 30:35] = 255
    assert np.array_equal(confirm(Path("first"), raw), raw)
    first_windows = confirm.windows.copy()
    raw[1000:1005, 1000:1005] = 255
    checked = confirm(Path("retry"), raw)
    assert checked[1000, 1000] == 0  # Budget/unseen areas remain uncertain.
    assert confirm.windows == first_windows
    assert len([p for p, _ in calls if p == Path("source")]) == 1


def test_local_probability_cache_binds_window_and_content(tmp_path, monkeypatch):
    model = tmp_path / text_segmentation.MODEL_FILENAME
    model.parent.mkdir()
    model.write_bytes(b"mock model")
    source = tmp_path / "source.png"
    pixels = np.zeros((100, 200, 3), np.uint8)
    pixels[:, 100:] = 255
    Image.fromarray(pixels).save(source)
    calls = []

    def infer(image):
        calls.append(image.shape)
        return image[:, :, 0].astype(np.float32) / 255

    monkeypatch.setattr(text_segmentation, "_segmenter", lambda *args: SimpleNamespace(predict=infer))
    for window, expected in [((0, 0, 80, 80), 0), ((100, 0, 80, 80), 1), ((0, 0, 80, 80), 0)]:
        result, _ = text_segmentation.text_probability(source, tmp_path, tmp_path / "cache", window=window)
        assert result.shape == (80, 80) and np.all(result == expected)
    assert len(calls) == 2
    with pytest.raises(ValueError, match="window"):
        text_segmentation.text_probability(source, tmp_path, window=(199, 0, 80, 80))
