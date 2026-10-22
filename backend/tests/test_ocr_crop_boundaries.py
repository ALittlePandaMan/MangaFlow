from __future__ import annotations

import base64
import io
import json
import math
from types import SimpleNamespace

import cv2
import httpx
import numpy as np
import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.schemas.domain import RegionCreate
from app.services.base import OCRResult, ProviderState
from app.services.cloud_results import CloudResultCache, current_cloud_results
from app.services.ocr import cloud
from app.services.ocr.checkpoint import recognize_local
from app.services.ocr.concurrency import OCRJob, recognize_regions
from app.services.ocr.crops import OCR_FRAME_KEY, _inside_mask, carry_source_frame, crop_region, source_crop_angle
from app.services.ocr.local_worker import validated_crop
from app.services.ocr.prose import native_boxes
from app.services.ocr.providers import MangaOCRProvider
from app.utils.geometry import bbox_to_polygon, polygon_to_bbox
from PIL import Image
from test_cloud_ocr import CONFIG, response_body


def fractional_source():
    ys, xs = np.mgrid[:80, :80].astype(float) + .5
    inside = (xs >= 10.8) & (xs < 61.2) & (ys >= 10.8) & (ys < 61.2)
    pixels = np.full((80, 80, 3), (255, 0, 0), dtype=np.uint8)
    pixels[inside] = (0, 150, 0)
    return Image.fromarray(pixels)


def assert_no_red(image):
    pixels = np.asarray(image).astype(np.int16)
    assert (pixels[:, :, 1] > pixels[:, :, 0] + 20).any()
    assert not (pixels[:, :, 0] > pixels[:, :, 1] + 20).any()


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_fractional_native_chunks_stay_inside_the_parent_frame(tmp_path, orientation):
    source = tmp_path / "fractional.png"
    fractional_source().save(source)
    bbox = [10.8, 10.8, 50.4, 50.4]
    lines = [[10.8, 10.8, 50.4, 15.2], [10.8, 40, 50.4, 15.2]] if orientation == "horizontal" else [
        [10.8, 10.8, 15.2, 50.4], [40, 10.8, 15.2, 50.4]]
    region = SimpleNamespace(bbox=bbox, polygon=bbox_to_polygon(bbox), layout_data={"detection": {
        "ocr_frame_bbox": bbox, "long_text_partition": {"direction": orientation, "ocr_boxes": lines}}})
    boxes = native_boxes(region, orientation, 4)
    assert len(boxes) == 2
    for x, y, width, height in boxes:
        assert x >= bbox[0] and y >= bbox[1]
        assert x + width <= bbox[0] + bbox[2] + 1e-8
        assert y + height <= bbox[1] + bbox[3] + 1e-8
        box = [x, y, width, height]
        assert_no_red(crop_region(source, box, 0, polygon=bbox_to_polygon(box), orientation=orientation, crop_angle=0))


def test_pipeline_real_manga_crops_exclude_fractional_parent_edges(client, monkeypatch):
    project = client.post("/api/projects", json={"name": "fractional-crop"}).json()
    buffer = io.BytesIO()
    fractional_source().save(buffer, format="PNG")
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("page.png", buffer.getvalue(), "image/png")}).json()[0]
    bbox = [10.8, 10.8, 50.4, 50.4]
    region = client.post(f"/api/images/{page['id']}/regions", json={"bbox": bbox, "orientation": "horizontal"}).json()
    with SessionLocal() as db:
        row = db.get(TextRegion, region["id"])
        row.layout_data = {**row.layout_data, "detection": {"ocr_frame_bbox": bbox,
            "long_text_partition": {"direction": "horizontal", "ocr_boxes": [
                [10.8, 10.8, 50.4, 15.2], [10.8, 40, 50.4, 15.2]]}}}
        db.commit()
    provider = MangaOCRProvider()
    provider.state = ProviderState.READY
    monkeypatch.setattr(provider, "batch_size", lambda: 2)

    def infer(images):
        assert len(images) == 2
        for image in images:
            assert_no_red(image)
        return ["one", "two"]

    monkeypatch.setattr(provider, "_recognize_crops", infer)
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda *_: (provider, "manga-ocr"))
    with SessionLocal() as db:
        PipelineProcessor(db).ocr(db.get(ImagePage, page["id"]), None, force=True, region_id=region["id"], options={"crop_padding": 4})
    after = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert after["source_text"] == "onetwo"
    assert after["bbox"] == bbox and after["polygon"] == bbox_to_polygon(bbox)


