import hashlib
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from app.services.detection.panels import detect_panel_frames, panel_reading_order
from app.services.inpainting.text_segmentation import ComicTextSegmenter, learned_text_mask, text_probability
from app.services.inpainting.verification import residual_mask, verify_and_retry
from app.services.layout.shape import inscribed_text_box
from app.services.ocr.quality import ocr_review_hints
from app.services.translation.context import attach_page_image, page_context, page_image_data_url
from app.services.translation.providers import OpenAICompatibleTranslator
from PIL import Image


def test_pixel_segmentation_keeps_seeded_weak_strokes_and_bounds_growth():
    probability = np.zeros((100, 100), np.float32)
    probability[30:50, 28:35] = 0.4
    probability[30:50, 30:35] = 0.95
    probability[60:64, 28:30] = 0.95  # Detached outside illustration.
    region = np.zeros((100, 100), np.uint8)
    region[20:80, 30:75] = 255
    mask = learned_text_mask(probability, region, expand=3, allow_outside=True)
    assert np.all(mask[30:50, 28:35] == 255)
    assert not np.any(mask[60:64, 28:30])
    bounded = learned_text_mask(probability, region, expand=3)
    assert not np.any(bounded[region == 0])
    assert not np.any(learned_text_mask(np.ones_like(probability), region))


def test_pixel_segmentation_rejects_invalid_maps():
    with pytest.raises(ValueError):
        learned_text_mask(np.full((8, 8), np.nan), np.ones((8, 8), np.uint8))
    with pytest.raises(ValueError):
        learned_text_mask(np.ones((8, 7)), np.ones((8, 8), np.uint8))


def test_missing_or_untrusted_weights_do_not_infer(tmp_path):
    probability, meta = text_probability(Path("no-source"), tmp_path)
    assert probability is None and meta["reason"] == "model_missing"
    fake = tmp_path / "fake.onnx"
    fake.write_bytes(b"not a model")
    with pytest.raises(ValueError, match="checksum"):
        ComicTextSegmenter(fake)


class RetryProvider:
    def __init__(self, improves=True, fail=False):
        self.calls = 0
        self.improves = improves
        self.fail = fail

    def inpaint(self, source, mask, output, kind):
        self.calls += 1
        if self.fail:
            raise ValueError("inference error")
        pixels = np.asarray(Image.open(source)).copy()
        if self.improves:
            pixels[cv2.imread(str(mask), 0) > 0] = 255
        Image.fromarray(pixels).save(output)
        return output


def fake_probability(path):
    return (np.asarray(Image.open(path).convert("L")) < 100).astype(np.float32)


@pytest.mark.parametrize(
    "improves,fail,expected",
    [(True, False, "accepted"), (False, False, "rejected_no_improvement"), (True, True, "failed")],
)
def test_repair_retry_is_bounded_and_keeps_successful_first_result(tmp_path, improves, fail, expected):
    src, output, path = [tmp_path / name for name in ("source.png", "clean.png", "mask.png")]
    image = np.full((60, 100, 3), 255, np.uint8)
    image[25:35, 40:50] = 0
    image[0:5] = 20
    Image.fromarray(image).save(src)
    Image.fromarray(image).save(output)
    mask = np.zeros((60, 100), np.uint8)
    mask[20:40, 30:60] = 255
    cv2.imwrite(str(path), mask)
    provider = RetryProvider(improves, fail)
    reports = verify_and_retry(
        provider,
        src,
        output,
        [(path, {"version": 2, "automatic_mask_sha256": hashlib.sha256(mask.tobytes()).hexdigest()})],
        fake_probability,
    )
    assert provider.calls == 1
    assert reports[0]["retry"] == expected
    assert np.array_equal(np.asarray(Image.open(output))[mask == 0], image[mask == 0])
    if expected != "accepted":
        assert np.array_equal(np.asarray(Image.open(output)), image)
    else:
        assert reports[0]["residual_pixels"] == 0
    assert not list(tmp_path.glob(".*.png"))


def test_manual_repair_and_missing_model_never_retry(tmp_path):
    src, output, mask = [tmp_path / name for name in ("src.png", "out.png", "mask.png")]
    Image.new("RGB", (20, 20), "black").save(src)
    Image.new("RGB", (20, 20), "black").save(output)
    Image.new("L", (20, 20), 255).save(mask)
    provider = RetryProvider()
    reports = verify_and_retry(
        provider, src, output, [(mask, {"version": 2, "text_segmentation": {"status": "manual"}})], fake_probability
    )
    assert reports[0]["status"] == "residual" and provider.calls == 0
    reports = verify_and_retry(provider, src, output, [(mask, {})], lambda _: None)
    assert reports[0]["status"] == "unavailable" and provider.calls == 0


