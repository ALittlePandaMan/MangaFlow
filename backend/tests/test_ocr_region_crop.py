from __future__ import annotations

import base64
import io
import json
import math
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import cv2
import httpx
import numpy as np
import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services.base import OCRResult, ProviderError, ProviderState
from app.services.cloud_results import CloudResultCache, current_cloud_results
from app.services.ocr.checkpoint import recognize_local
from app.services.ocr.cloud import CloudMultimodalOCRProvider
from app.services.ocr.concurrency import OCRJob, recognize_regions
from app.services.ocr.crops import crop_region
from app.services.ocr.generic import GenericCloudOCRProvider
from app.services.ocr.local import LocalOCRWorker
from app.services.ocr.local_worker import validated_crop
from app.services.ocr.providers import MangaOCRProvider
from app.services.workflow_state import fingerprint
from app.utils.geometry import polygon_to_bbox
from PIL import Image, ImageDraw
from test_translation_auto_ocr import setup

POLYGON = [[20, 100], [140, 40], [150, 60], [30, 120]]
BOX = polygon_to_bbox(POLYGON)
CONCAVE = [[10, 10], [70, 10], [30, 30], [10, 70]]


@pytest.fixture(params=[
    CONCAVE,
    [[10, 10], [70, 70], [70, 10], [10, 70]],  # Crossed, zero signed area.
    [[10, 10], [70, 10], [70, 11.25], [10, 11.25]],  # Too narrow to rectify safely.
    [[10, 10], [70, 10], [10, 70], [10, 70]],  # Coincident handles still enclose a triangle.
], ids=["concave", "crossed", "narrow", "coincident"])
def irregular_source(tmp_path, request):
    polygon = deepcopy(request.param)
    path = tmp_path / "irregular.png"
    pixels = np.full((80, 80, 3), (255, 0, 0), dtype=np.uint8)
    contour = np.asarray(polygon, dtype=np.float32)
    for y in range(80):
        for x in range(80):
            if cv2.pointPolygonTest(contour, (x + .5, y + .5), False) >= 0:
                pixels[y, x] = (0, 150, 0)
    Image.fromarray(pixels).save(path)
    return path, polygon_to_bbox(polygon), polygon


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "selection.png"
    # Independent analytic half-planes at pixel centres; integer polygon drawing
    # has different edge rounding and would paint some selected pixels red.
    ys, xs = np.mgrid[:160, :180].astype(float) + .5
    inside = ((ys + .5 * xs >= 110) & (ys + .5 * xs < 135)
              & (xs - .5 * ys >= -30) & (xs - .5 * ys < 120))
    pixels = np.full((160, 180, 3), (255, 0, 0), dtype=np.uint8)
    pixels[inside] = (0, 150, 0)
    Image.fromarray(pixels).save(path)
    return path


def assert_selection_only(image):
    pixels = np.asarray(image).astype(np.int16)
    assert (pixels[:, :, 1] > pixels[:, :, 0] + 20).any(), "selected pixels were lost"
    assert not (pixels[:, :, 0] > pixels[:, :, 1] + 20).any(), "outside pixels leaked into OCR"


@pytest.mark.parametrize("padding", [0, 4, 50])
@pytest.mark.parametrize("vertices", [POLYGON, POLYGON[2:] + POLYGON[:2], POLYGON[::-1]])
def test_slanted_frame_is_rectified_without_neighbours(source, padding, vertices):
    crop = crop_region(source, BOX, padding, polygon=vertices)
    assert crop.size == (135 + padding * 2, 23 + padding * 2)
    assert_selection_only(crop)
    if padding:
        assert np.all(np.asarray(crop)[:padding] == 255)
        assert np.all(np.asarray(crop)[:, :padding] == 255)


def test_rectangular_legacy_frames_get_white_not_image_padding(tmp_path):
    path = tmp_path / "rect.png"
    image = Image.new("RGB", (80, 70), "red")
    ImageDraw.Draw(image).rectangle((10, 10, 39, 39), fill=(0, 150, 0))
    image.save(path)
    for polygon in (None, [], [[10, 10], [40, 10], [40, 40], [10, 40]]):
        crop = crop_region(path, [10, 10, 30, 30], 8, polygon=polygon)
        assert crop.size == (46, 46)
        assert_selection_only(crop)
        assert np.all(np.asarray(crop)[:8] == 255)