def rotated_source(tmp_path, degrees, orientation):
    width, height = (120, 24) if orientation == "horizontal" else (24, 120)
    angle = math.radians(degrees)
    rotation = np.asarray([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    quad = (np.asarray([[-width / 2, -height / 2], [width / 2, -height / 2],
                        [width / 2, height / 2], [-width / 2, height / 2]]) @ rotation.T + 120).tolist()
    tile = np.full((height, width, 3), 255, dtype=np.uint8)
    if orientation == "horizontal":
        tile[:, :width // 3] = (0, 150, 0)
        tile[:, width * 2 // 3:] = (0, 0, 200)
    else:
        tile[:height // 3] = (0, 150, 0)
        tile[height * 2 // 3:] = (0, 0, 200)
    corners = np.asarray([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32) - .5
    matrix = cv2.getPerspectiveTransform(corners, np.asarray(quad, dtype=np.float32) - .5)
    pixels = cv2.warpPerspective(tile, matrix, (240, 240), borderValue=(255, 255, 255))
    source = tmp_path / "rotated.png"
    Image.fromarray(pixels).save(source)
    return source, quad


def assert_reading_axes(image, orientation):
    pixels = np.asarray(image).astype(float)
    if orientation == "horizontal":
        assert image.width > image.height * 4
        beginning, ending = pixels[:, :image.width // 4], pixels[:, -image.width // 4:]
    else:
        assert image.height > image.width * 4
        beginning, ending = pixels[:image.height // 4], pixels[-image.height // 4:]
    assert beginning[:, :, 1].mean() > beginning[:, :, 2].mean() + 40
    assert ending[:, :, 2].mean() > ending[:, :, 1].mean() + 40


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
@pytest.mark.parametrize("degrees", [46, 60, 80, -46, -60, -80])
@pytest.mark.parametrize("start,reverse", [(0, False), (1, False), (2, True), (3, True)])
def test_steep_reading_direction_does_not_swap_or_flip_axes(tmp_path, orientation, degrees, start, reverse):
    source, polygon = rotated_source(tmp_path, degrees, orientation)
    polygon = polygon[start:] + polygon[:start]
    if reverse:
        polygon = polygon[::-1]
    assert_reading_axes(crop_region(source, polygon_to_bbox(polygon), 0, polygon=polygon, orientation=orientation), orientation)


@pytest.mark.parametrize("degrees", [0, 30, 60, 90, -60])
def test_known_angle_preserves_a_tall_horizontal_paragraph(tmp_path, degrees):
    source, polygon = rotated_source(tmp_path, degrees, "vertical")
    # A horizontally written paragraph may have a tall overall bounding frame.
    image = crop_region(source, polygon_to_bbox(polygon), 0, polygon=polygon, orientation="horizontal", crop_angle=degrees)
    assert_reading_axes(image, "vertical")


@pytest.mark.parametrize("protocol", ["openai", "responses", "anthropic"])
def test_cloud_payload_and_native_worker_apply_reading_orientation(tmp_path, monkeypatch, protocol):
    source, polygon = rotated_source(tmp_path, 60, "horizontal")
    bbox = polygon_to_bbox(polygon)
    captured = []

    def handler(request):
        body = json.loads(request.content)
        if protocol == "anthropic":
            encoded = body["messages"][0]["content"][0]["source"]["data"]
        elif protocol == "responses":
            encoded = body["input"][0]["content"][0]["image_url"].split(",")[1]
        else:
            encoded = body["messages"][1]["content"][0]["image_url"]["url"].split(",")[1]
        image = Image.open(io.BytesIO(base64.b64decode(encoded)))
        assert_reading_axes(image, "horizontal")
        captured.append(image.size)
        return httpx.Response(200, json=response_body(protocol))

    client = httpx.Client
    monkeypatch.setattr(cloud.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    cloud.CloudMultimodalOCRProvider({**CONFIG, "api_protocol": protocol}).recognize_once(
        source, bbox, "horizontal", 0, polygon=polygon)
    image, _ = validated_crop({"image_path": str(source), "bbox": bbox, "polygon": polygon, "padding": 0, "orientation": "horizontal"})
    assert_reading_axes(image, "horizontal")
    assert captured == [image.size]


@pytest.mark.parametrize("manual_edit", [False, True])
def test_trusted_source_angle_reaches_the_actual_pipeline_cloud_upload(client, tmp_path, monkeypatch, manual_edit):
    source, polygon = rotated_source(tmp_path, 60, "vertical")
    project = client.post("/api/projects", json={"name": "angle-crop"}).json()
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("angle.png", source.read_bytes(), "image/png")}).json()[0]
    initial_polygon = bbox_to_polygon([108, 60, 24, 120]) if manual_edit else polygon
    region = client.post(f"/api/images/{page['id']}/regions", json={"bbox": polygon_to_bbox(initial_polygon), "polygon": initial_polygon,
                                                              "orientation": "horizontal"}).json()
    if manual_edit:
        response = client.patch(f"/api/regions/{region['id']}", json={"polygon": polygon})
        assert response.status_code == 200, response.text
        assert response.json()["layout_data"][OCR_FRAME_KEY]["angle"] == pytest.approx(60)
    else:
        with SessionLocal() as db:
            db.get(TextRegion, region["id"]).layout_data = {"detection": {"text_angle": 60}}
            db.commit()
    uploaded = []

    def handler(request):
        body = json.loads(request.content)
        encoded = body["messages"][1]["content"][0]["image_url"]["url"].split(",")[1]
        image = Image.open(io.BytesIO(base64.b64decode(encoded)))
        # Horizontal prose in a tall frame must remain tall, not become a
        # sideways single line just because the frame's long edge is vertical.
        assert_reading_axes(image, "vertical")
        uploaded.append(image.size)
        return httpx.Response(200, json=response_body("openai", "paragraph"))

    transport_client = httpx.Client
    monkeypatch.setattr(cloud.httpx, "Client", lambda **kwargs: transport_client(transport=httpx.MockTransport(handler), **kwargs))
    model = cloud.CloudMultimodalOCRProvider(CONFIG)
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda *_: (model, "cloud-test"))
    with SessionLocal() as db:
        PipelineProcessor(db).ocr(db.get(ImagePage, page["id"]), None, force=True, region_id=region["id"], options={"crop_padding": 0})
    assert len(uploaded) == 1


def test_native_batch_oom_retry_preserves_detector_angle_hints(tmp_path, monkeypatch):
    source, polygon = rotated_source(tmp_path, 60, "vertical")
    model = MangaOCRProvider()
    model.state = ProviderState.READY
    monkeypatch.setattr(model, "batch_size", lambda: model._batch_ceiling)
    calls = []

    def infer(images):
        for image in images:
            assert_reading_axes(image, "vertical")
        calls.append(len(images))
        if len(images) > 2:
            raise RuntimeError("CUDA out of memory")
        return ["paragraph"] * len(images)

    monkeypatch.setattr(model, "_recognize_crops", infer)
    rows = model.recognize_batch(source, [(polygon_to_bbox(polygon), "horizontal")] * 4, 0,
        polygons=[polygon] * 4, crop_angles=[60] * 4)
    assert [row.text for row in rows] == ["paragraph"] * 4
    assert calls == [4, 2, 2]


@pytest.mark.parametrize("kind", ["native", "cloud"])
def test_crop_angle_change_invalidates_request_cache_without_geometry_change(tmp_path, kind):
    calls = []

    def infer(source, box, orientation, padding, *, polygon, crop_angle):
        calls.append(crop_angle)
        return OCRResult(str(crop_angle), .9, orientation)

    model = SimpleNamespace(config={"max_concurrency": 1, "retries": 0}, recognize=infer, recognize_once=infer)
    box = [10, 10, 100, 20]
    polygon = bbox_to_polygon(box)
    token = current_cloud_results.set(CloudResultCache(tmp_path, tmp_path / "results"))
    try:
        def run(angle):
            if kind == "native":
                return recognize_local(model, None, [(box, "horizontal")], 4, polygons=[polygon], crop_angles=[angle])[0].text
            jobs = [OCRJob("region", tuple(box), "horizontal", tuple(tuple(point) for point in polygon), angle)]
            return list(recognize_regions(model, None, jobs, 4, lambda: None, lambda _: None))[0][1].text

        assert run(30) == run(30) == "30"
        assert run(60) == "60"
        assert calls == [30, 60]
    finally:
        current_cloud_results.reset(token)


@pytest.mark.parametrize("count", [1001, 5000])
def test_long_lasso_is_accepted_without_simplifying_its_outline(tmp_path, count):
    polygon = [[80 + 60 * math.cos(2 * math.pi * index / count), 80 + 60 * math.sin(2 * math.pi * index / count)] for index in range(count)]
    model = RegionCreate(bbox=polygon_to_bbox(polygon), polygon=polygon)
    source = tmp_path / "long.png"
    Image.new("RGB", (160, 160), "black").save(source)
    image = crop_region(source, model.bbox, 4, polygon=model.polygon)
    pixels = np.asarray(image)
    assert image.size == (128, 128)
    assert (pixels == 0).all(axis=2).any() and (pixels == 255).all(axis=2).any()
    assert image.getpixel((5, 5)) == (255, 255, 255)
    assert image.getpixel((64, 64)) == (0, 0, 0)
    assert model.polygon == polygon


@pytest.mark.parametrize("polygon", [
    [[-10000, 10], [40, 10], [40, 70], [-10000, 70]],
    [[10, -10000], [70, -10000], [70, 40], [10, 40]],
    [[-10000, -10000], [40, -10000], [40, 40], [-10000, 40]],
])
def test_partially_visible_quad_keeps_native_pixels_not_an_empty_image(tmp_path, polygon):
    source = tmp_path / "offpage.png"
    Image.new("RGB", (80, 80), "black").save(source)
    image = crop_region(source, polygon_to_bbox(polygon), 4, polygon=polygon, orientation="horizontal")
    pixels = np.asarray(image)
    assert np.all(pixels[4:-4, 4:-4] == 0)
    assert image.width <= 88 and image.height <= 88


@pytest.mark.parametrize("polygon", [
    [[2.2, 1.8], [68.3, 4.8], [20.8, 22.2], [3.6, 70.4]],
    [[10, 10], [70, 70], [70, 10], [10, 70]],
    [[-15, 20], [25, -15], [70, 25], [25, 70]],
    [[40 + 30 * math.cos(index * 2 * math.pi / 1001), 40 + 30 * math.sin(index * 2 * math.pi / 1001)] for index in range(1001)],
])
def test_scanline_mask_matches_the_original_exact_pixel_centre_rule(polygon):
    vertices = np.asarray(polygon, dtype=float)
    ys, xs = np.mgrid[:80, :80].astype(float) + .5
    expected = np.zeros((80, 80), dtype=bool)
    for a, b in zip(vertices, np.roll(vertices, -1, axis=0), strict=True):
        if a[1] != b[1]:
            expected ^= ((a[1] > ys) != (b[1] > ys)) & (xs < a[0] + (ys - a[1]) * (b[0] - a[0]) / (b[1] - a[1]))
    assert np.array_equal(_inside_mask(vertices, 80, 80), expected)


def test_detector_angle_is_not_reused_after_manual_geometry_edit():
    region = SimpleNamespace(layout_data={"detection": {"text_angle": 30}})
    assert source_crop_angle(region) == 30
    region.layout_data["manual"] = True
    assert source_crop_angle(region) is None


@pytest.mark.parametrize("degrees", [30, 60, 90, -60])
def test_manual_multiline_frame_keeps_its_axes_through_repeated_edits(tmp_path, degrees):
    source, polygon = rotated_source(tmp_path, degrees, "vertical")
    region = SimpleNamespace(polygon=bbox_to_polygon([108, 60, 24, 120]), orientation="horizontal", layout_data={"manual": True})
    region.layout_data[OCR_FRAME_KEY] = carry_source_frame(region, polygon)
    region.polygon = polygon
    assert source_crop_angle(region) == pytest.approx(degrees)
    image = crop_region(source, polygon_to_bbox(polygon), 0, polygon=polygon, orientation="horizontal", crop_angle=source_crop_angle(region))
    assert_reading_axes(image, "vertical")
    shifted = [[x + 3, y + 4] for x, y in polygon]
    region.layout_data[OCR_FRAME_KEY] = carry_source_frame(region, shifted)
    region.polygon = shifted
    assert source_crop_angle(region) == pytest.approx(degrees)
    # A later geometry rewrite that bypasses the edit service invalidates it.
    region.polygon = bbox_to_polygon([10, 10, 20, 100])
    assert source_crop_angle(region) is None


def test_vertex_order_change_does_not_rotate_a_remembered_source_frame():
    polygon = bbox_to_polygon([10, 10, 20, 100])
    region = SimpleNamespace(polygon=polygon, orientation="horizontal", layout_data={"manual": True})
    reordered = polygon[2:] + polygon[:2]
    assert carry_source_frame(region, reordered)["angle"] == pytest.approx(0)