def test_residual_check_requires_before_evidence_and_mask():
    after = np.ones((30, 30), np.float32)
    before = np.zeros_like(after)
    before[10:20, 10:20] = 1
    allowed = np.zeros((30, 30), np.uint8)
    allowed[5:15, 5:15] = 255
    result = residual_mask(after, before, allowed)
    assert np.count_nonzero(result) == 25


def test_panel_order_finishes_top_row_before_bottom_row():
    panels = [[10, 10, 180, 150], [210, 10, 180, 150], [10, 190, 180, 150], [210, 190, 180, 150]]
    regions = [
        {"bbox": [45, 50, 50, 70]},
        {"bbox": [245, 50, 50, 70]},
        {"bbox": [45, 225, 50, 70]},
        {"bbox": [245, 225, 50, 70]},
    ]
    order, assignment = panel_reading_order(regions, panels)
    assert order == [1, 0, 3, 2]
    assert len(assignment) == 4


def test_panel_detection_and_uncertain_page_fallback():
    image = np.full((400, 400, 3), 255, np.uint8)
    for box in [(10, 10, 190, 160), (210, 10, 390, 160), (10, 200, 390, 390)]:
        cv2.rectangle(image, box[:2], box[2:], (0, 0, 0), 3)
    assert len(detect_panel_frames(image)) == 3
    assert detect_panel_frames(np.full_like(image, 255)) == []
    regions = [{"bbox": [10, 10, 30, 50]}, {"bbox": [100, 10, 30, 50]}]
    assert panel_reading_order(regions, [[0, 0, 50, 80]]) == ([1, 0], {})