def test_concave_polygon_masks_its_notch_and_partial_off_page_frame_is_safe(tmp_path):
    path = tmp_path / "concave.png"
    polygon = [[-10, 10], [70, 10], [70, 30], [30, 30], [30, 70], [-10, 70]]
    image = Image.new("RGB", (80, 80), "red")
    ImageDraw.Draw(image).polygon(polygon, fill=(0, 150, 0))
    image.save(path)
    assert_selection_only(crop_region(path, polygon_to_bbox(polygon), 4, polygon=polygon))


@pytest.mark.parametrize("polygon", [
    [[0, 0], [10, 10]], [[0, 0], [10, 10], [20, 20]],
    [[0, 0], [10, 10], [20, 20], [30, 30]], [[0, 0], [10, 0], [10, float("nan")]],
    [[0, 0], [10, 0], [True, 10]], "invalid",
])
def test_invalid_polygon_never_falls_back_to_larger_bbox(polygon):
    with pytest.raises(ProviderError, match="轮廓无效"):
        crop_region(Path("must-not-be-read"), [0, 0, 100, 100], polygon=polygon)


def test_off_page_selection_is_rejected(source):
    with pytest.raises(ProviderError, match="图片范围"):
        crop_region(source, [200, 200, 10, 10])


@pytest.mark.parametrize("polygon", [
    [[25.25, 30.5], [145.75, 45.25], [125.5, 105.75], [35.5, 90.25]],
    [[45.25, 10.5], [70.75, 20.25], [35.5, 145.75], [10.5, 135.25]],
])
def test_fractional_perspective_and_vertical_frames_do_not_leak_pixels(tmp_path, polygon):
    path = tmp_path / "perspective.png"
    pixels = np.full((160, 180, 3), (255, 0, 0), dtype=np.uint8)
    contour = np.asarray(polygon, dtype=np.float32)
    for y in range(160):
        for x in range(180):
            if cv2.pointPolygonTest(contour, (x + .5, y + .5), False) >= 0:
                pixels[y, x] = (0, 150, 0)
    Image.fromarray(pixels).save(path)
    crop = crop_region(path, polygon_to_bbox(polygon), 4, polygon=polygon)
    assert_selection_only(crop)
    if polygon[0][1] < 20:
        assert crop.height > crop.width  # Do not turn vertical text sideways.


@pytest.mark.parametrize("start", range(4))
@pytest.mark.parametrize("reverse", [False, True])
def test_non_rectifiable_quads_keep_the_exact_outline_without_failing(irregular_source, start, reverse):
    source, box, polygon = irregular_source
    polygon = polygon[start:] + polygon[:start]
    if reverse:
        polygon = polygon[::-1]
    original = deepcopy(polygon)
    crop = crop_region(source, box, 4, polygon=polygon)
    assert crop.size == (math.ceil(box[0] + box[2]) - math.floor(box[0]) + 8,
                         math.ceil(box[1] + box[3]) - math.floor(box[1]) + 8)
    assert_selection_only(crop)
    assert polygon == original
    assert np.all(np.asarray(crop)[:4] == 255)
    assert np.all(np.asarray(crop)[:, :4] == 255)


def test_concave_notch_is_white_not_a_convex_hull(tmp_path):
    path = tmp_path / "notch.png"
    Image.new("RGB", (80, 80), "black").save(path)
    crop = crop_region(path, polygon_to_bbox(CONCAVE), 4, polygon=CONCAVE)
    assert crop.getpixel((45 - 10 + 4, 40 - 10 + 4)) == (255, 255, 255)
    assert crop.getpixel((15 - 10 + 4, 15 - 10 + 4)) == (0, 0, 0)