@pytest.mark.parametrize("kind", ["ellipse", "concave", "slanted"])
def test_layout_box_contains_only_safe_shape_pixels(kind):
    mask = np.zeros((200, 180), np.uint8)
    if kind == "ellipse":
        cv2.ellipse(mask, (90, 100), (75, 95), 0, 0, 360, 255, -1)
    elif kind == "concave":
        cv2.fillPoly(mask, [np.array([[10, 10], [150, 10], [150, 60], [70, 60], [70, 180], [10, 180]])], 255)
    else:
        cv2.fillPoly(mask, [np.array([[60, 10], [150, 20], [130, 185], [10, 160]])], 255)
    box = inscribed_text_box(mask, [0, 0, 180, 200], margin=3)
    assert box is not None
    x, y, w, h = map(int, box)
    safe = cv2.erode(mask, np.ones((7, 7), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0)
    assert np.all(safe[y : y + h, x : x + w] > 0)
    assert w * h > 1000


def test_empty_shape_has_no_safe_layout_box():
    assert inscribed_text_box(np.zeros((100, 100), np.uint8), [0, 0, 100, 100]) is None


@pytest.mark.parametrize("protocol", ["openai", "responses", "anthropic"])
def test_optional_visual_translation_context_in_all_protocols(protocol, tmp_path):
    source = tmp_path / "page.png"
    Image.new("RGB", (1800, 2000), "white").save(source)
    data = page_image_data_url(source)
    _, _, payload = OpenAICompatibleTranslator._build_request(
        protocol=protocol,
        base_url="https://example.test/v1",
        api_key="",
        model="vision",
        system_prompt="translate",
        user_prompt="request",
        expected_ids={"R001"},
        temperature=0.2,
        max_tokens=100,
    )
    attach_page_image(payload, protocol, data)
    content = payload["input"][0]["content"] if protocol == "responses" else payload["messages"][-1]["content"]
    assert len(content) == 2
    assert content[0]["text"] == "request"


def test_translation_context_is_ordered_and_bounded():
    def region(order):
        return SimpleNamespace(
            region_key=f"R{order}",
            source_text="日" * 200,
            translated_text="中" * 200,
            reading_order=order,
            panel_id="p1",
            bubble_id="b1",
            bbox=[0, 0, 20, 20],
            orientation="vertical",
        )

    items = page_context([region(2), region(1)], character_budget=500)
    assert items[0]["id"] == "R1"
    assert sum(len(r["source"]) + len(r["translation"]) for r in items) == 500
    assert items[0]["panel_id"] == "p1"


def test_ocr_hints_do_not_rewrite_normal_repetitions():
    assert ocr_review_hints("哈哈哈哈……！！！") == []
    assert ocr_review_hints("啊" * 20) == ["ocr_repetition"]
    assert ocr_review_hints("文字\ufffd") == ["ocr_invalid_characters"]


def test_edited_automatic_mask_is_not_retried(tmp_path):
    src, output, path = [tmp_path / name for name in ("src.png", "out.png", "mask.png")]
    Image.new("RGB", (30, 30), "black").save(src)
    Image.new("RGB", (30, 30), "black").save(output)
    mask = np.full((30, 30), 255, np.uint8)
    fingerprint = hashlib.sha256(mask.tobytes()).hexdigest()
    mask[:5] = 0
    cv2.imwrite(str(path), mask)
    provider = RetryProvider()
    report = verify_and_retry(
        provider, src, output, [(path, {"version": 2, "automatic_mask_sha256": fingerprint})], fake_probability
    )
    assert provider.calls == 0
    assert report[0]["retry"] == "disabled"


def test_segmentation_cache_is_content_bound_and_invalid_cache_is_rebuilt(tmp_path, monkeypatch):
    from app.services.inpainting import text_segmentation as module

    model = tmp_path / module.MODEL_FILENAME
    model.parent.mkdir()
    model.write_bytes(b"mock model")
    source = tmp_path / "source.png"
    Image.new("RGB", (40, 30), "white").save(source)
    calls = []

    def infer(image):
        calls.append(image.shape)
        return np.full(image.shape[:2], 0.7, np.float32)

    monkeypatch.setattr(module, "_segmenter", lambda *args: SimpleNamespace(predict=infer))
    cache = tmp_path / "cache"
    first, metadata = text_probability(source, tmp_path, cache)
    second, metadata = text_probability(source, tmp_path, cache)
    assert metadata["cached"] and len(calls) == 1
    assert np.allclose(first, second, atol=1 / 65535)
    next(cache.glob("*.png")).write_bytes(b"interrupted old cache")
    text_probability(source, tmp_path, cache)
    assert len(calls) == 2
    Image.new("RGB", (40, 30), "black").save(source)
    text_probability(source, tmp_path, cache)
    assert len(calls) == 3
    assert not list(cache.glob(".*.png"))


def test_retry_runtime_failure_preserves_initial_and_cancellation_propagates(tmp_path):
    from app.pipeline.processor import TaskCancelled

    src, output, path = [tmp_path / name for name in ("src.png", "out.png", "mask.png")]
    Image.new("RGB", (30, 30), "black").save(src)
    Image.new("RGB", (30, 30), "black").save(output)
    mask = np.full((30, 30), 255, np.uint8)
    cv2.imwrite(str(path), mask)
    metadata = {"version": 2, "automatic_mask_sha256": hashlib.sha256(mask.tobytes()).hexdigest()}

    def failure(*args):
        raise RuntimeError("GPU unavailable")

    reports = verify_and_retry(SimpleNamespace(inpaint=failure), src, output, [(path, metadata)], fake_probability)
    assert reports[0]["retry"] == "failed"
    assert np.asarray(Image.open(output)).max() == 0

    def cancel():
        raise TaskCancelled("cancelled")

    with pytest.raises(TaskCancelled):
        verify_and_retry(RetryProvider(), src, output, [(path, metadata)], fake_probability, check_interruption=cancel)


def test_overlapping_manual_mask_is_protected_from_automatic_retry(tmp_path):
    src, output, path, manual_path = [tmp_path / name for name in ("src.png", "out.png", "mask.png", "manual.png")]
    Image.new("RGB", (30, 30), "black").save(src)
    Image.new("RGB", (30, 30), "black").save(output)
    mask = np.full((30, 30), 255, np.uint8)
    manual = np.zeros_like(mask)
    manual[:, :15] = 255
    cv2.imwrite(str(path), mask)
    cv2.imwrite(str(manual_path), manual)
    metadata = {"version": 2, "automatic_mask_sha256": hashlib.sha256(mask.tobytes()).hexdigest()}
    reports = verify_and_retry(RetryProvider(), src, output, [(path, metadata), (manual_path, {})], fake_probability)
    pixels = np.asarray(Image.open(output))
    assert pixels[:, :15].max() == 0
    assert pixels[:, 15:].min() == 255
    assert reports[1]["retry"] == "disabled"


def test_atomic_retry_publish_failure_keeps_first_result(tmp_path, monkeypatch):
    src, output, path = [tmp_path / name for name in ("src.png", "out.png", "mask.png")]
    Image.new("RGB", (30, 30), "black").save(src)
    Image.new("RGB", (30, 30), "black").save(output)
    mask = np.full((30, 30), 255, np.uint8)
    cv2.imwrite(str(path), mask)
    metadata = {"version": 2, "automatic_mask_sha256": hashlib.sha256(mask.tobytes()).hexdigest()}
    def fail_replace(*args):
        raise OSError("cannot publish")
    monkeypatch.setattr(Path, "replace", fail_replace)
    reports = verify_and_retry(RetryProvider(), src, output, [(path, metadata)], fake_probability)
    assert reports[0]["retry"] == "failed"
    assert np.asarray(Image.open(output)).max() == 0