@pytest.mark.parametrize("protocol", ["openai", "responses", "anthropic"])
def test_actual_cloud_request_contains_only_polygon_pixels(source, monkeypatch, protocol):
    from app.services.ocr import cloud
    from test_cloud_ocr import CONFIG, response_body

    uploads = []

    def handler(request):
        body = json.loads(request.content)
        if protocol == "anthropic":
            encoded = body["messages"][0]["content"][0]["source"]["data"]
        elif protocol == "responses":
            encoded = body["input"][0]["content"][0]["image_url"].split(",")[1]
        else:
            encoded = body["messages"][1]["content"][0]["image_url"]["url"].split(",")[1]
        uploads.append(Image.open(io.BytesIO(base64.b64decode(encoded))))
        return httpx.Response(200, json=response_body(protocol, "subject only"))

    client = httpx.Client
    monkeypatch.setattr(cloud.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    provider = CloudMultimodalOCRProvider({**CONFIG, "api_protocol": protocol})
    assert provider.recognize_once(source, BOX, "horizontal", polygon=POLYGON).text == "subject only"
    assert len(uploads) == 1
    assert_selection_only(uploads[0])


@pytest.mark.parametrize("protocol", ["openai", "responses", "anthropic"])
def test_cloud_request_accepts_non_rectifiable_quads(irregular_source, monkeypatch, protocol):
    from app.services.ocr import cloud
    from test_cloud_ocr import CONFIG, response_body

    source, box, polygon = irregular_source
    uploads = []

    def handler(request):
        body = json.loads(request.content)
        if protocol == "anthropic":
            encoded = body["messages"][0]["content"][0]["source"]["data"]
        elif protocol == "responses":
            encoded = body["input"][0]["content"][0]["image_url"].split(",")[1]
        else:
            encoded = body["messages"][1]["content"][0]["image_url"]["url"].split(",")[1]
        image = Image.open(io.BytesIO(base64.b64decode(encoded)))
        assert_selection_only(image)
        uploads.append(image.size)
        return httpx.Response(200, json=response_body(protocol, "selected text"))

    client = httpx.Client
    monkeypatch.setattr(cloud.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    provider = CloudMultimodalOCRProvider({**CONFIG, "api_protocol": protocol})
    assert provider.recognize_once(source, box, "horizontal", polygon=polygon).text == "selected text"
    assert len(uploads) == 1


def test_generic_cloud_request_uses_the_same_polygon_crop(source, monkeypatch):
    from app.services.ocr import generic

    def handler(request):
        image = Image.open(io.BytesIO(base64.b64decode(json.loads(request.content)["image"])))
        assert_selection_only(image)
        return httpx.Response(200, json={"text": "subject only"})

    client = httpx.Client
    monkeypatch.setattr(generic.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    provider = GenericCloudOCRProvider({"base_url": "https://ocr.invalid/read", "auth_mode": "none", "retries": 0})
    assert provider.recognize_once(source, BOX, "horizontal", polygon=POLYGON).text == "subject only"


def test_manga_ocr_microbatches_keep_polygon_crops_after_oom(source, monkeypatch):
    provider = MangaOCRProvider()
    provider.state = ProviderState.READY
    provider._device = "cuda:0"
    sizes = []

    def infer(crops):
        for crop in crops:
            assert_selection_only(crop)
        sizes.append(len(crops))
        if len(crops) > 2:
            raise RuntimeError("CUDA out of memory")
        return ["subject only"] * len(crops)

    monkeypatch.setattr(provider, "batch_size", lambda: provider._batch_ceiling)
    monkeypatch.setattr(provider, "_recognize_crops", infer)
    results = provider.recognize_batch(source, [(BOX, "horizontal")] * 4, polygons=[POLYGON] * 4)
    assert [row.text for row in results] == ["subject only"] * 4
    assert sizes == [4, 2, 2]


def test_native_ocr_and_isolated_worker_accept_non_rectifiable_quads(irregular_source, monkeypatch):
    source, box, polygon = irregular_source
    image, _ = validated_crop({"image_path": str(source), "bbox": box, "polygon": polygon, "padding": 4})
    assert_selection_only(image)
    provider = MangaOCRProvider()
    provider.state = ProviderState.READY
    monkeypatch.setattr(provider, "batch_size", lambda: 2)

    def recognize(image):
        assert_selection_only(image)
        return "selected text"

    provider._model = recognize
    monkeypatch.setattr(provider, "_recognize_crops", lambda images: [recognize(image) for image in images])
    assert provider.recognize(source, box, "horizontal", polygon=polygon).text == "selected text"
    results = provider.recognize_batch(source, [(box, "horizontal")] * 2, polygons=[polygon] * 2)
    assert [row.text for row in results] == ["selected text", "selected text"]


def test_isolated_worker_protocol_preserves_polygon(source, monkeypatch):
    worker = LocalOCRWorker("hayai-ocr", "cpu")
    stream = io.StringIO()
    worker.process = SimpleNamespace(poll=lambda: None, stdin=stream)

    def receive(timeout):
        request = json.loads(stream.getvalue())
        assert request["polygon"] == POLYGON
        image, maximum = validated_crop(request)
        assert maximum == 2048
        assert_selection_only(image)
        return {"id": request["id"], "text": "subject only"}

    monkeypatch.setattr(worker, "_receive", receive)
    try:
        assert worker.recognize(source, BOX, "horizontal", 4, 2048, 180, polygon=POLYGON)["text"] == "subject only"
    finally:
        worker.process = None


@pytest.mark.parametrize("kind", ["local", "manga", "cloud"])
def test_pipeline_uses_source_polygon_not_bbox_or_translated_frame(client, monkeypatch, kind):
    page, rows = setup(client, [""])
    with SessionLocal() as db:
        region = db.get(TextRegion, rows[0]["id"])
        region.bbox, region.polygon = BOX, POLYGON
        region.translated_bbox = [0, 0, 500, 500]
        region.translated_polygon = [[0, 0], [500, 0], [500, 500], [0, 500]]
        db.commit()
    seen = []

    def recognize(source, box, orientation, padding, *, polygon):
        seen.append((box, polygon))
        return OCRResult("subject only", .9, orientation)

    if kind == "cloud":
        provider = CloudMultimodalOCRProvider({"base_url": "https://ocr.invalid", "model": "fake", "retries": 0})
        monkeypatch.setattr(provider, "recognize_once", recognize)
    elif kind == "manga":
        provider = MangaOCRProvider()
        monkeypatch.setattr(provider, "batch_size", lambda: 2)
        monkeypatch.setattr(provider, "recognize_batch", lambda source, jobs, padding, **kwargs: [
            recognize(source, box, orientation, padding, polygon=polygon)
            for (box, orientation), polygon in zip(jobs, kwargs["polygons"], strict=True)])
    else:
        provider = SimpleNamespace(recognize=recognize, ensure_loaded=lambda: None)
    monkeypatch.setattr(provider, "ensure_loaded", lambda: None)
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda *_: (provider, "test"))
    with SessionLocal() as db:
        PipelineProcessor(db).ocr(db.get(ImagePage, page["id"]), None, force=True, region_id=rows[0]["id"], options={})
    assert seen == [(BOX, POLYGON)]
    after = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert after["source_text"] == "subject only"
    assert after["polygon"] == POLYGON
    assert after["bbox"] == BOX


@pytest.mark.parametrize("kind", ["local", "cloud"])
def test_retry_cache_is_invalidated_by_polygon_even_with_unchanged_bbox(tmp_path, kind):
    calls = []

    def recognize(source, box, orientation, padding, **kwargs):
        calls.append(kwargs.get("polygon"))
        return OCRResult(str(len(calls)), .9, orientation)

    provider = SimpleNamespace(config={"max_concurrency": 1, "retries": 0}, recognize=recognize, recognize_once=recognize)
    token = current_cloud_results.set(CloudResultCache(tmp_path, tmp_path / "results"))
    alternative = [[20, 100], [140, 40], [145, 50], [25, 110]]
    try:
        def run(polygon):
            if kind == "local":
                return recognize_local(provider, Path("unused"), [(BOX, "horizontal")], 4, polygons=[polygon])[0].text
            jobs = [OCRJob("same-region", tuple(BOX), "horizontal", tuple(tuple(point) for point in polygon))]
            return list(recognize_regions(provider, Path("unused"), jobs, 4, lambda: None, lambda _: None))[0][1].text

        assert run(POLYGON) == run(POLYGON) == "1"
        assert run(alternative) == "2"
        assert calls == [POLYGON, alternative]
    finally:
        current_cloud_results.reset(token)


@pytest.mark.parametrize("kind", ["local", "cloud"])
def test_previous_crop_version_cannot_restore_an_expanded_rectangle(tmp_path, kind):
    provider = SimpleNamespace(config={"max_concurrency": 1, "retries": 0},
        recognize=lambda *args, **kwargs: OCRResult("fresh", .9, "horizontal"),
        recognize_once=lambda *args, **kwargs: OCRResult("fresh", .9, "horizontal"))
    old_key = {"format": 2, "kind": "native-ocr" if kind == "local" else "ocr", "config": provider.config,
               "bbox": BOX, "polygon": POLYGON, "orientation": "horizontal", "padding": 4}
    if kind == "local":
        old_key["provider"] = "SimpleNamespace"
    else:
        old_key["region_id"] = "region"
    cache = CloudResultCache(tmp_path, tmp_path / "results")
    cache.put(fingerprint(old_key), {"text": "old expanded crop", "confidence": .9, "orientation": "horizontal", "metadata": {}})
    token = current_cloud_results.set(cache)
    try:
        if kind == "local":
            result, = recognize_local(provider, Path("unused"), [(BOX, "horizontal")], 4, polygons=[POLYGON])
        else:
            jobs = [OCRJob("region", tuple(BOX), "horizontal", tuple(tuple(point) for point in POLYGON))]
            _, result = next(recognize_regions(provider, Path("unused"), jobs, 4, lambda: None, lambda _: None))
        assert result.text == "fresh"
    finally:
        current_cloud_results.reset(token)
