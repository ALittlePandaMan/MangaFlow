from __future__ import annotations

import threading
import time
from io import BytesIO
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from app.pipeline.processor import PipelineProcessor
from app.services.base import DetectionResult, OCRResult
from app.services.inpainting.providers import SimpleLaMaInpainter
from PIL import Image, ImageChops, ImageDraw


def manga_page() -> BytesIO:
    image = Image.new("RGB", (360, 480), "#d8d8d8")
    draw = ImageDraw.Draw(image)
    draw.ellipse((90, 70, 270, 310), fill="white", outline="black", width=3)
    draw.rectangle((150, 120, 160, 230), fill="black")
    output = BytesIO()
    image.save(output, "PNG")
    output.seek(0)
    return output


def solid_page(width: int = 180, height: int = 100, color: tuple[int, int, int] = (90, 100, 110)) -> BytesIO:
    output = BytesIO()
    Image.new("RGB", (width, height), color).save(output, "PNG")
    output.seek(0)
    return output


@pytest.mark.parametrize("include_image", [False, True])
def test_translation_context_uses_previous_page_in_same_project_and_opt_in_image(client, monkeypatch, include_image):
    from app.services.translation.providers import OpenAICompatibleTranslator

    contexts = []

    class CaptureTranslator(OpenAICompatibleTranslator):
        async def translate_regions(self, regions, **kwargs):
            contexts.append(kwargs["context"])
            return {key: "译文" for key, _ in regions}

    provider = CaptureTranslator({"include_page_image": include_image})
    original = PipelineProcessor._provider

    def use_capture(processor, kind, requested):
        return (provider, "capture") if kind == "translation" else original(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", use_capture)
    project = client.post("/api/projects", json={"name": "context-scope"}).json()
    other = client.post("/api/projects", json={"name": "unrelated"}).json()
    pages = []
    for project_id, name, source in [
        (project["id"], "1.png", "previous page"),
        (other["id"], "secret.png", "unrelated page"),
        (project["id"], "2.png", "current page"),
    ]:
        page = client.post(
            f"/api/projects/{project_id}/images", files={"files": (name, solid_page(), "image/png")}
        ).json()[0]
        region = client.post(
            f"/api/images/{page['id']}/regions", json={"polygon": [[10, 10], [80, 10], [80, 80], [10, 80]]}
        ).json()
        response = client.patch(f"/api/regions/{region['id']}", json={"source_text": source})
        assert response.status_code == 200
        pages.append(page)
    task = client.post(
        f"/api/images/{pages[-1]['id']}/process",
        json={"start_stage": "translation", "end_stage": "translation", "force": True},
    ).json()
    task = wait_for_task(client, task["id"])
    assert task["status"] == "COMPLETED", task
    assert contexts[0]["previous_page"]["page_id"] == pages[0]["id"]
    assert contexts[0]["previous_page"]["regions"][0]["source"] == "previous page"
    assert contexts[0]["page_context"][0]["source"] == "current page"
    assert ("_page_image_data_url" in contexts[0]) == include_image


def rectangular_mask(width: int, height: int, box: tuple[int, int, int, int]) -> BytesIO:
    output = BytesIO()
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rectangle(box, fill=255)
    mask.save(output, "PNG")
    output.seek(0)
    return output


def wait_for_task(client, task_id: str) -> dict:
    task = {}
    for _ in range(300):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.03)
    return task


class RecordingColorInpainter:
    def __init__(self, colors: list[tuple[int, int, int]], *, threshold: int = 8) -> None:
        self.colors = colors
        self.threshold = threshold
        self.calls: list[np.ndarray] = []
        self.fail_after: int | None = None

    def inpaint(self, image_path, mask_path, output_path, _region_type):
        with Image.open(image_path) as source:
            image = np.asarray(source.convert("RGB")).copy()
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        assert mask is not None
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("intentional inpainting failure")
        color = self.colors[len(self.calls)]
        self.calls.append(mask.copy())
        image[mask > self.threshold] = color
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(image).save(output_path, "PNG")
        return output_path


def test_forced_detection_replaces_manual_regions_and_detector_output(client, monkeypatch) -> None:
    class ChangingDetector:
        calls = 0

        def detect(self, _image_path):
            self.calls += 1
            if self.calls == 1:
                return [
                    DetectionResult(
                        polygon=[[20, 30], [70, 30], [70, 130], [20, 130]],
                        bbox=[20, 30, 50, 100],
                        confidence=0.95,
                        bubble_id="bubble-stable",
                        metadata={"pass": self.calls},
                    )
                ]
            return [
                DetectionResult(
                    polygon=[[x, 70], [x + 50, 70], [x + 50, 140], [x, 140]],
                    bbox=[x, 70, 50, 70],
                    confidence=0.95,
                    bubble_id="bubble-stable",
                    metadata={"pass": self.calls},
                    orientation="horizontal" if x == 100 else "vertical",
                )
                for x in [100, 220]
            ]

    detector = ChangingDetector()
    original_provider = PipelineProcessor._provider

    def use_changing_detector(processor, kind, requested):
        if kind == "detection":
            return detector, "changing-detector"
        return original_provider(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", use_changing_detector)
    project = client.post("/api/projects", json={"name": "preserve-manual-region"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("detect.png", manga_page(), "image/png")},
    ).json()[0]

    first_task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "detection", "end_stage": "detection", "force": True},
    ).json()
    first_task = wait_for_task(client, first_task["id"])
    assert first_task["status"] == "COMPLETED", first_task
    first_detected = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert first_detected["layout_data"]["detection"]["pass"] == 1

    manual = client.post(
        f"/api/images/{page['id']}/regions",
        json={
            "polygon": [[90, 60], [165, 55], [170, 170], [85, 175]],
            "reading_order": 2,
        },
    ).json()
    assert manual["layout_data"]["manual"] is True

    second_task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "detection", "end_stage": "detection", "force": True},
    ).json()
    second_task = wait_for_task(client, second_task["id"])
    assert second_task["status"] == "COMPLETED", second_task
    regions = client.get(f"/api/images/{page['id']}/regions").json()

    assert manual["id"] not in {region["id"] for region in regions}
    assert first_detected["id"] not in {region["id"] for region in regions}
    replacement = next(region for region in regions if "detection" in region["layout_data"])
    assert {region["bbox"][0] for region in regions} == {100, 220}
    assert replacement["bubble_id"] == "bubble-stable"
    assert replacement["layout_data"]["detection"]["pass"] == 2
    assert len(regions) == 2
    assert "line_grouping" not in replacement["layout_data"]["detection"]
    assert replacement["orientation"] == ("horizontal" if replacement["bbox"][0] == 100 else "vertical")


def test_failed_forced_detection_keeps_last_good_regions_masks_and_clean_page(client, monkeypatch) -> None:
    class FailingSecondDetector:
        calls = 0

        def detect(self, _image_path):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("intentional detection failure")
            return [
                DetectionResult(
                    polygon=[[30, 20], [80, 20], [80, 80], [30, 80]],
                    bbox=[30, 20, 50, 60],
                    confidence=0.94,
                    metadata={
                        "balloon_assignment": {
                            "status": "outside",
                            "bubble_id": None,
                            "reason": "no_matching_balloon",
                        }
                    },
                )
            ]

    detector = FailingSecondDetector()
    inpainter = RecordingColorInpainter([(30, 170, 80)])
    original_provider = PipelineProcessor._provider

    def use_test_providers(processor, kind, requested):
        if kind == "detection":
            return detector, "failing-detector"
        if kind == "inpainting":
            return inpainter, "recording-inpainter"
        return original_provider(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", use_test_providers)
    project = client.post("/api/projects", json={"name": "atomic-forced-detection"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", solid_page(120, 100), "image/png")},
    ).json()[0]
    first = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "detection", "end_stage": "detection", "force": True},
    ).json()
    assert wait_for_task(client, first["id"])["status"] == "COMPLETED"
    region = client.get(f"/api/images/{page['id']}/regions").json()[0]
    uploaded = client.put(
        f"/api/regions/{region['id']}/mask",
        files={"file": ("mask.png", rectangular_mask(120, 100, (35, 25, 70, 75)), "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    repaired = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "inpainting", "end_stage": "inpainting", "force": True},
    ).json()
    assert wait_for_task(client, repaired["id"])["status"] == "COMPLETED"
    before_page = client.get(f"/api/images/{page['id']}").json()
    before_region = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert before_page["clean_url"]

    failed = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "detection", "end_stage": "detection", "force": True},
    ).json()
    assert wait_for_task(client, failed["id"])["status"] == "FAILED"

    after_page = client.get(f"/api/images/{page['id']}").json()
    after_region = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert after_page["clean_url"] == before_page["clean_url"]
    assert after_region["id"] == before_region["id"]
    assert after_region["bbox"] == before_region["bbox"]
    assert after_region["mask_url"] == before_region["mask_url"]


def test_source_geometry_edit_becomes_manual_and_is_replaced_by_forced_detection(client, monkeypatch) -> None:
    class MovingDetector:
        calls = 0

        def detect(self, _image_path):
            self.calls += 1
            x = 20 if self.calls == 1 else 220
            return [
                DetectionResult(
                    polygon=[[x + 5, 30], [x + 45, 30], [x + 50, 130], [x, 130]],
                    bbox=[x, 30, 50, 100],
                    confidence=0.95,
                    bubble_id="bubble-stable",
                    metadata={
                        "balloon_assignment": {"status": "assigned", "bubble_id": "bubble-stable"},
                    },
                )
            ]

    detector = MovingDetector()
    original_provider = PipelineProcessor._provider

    def use_moving_detector(processor, kind, requested):
        if kind == "detection":
            return detector, "moving-detector"
        return original_provider(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", use_moving_detector)
    project = client.post("/api/projects", json={"name": "preserve-edited-detection"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("edited.png", manga_page(), "image/png")},
    ).json()[0]
    first_task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "detection", "end_stage": "detection", "force": True},
    ).json()
    assert wait_for_task(client, first_task["id"])["status"] == "COMPLETED"
    detected = client.get(f"/api/images/{page['id']}/regions").json()[0]

    # Even though the bbox value is unchanged, setting it normalizes the
    # detector's trapezoid to a rectangle and is therefore a real source edit.
    edited = client.patch(f"/api/regions/{detected['id']}", json={"bbox": [20, 30, 50, 100]}).json()
    assert edited["layout_data"]["manual"] is True
    assert edited["bubble_id"] is None
    assert "balloon_assignment" not in edited["layout_data"]["detection"]

    second_task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "detection", "end_stage": "detection", "force": True},
    ).json()
    assert wait_for_task(client, second_task["id"])["status"] == "COMPLETED"
    regions = client.get(f"/api/images/{page['id']}/regions").json()
    assert edited["id"] not in {region["id"] for region in regions}
    assert len(regions) == 1
    assert any(region["bbox"][0] == 220 for region in regions)


def test_balloon_grouped_region_masks_only_its_source_text_polygons(client, monkeypatch) -> None:
    source_polygons = [
        [[20, 20], [52, 20], [52, 80], [20, 80]],
        [[108, 20], [140, 20], [140, 80], [108, 80]],
    ]

    class GroupedDetector:
        def detect(self, _image_path):
            bbox = [20, 20, 120, 60]
            bubble_mask = np.zeros((90, 150), dtype=np.uint8)
            cv2.ellipse(bubble_mask, (75, 45), (70, 40), 0, 0, 360, 1, -1)
            return [
                DetectionResult(
                    polygon=[[20, 20], [140, 20], [140, 80], [20, 80]],
                    bbox=bbox,
                    confidence=0.91,
                    bubble_id="bubble-safe-mask",
                    metadata={
                        "balloon_assignment": {
                            "status": "assigned",
                            "bubble_id": "bubble-safe-mask",
                            "coverage": 0.95,
                            "core_coverage": 0.99,
                            "score": 0.96,
                            "balloon_confidence": 0.97,
                        },
                        "line_grouping": {
                            "method": "balloon_instance",
                            "source_count": 2,
                            "source_polygons": source_polygons,
                        },
                    },
                    balloon_mask=bubble_mask,
                    balloon_mask_origin=(10, 5),
                    balloon_mask_id="bubble-safe-mask",
                    balloon_mask_confidence=0.97,
                )
            ]

    detector = GroupedDetector()
    original_provider = PipelineProcessor._provider

    def use_grouped_detector(processor, kind, requested):
        if kind == "detection":
            return detector, "grouped-detector"
        if kind == "inpainting":

            class NoModelNeeded:
                def inpaint(self, *_args):
                    raise AssertionError("A verified flat balloon should not need model inference")

            return NoModelNeeded(), "unused-for-flat-balloon"
        return original_provider(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", use_grouped_detector)
    project = client.post("/api/projects", json={"name": "safe-grouped-mask"}).json()
    grouped_image = Image.new("RGB", (180, 100), (90, 100, 110))
    draw = ImageDraw.Draw(grouped_image)
    draw.rectangle((28, 42, 33, 58), fill="black")
    draw.rectangle((118, 42, 123, 58), fill="black")
    grouped_source = BytesIO()
    grouped_image.save(grouped_source, "PNG")
    grouped_source.seek(0)
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("grouped.png", grouped_source, "image/png")},
    ).json()[0]
    detection_task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "detection", "end_stage": "detection", "force": True},
    ).json()
    detection_task = wait_for_task(client, detection_task["id"])
    assert detection_task["status"] == "COMPLETED", detection_task

    mask_task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "mask", "end_stage": "mask", "force": True, "options": {"expand": 1}},
    ).json()
    mask_task = wait_for_task(client, mask_task["id"])
    assert mask_task["status"] == "COMPLETED", mask_task
    region = client.get(f"/api/images/{page['id']}/regions").json()[0]
    response = client.get(region["mask_url"])
    mask = cv2.imdecode(np.frombuffer(response.content, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)

    assert region["bubble_id"] == "bubble-safe-mask"
    assert region["layout_data"]["detection"]["balloon_constraint"]["status"] == "available"
    assert region["layout_data"]["mask_generation"]["method"] == "source_polygon_union"
    assert region["layout_data"]["mask_generation"]["constraint"]["status"] in {
        "applied",
        "relaxed",
    }
    assert region["layout_data"]["mask_generation"]["constraint"]["outside_pixels_after"] == 0
    assert mask[50, 30] == 255
    assert mask[50, 120] == 255
    assert mask[50, 80] == 0
    assert mask[20, 20] == 0

    repair_task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "inpainting", "end_stage": "inpainting", "force": True},
    ).json()
    assert wait_for_task(client, repair_task["id"])["status"] == "COMPLETED"

    edited = client.patch(
        f"/api/regions/{region['id']}",
        json={"bbox": [18, 18, 124, 64]},
    ).json()
    assert edited["bubble_id"] is None
    assert "line_grouping" not in edited["layout_data"]["detection"]
    assert "balloon_constraint" not in edited["layout_data"]["detection"]
    assert edited["mask_url"] is None


def test_outside_balloon_region_uses_conservative_glyph_mask(client, monkeypatch) -> None:
    class OutsideDetector:
        def detect(self, _image_path):
            return [
                DetectionResult(
                    polygon=[[25, 15], [85, 15], [85, 85], [25, 85]],
                    bbox=[25, 15, 60, 70],
                    confidence=0.93,
                    metadata={
                        "balloon_assignment": {
                            "status": "outside",
                            "bubble_id": None,
                            "reason": "no_matching_balloon",
                        }
                    },
                )
            ]

    original_provider = PipelineProcessor._provider

    def use_outside_detector(processor, kind, requested):
        if kind == "detection":
            return OutsideDetector(), "outside-detector"
        return original_provider(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", use_outside_detector)
    image = Image.new("RGB", (120, 100), "#ebebeb")
    draw = ImageDraw.Draw(image)
    draw.rectangle((38, 25, 44, 75), fill="#141414")
    draw.rectangle((58, 25, 64, 75), fill="#141414")
    upload = BytesIO()
    image.save(upload, "PNG")
    upload.seek(0)

    project = client.post("/api/projects", json={"name": "outside-safe-mask"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("outside.png", upload, "image/png")},
    ).json()[0]
    detection_task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "detection", "end_stage": "detection", "force": True},
    ).json()
    assert wait_for_task(client, detection_task["id"])["status"] == "COMPLETED"
    mask_task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "mask", "end_stage": "mask", "force": True, "options": {"expand": 1}},
    ).json()
    assert wait_for_task(client, mask_task["id"])["status"] == "COMPLETED"

    region = client.get(f"/api/images/{page['id']}/regions").json()[0]
    mask_response = client.get(region["mask_url"])
    mask = cv2.imdecode(np.frombuffer(mask_response.content, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    metadata = region["layout_data"]["mask_generation"]

    assert metadata["method"] == "conservative_glyph"
    assert metadata["constraint"] == {
        "version": 1,
        "status": "conservative",
        "reason": "outside_balloon",
    }
    assert 0 < cv2.countNonZero(mask) < 3000
    assert mask[50, 40] == 255
    assert mask[50, 30] == 0


@pytest.mark.parametrize("grouped", [False, True])
def test_generated_mask_is_clipped_to_current_polygon_after_expansion(client, monkeypatch, grouped):
    import hashlib

    from app.core.database import SessionLocal
    from app.models import TextRegion
    from app.pipeline import processor as processor_module

    project = client.post("/api/projects", json={"name": "strict-mask-boundary"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images", files={"files": ("page.png", solid_page(), "image/png")}
    ).json()[0]
    polygon = [[30, 10], [80, 30], [60, 80], [10, 60]]
    region = client.post(f"/api/images/{page['id']}/regions", json={"polygon": polygon}).json()
    if grouped:
        with SessionLocal() as db:
            stored = db.get(TextRegion, region["id"])
            stored.layout_data = {
                "detection": {
                    "line_grouping": {
                        "method": "balloon_instance",
                        "source_polygons": [polygon, [[70, 20], [110, 20], [110, 70], [70, 70]]],
                    }
                }
            }
            db.commit()
    raw = np.full((100, 180), 255, np.uint8)
    calls = []

    def expanded_mask(_source, polygons, output, **_kwargs):
        calls.append(polygons)
        output.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output), raw)
        return {
            "method": "balloon_glyph",
            "repair": {
                "strategy": "uniform_fill",
                "color_rgb": [255, 255, 255],
                "mask_sha256": hashlib.sha256(raw.tobytes()).hexdigest(),
            },
        }

    monkeypatch.setattr(processor_module, "create_text_mask", expanded_mask)
    monkeypatch.setattr(processor_module, "create_text_mask_union", expanded_mask)
    task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "mask", "end_stage": "mask", "force": True, "options": {"text_segmentation": False}},
    ).json()
    assert wait_for_task(client, task["id"])["status"] == "COMPLETED"
    current = client.get(f"/api/images/{page['id']}/regions").json()[0]
    mask = np.asarray(Image.open(BytesIO(client.get(current["mask_url"]).content)))
    boundary = np.zeros_like(raw)
    cv2.fillPoly(boundary, [np.asarray(polygon, np.int32)], 255)
    assert np.array_equal(mask, boundary)  # Not the axis-aligned bounding box.
    assert len(calls) == 1
    assert calls[0] == (polygon if not grouped else [polygon, [[70, 20], [110, 20], [110, 70], [70, 70]]])
    generation = current["layout_data"]["mask_generation"]
    assert generation["region_boundary"]["clipped_pixels"] > 0
    assert generation["repair"]["mask_sha256"] == hashlib.sha256(mask.tobytes()).hexdigest()
    assert generation["automatic_mask_sha256"] == hashlib.sha256(mask.tobytes()).hexdigest()


@pytest.mark.parametrize("legacy_expand", [None, 2, 12])
def test_manual_frame_still_uses_learned_text_segmentation(client, monkeypatch, legacy_expand):
    from app.pipeline import processor as processor_module

    probability = np.zeros((100, 180), np.float32)
    probability[25:65, 40:46] = 1.0
    monkeypatch.setattr(
        processor_module, "text_probability", lambda *_args, **_kwargs: (probability, {"status": "ready", "model": "test"})
    )
    monkeypatch.setattr(
        processor_module, "refine_probability", lambda *_args, **_kwargs: (probability, {"status": "ready"})
    )
    project = client.post("/api/projects", json={"name": "manual-frame-segmentation"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images", files={"files": ("page.png", solid_page(), "image/png")}
    ).json()[0]
    created = client.post(
        f"/api/images/{page['id']}/regions", json={"polygon": [[25, 10], [80, 10], [80, 80], [25, 80]]}
    ).json()
    assert created["layout_data"]["manual"] is True
    task = client.post(
        f"/api/images/{page['id']}/process", json={"start_stage": "mask", "end_stage": "mask", "force": True,
            "options": {} if legacy_expand is None else {"expand": legacy_expand}}
    ).json()
    assert wait_for_task(client, task["id"])["status"] == "COMPLETED"
    current = client.get(f"/api/images/{page['id']}/regions").json()[0]
    mask = np.asarray(Image.open(BytesIO(client.get(current["mask_url"]).content)))
    generation = current["layout_data"]["mask_generation"]
    assert generation["text_segmentation"]["status"] == "ready"
    assert generation["method"] == "learned_glyph"
    assert generation["fallback_reason"] is None
    assert mask[45, 42] == 255
    assert mask[45, 70] == 0
    assert np.array_equal(mask, (probability > 0).astype(np.uint8) * 255)
    assert generation["glyph_expansion_px"] == 0
    assert current["polygon"] == created["polygon"]


def test_manual_mask_edit_preserves_clean_page_and_marks_only_the_mask_pending(client, monkeypatch) -> None:
    inpainter = RecordingColorInpainter([(20, 180, 60)])
    original_provider = PipelineProcessor._provider

    def use_recording_inpainter(processor, kind, requested):
        if kind == "inpainting":
            return inpainter, "recording-inpainter"
        return original_provider(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", use_recording_inpainter)
    project = client.post("/api/projects", json={"name": "manual-mask-invalidation"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", solid_page(120, 100), "image/png")},
    ).json()[0]
    region = client.post(
        f"/api/images/{page['id']}/regions",
        json={"polygon": [[25, 20], [75, 20], [75, 80], [25, 80]]},
    ).json()
    uploaded = client.put(
        f"/api/regions/{region['id']}/mask",
        files={"file": ("mask.png", rectangular_mask(120, 100, (35, 30, 65, 70)), "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    repaired = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "inpainting", "end_stage": "inpainting", "force": True},
    ).json()
    assert wait_for_task(client, repaired["id"])["status"] == "COMPLETED"
    clean_url = client.get(f"/api/images/{page['id']}").json()["clean_url"]
    assert clean_url
    clean_bytes = client.get(clean_url).content

    edited = client.post(
        f"/api/regions/{region['id']}/mask/operation",
        json={"operation": "dilate", "amount": 3},
    )
    assert edited.status_code == 200, edited.text
    edited_region = edited.json()

    assert client.get(f"/api/images/{page['id']}").json()["clean_url"] == clean_url
    assert client.get(clean_url).content == clean_bytes
    assert edited_region["layout_data"]["mask_generation"]["repair_pending"] is True
    assert edited_region["layout_data"]["mask_generation"]["method"] == "manual_mask"
    assert edited_region["layout_data"]["mask_generation"]["constraint"] == {
        "version": 1,
        "status": "manual",
        "reason": "manual_mask_edit",
    }


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("selected", [False, True])
def test_forced_repair_preserves_manual_mask_and_original(client, monkeypatch, empty, selected):
    inpainter = RecordingColorInpainter([(20, 180, 60)])
    original_provider = PipelineProcessor._provider

    def provider(processor, kind, requested):
        return (inpainter, "recording") if kind == "inpainting" else original_provider(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", provider)
    project = client.post("/api/projects", json={"name": "manual-mask-rerun"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images", files={"files": ("page.png", solid_page(120, 100), "image/png")}
    ).json()[0]
    region = client.post(f"/api/images/{page['id']}/regions", json={"bbox": [20, 20, 60, 60]}).json()
    original_bytes = client.get(page["original_url"]).content
    mask = Image.new("RGBA", (120, 100), (255, 255, 255, 0))
    if not empty:
        ImageDraw.Draw(mask).rectangle((40, 35, 55, 60), fill=(255, 255, 255, 255))
    encoded = BytesIO()
    mask.save(encoded, "PNG")
    encoded.seek(0)
    response = client.put(
        f"/api/regions/{region['id']}/mask",
        files={"file": ("mask.png", encoded, "image/png")},
        data={"expected_updated_at": region["updated_at"]},
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    saved_bytes = client.get(saved["mask_url"]).content
    if selected:
        response = client.post(f"/api/regions/{region['id']}/inpaint", json={"force": True})
    else:
        response = client.post(
            f"/api/images/{page['id']}/process", json={"start_stage": "mask", "end_stage": "inpainting", "force": True}
        )
    assert response.status_code == 202, response.text
    completed = wait_for_task(client, response.json()["id"])
    assert completed["status"] == "COMPLETED", completed
    current = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert current["mask_url"] == saved["mask_url"]
    assert client.get(current["mask_url"]).content == saved_bytes
    assert current["layout_data"]["mask_generation"]["method"] == "manual_mask"
    assert current["layout_data"]["mask_generation"]["repair_pending"] is False
    assert client.get(page["original_url"]).content == original_bytes
    clean_url = client.get(f"/api/images/{page['id']}").json()["clean_url"]
    with Image.open(BytesIO(client.get(clean_url).content)) as clean:
        assert clean.getpixel((25, 25))[:3] == (90, 100, 110)
        assert clean.getpixel((45, 45))[:3] == ((90, 100, 110) if empty else (20, 180, 60))


def test_mask_upload_checks_version_lock_and_preserves_previous_asset(client):
    project = client.post("/api/projects", json={"name": "mask-version"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images", files={"files": ("page.png", solid_page(120, 100), "image/png")}
    ).json()[0]
    region = client.post(f"/api/images/{page['id']}/regions", json={"bbox": [20, 20, 60, 60]}).json()
    url = f"/api/regions/{region['id']}/mask"

    def upload(version):
        return client.put(
            url,
            data={"expected_updated_at": version},
            files={"file": ("mask.png", rectangular_mask(120, 100, (35, 30, 65, 70)), "image/png")},
        )

    first = upload(region["updated_at"])
    assert first.status_code == 200, first.text
    saved = first.json()
    previous = client.get(saved["mask_url"]).content
    assert upload(region["updated_at"]).status_code == 409
    second = upload(saved["updated_at"])
    assert second.status_code == 200, second.text
    assert second.json()["mask_url"] != saved["mask_url"]
    assert client.get(saved["mask_url"]).content == previous
    locked = client.patch(f"/api/regions/{region['id']}", json={"locked": True}).json()
    assert upload(locked["updated_at"]).status_code == 409


def test_ocr_commits_incremental_region_progress(client, monkeypatch) -> None:
    second_region_started = threading.Event()
    release_second_region = threading.Event()

    class IncrementalOCR:
        calls = 0

        def ensure_loaded(self) -> None:
            return

        def recognize(self, _image_path, _bbox, orientation, _padding) -> OCRResult:
            self.calls += 1
            if self.calls == 2:
                second_region_started.set()
                release_second_region.wait(timeout=5)
            return OCRResult(text=f"recognized-{self.calls}", confidence=0.9, orientation=orientation)

    fake_ocr = IncrementalOCR()
    original_provider = PipelineProcessor._provider

    def use_incremental_ocr(processor, kind, requested):
        if kind == "ocr":
            return fake_ocr, "incremental-test"
        return original_provider(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", use_incremental_ocr)
    project = client.post("/api/projects", json={"name": "incremental-ocr"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("ocr.png", manga_page(), "image/png")},
    ).json()[0]
    for index in range(2):
        response = client.post(
            f"/api/images/{page['id']}/regions",
            json={"bbox": [90 + index * 80, 80, 60, 140], "reading_order": index + 1},
        )
        assert response.status_code == 201, response.text

    task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "ocr", "end_stage": "ocr", "force": True, "options": {}},
    ).json()
    try:
        assert second_region_started.wait(timeout=3)
        partial_task = client.get(f"/api/tasks/{task['id']}").json()
        assert partial_task["status"] == "RUNNING"
        assert partial_task["message"] == "OCR 1/2"
        assert partial_task["progress"] == 0.5
        partial_regions = client.get(f"/api/images/{page['id']}/regions").json()
        from app.core.config import get_settings
        expected = ["",""] if get_settings().checkpoint_pipeline_enabled else ["recognized-1", ""]
        assert [region["source_text"] for region in partial_regions] == expected
    finally:
        release_second_region.set()

    for _ in range(100):
        completed = client.get(f"/api/tasks/{task['id']}").json()
        if completed["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.03)
    assert completed["status"] == "COMPLETED", completed


def test_cancelling_ocr_stops_before_the_next_region(client, monkeypatch) -> None:
    third_region_started = threading.Event()
    release_third_region = threading.Event()

    class CancelAwareOCR:
        calls = 0

        def ensure_loaded(self) -> None:
            return

        def recognize(self, _image_path, _bbox, orientation, _padding) -> OCRResult:
            self.calls += 1
            if self.calls == 3:
                third_region_started.set()
                release_third_region.wait(timeout=5)
            return OCRResult(text=f"recognized-{self.calls}", confidence=0.9, orientation=orientation)

    fake_ocr = CancelAwareOCR()
    original_provider = PipelineProcessor._provider

    def use_cancel_aware_ocr(processor, kind, requested):
        if kind == "ocr":
            return fake_ocr, "cancel-aware-test"
        return original_provider(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", use_cancel_aware_ocr)
    project = client.post("/api/projects", json={"name": "cancel-ocr"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("cancel.png", manga_page(), "image/png")},
    ).json()[0]
    for index in range(4):
        client.post(
            f"/api/images/{page['id']}/regions",
            json={"bbox": [50 + index * 80, 80, 60, 140], "reading_order": index + 1},
        )

    task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "ocr", "end_stage": "ocr", "force": True, "options": {}},
    ).json()
    try:
        assert third_region_started.wait(timeout=3)
        cancel_started_at = time.perf_counter()
        cancelled = client.post(f"/api/tasks/{task['id']}/cancel")
        cancel_duration = time.perf_counter() - cancel_started_at
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLING"
        assert cancel_duration < 1.0
        retry_while_stopping = client.post(f"/api/tasks/{task['id']}/retry")
        assert retry_while_stopping.status_code == 409
    finally:
        release_third_region.set()

    for _ in range(100):
        cancelled_task = client.get(f"/api/tasks/{task['id']}").json()
        updated_page = client.get(f"/api/images/{page['id']}").json()
        if cancelled_task["status"] == "CANCELLED" and updated_page["current_stage"] is None:
            break
        time.sleep(0.03)
    regions = client.get(f"/api/images/{page['id']}/regions").json()
    assert cancelled_task["status"] == "CANCELLED"
    assert fake_ocr.calls == 3
    from app.core.config import get_settings
    if get_settings().checkpoint_pipeline_enabled:
        from app.storage import get_storage
        assert [region["source_text"] for region in regions]==["","","",""]
        # Completed crops are durable but remain PRIVATE until the OCR step
        # can be atomically published; cancellation must not discard them.
        directory = get_storage().page_dir(project["id"],page["id"])
        assert len(list(directory.glob("workflows/*/.requests/ocr/*/*.json")))==3
    else:
        assert [region["source_text"] for region in regions]==["recognized-1","recognized-2","recognized-3",""]
    assert updated_page["status"] == "UPLOADED"


def test_queued_cancel_can_be_retried_without_the_old_dispatch_running(client, monkeypatch) -> None:
    first_inference_started = threading.Event()
    release_first_inference = threading.Event()

    class QueuedRetryOCR:
        calls = 0

        def ensure_loaded(self) -> None:
            return

        def recognize(self, _image_path, _bbox, orientation, _padding) -> OCRResult:
            self.calls += 1
            if self.calls == 1:
                first_inference_started.set()
                release_first_inference.wait(timeout=5)
            return OCRResult(text=f"attempt-{self.calls}", confidence=0.95, orientation=orientation)

    fake_ocr = QueuedRetryOCR()
    original_provider = PipelineProcessor._provider

    def use_queued_retry_ocr(processor, kind, requested):
        if kind == "ocr":
            return fake_ocr, "queued-retry-test"
        return original_provider(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", use_queued_retry_ocr)
    project = client.post("/api/projects", json={"name": "queued-cancel-retry"}).json()
    pages = client.post(
        f"/api/projects/{project['id']}/images",
        files=[
            ("files", ("blocking.png", manga_page(), "image/png")),
            ("files", ("queued.png", manga_page(), "image/png")),
        ],
    ).json()
    for page in pages:
        response = client.post(
            f"/api/images/{page['id']}/regions",
            json={"bbox": [80, 70, 70, 150], "reading_order": 1},
        )
        assert response.status_code == 201, response.text

    blocking_task = client.post(
        f"/api/images/{pages[0]['id']}/process",
        json={"start_stage": "ocr", "end_stage": "ocr", "force": True},
    ).json()
    assert first_inference_started.wait(timeout=3)
    queued_task = client.post(
        f"/api/images/{pages[1]['id']}/process",
        json={"start_stage": "ocr", "end_stage": "ocr", "force": True},
    ).json()

    cancelled = client.post(f"/api/tasks/{queued_task['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    retried = client.post(f"/api/tasks/{queued_task['id']}/retry")
    assert retried.status_code == 202, retried.text
    assert retried.json()["status"] == "QUEUED"

    release_first_inference.set()
    blocking_task = wait_for_task(client, blocking_task["id"])
    retried = wait_for_task(client, queued_task["id"])
    assert blocking_task["status"] == "COMPLETED", blocking_task
    assert retried["status"] == "COMPLETED", retried
    assert fake_ocr.calls == 2
    queued_regions = client.get(f"/api/images/{pages[1]['id']}/regions").json()
    assert [region["source_text"] for region in queued_regions] == ["attempt-2"]


def test_ocr_process_can_target_multiple_selected_regions(client, monkeypatch) -> None:
    class SelectedRegionOCR:
        calls = 0

        def ensure_loaded(self) -> None:
            return

        def recognize(self, _image_path, _bbox, orientation, _padding) -> OCRResult:
            self.calls += 1
            return OCRResult(text=f"selected-{self.calls}", confidence=0.95, orientation=orientation)

    fake_ocr = SelectedRegionOCR()
    original_provider = PipelineProcessor._provider

    def use_selected_region_ocr(processor, kind, requested):
        if kind == "ocr":
            return fake_ocr, "selected-region-test"
        return original_provider(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", use_selected_region_ocr)
    project = client.post("/api/projects", json={"name": "selected-region-ocr"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("selected.png", manga_page(), "image/png")},
    ).json()[0]
    regions = [
        client.post(
            f"/api/images/{page['id']}/regions",
            json={"bbox": [40 + index * 90, 80, 60, 140], "reading_order": index + 1},
        ).json()
        for index in range(3)
    ]

    task = client.post(
        f"/api/images/{page['id']}/process",
        json={
            "start_stage": "ocr",
            "end_stage": "ocr",
            "force": True,
            "options": {"region_ids": [regions[0]["id"], regions[2]["id"]]},
        },
    ).json()
    for _ in range(100):
        task = client.get(f"/api/tasks/{task['id']}").json()
        if task["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.03)

    assert task["status"] == "COMPLETED", task
    updated = client.get(f"/api/images/{page['id']}/regions").json()
    assert [region["source_text"] for region in updated] == ["selected-1", "", "selected-2"]


def test_rerun_stages_preserve_source_and_translated_geometry(client, monkeypatch) -> None:
    class GeometryOCR:
        calls = 0

        def ensure_loaded(self) -> None:
            return

        def recognize(self, _image_path, _bbox, orientation, _padding) -> OCRResult:
            self.calls += 1
            return OCRResult(text=f"source-{self.calls}", confidence=0.97, orientation=orientation)

    class GeometryTranslator:
        config = {"model": "geometry-test"}

        async def translate_regions(self, regions, **_kwargs):
            return {region_key: f"translated-{index}" for index, (region_key, _) in enumerate(regions, start=1)}

    class GeometryRenderer:
        def render(self, background_path, regions, output_path, text_layer_path):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            text_layer_path.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(background_path) as background:
                background.convert("RGB").save(output_path, "PNG")
                Image.new("RGBA", background.size, (0, 0, 0, 0)).save(text_layer_path, "PNG")
            return {
                "layouts": {
                    region.id: {"font_size": region.font_size, "overflow": False, "placements": []}
                    for region in regions
                }
            }

    fake_ocr = GeometryOCR()
    fake_translator = GeometryTranslator()
    fake_inpainter = RecordingColorInpainter([(30, 140, 210)])
    original_provider = PipelineProcessor._provider

    def use_geometry_safe_providers(processor, kind, requested):
        if kind == "ocr":
            return fake_ocr, "geometry-ocr"
        if kind == "translation":
            return fake_translator, "geometry-translation"
        if kind == "inpainting":
            return fake_inpainter, "geometry-inpainting"
        if kind == "rendering":
            return GeometryRenderer(), "geometry-rendering"
        return original_provider(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", use_geometry_safe_providers)
    project = client.post("/api/projects", json={"name": "preserve-rerun-geometry"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("geometry.png", solid_page(), "image/png")},
    ).json()[0]
    definitions = [
        {
            "polygon": [[12, 12], [48, 9], [52, 58], [9, 62]],
            "translated_polygon": [[15, 8], [55, 14], [49, 65], [7, 57]],
            "rotation": 7.5,
            "perspective_warp": True,
        },
        {
            "polygon": [[92, 18], [151, 21], [147, 72], [88, 68]],
            "translated_polygon": [[86, 15], [158, 19], [151, 77], [83, 70]],
            "rotation": -4.0,
            "perspective_warp": True,
        },
    ]
    regions = []
    for index, definition in enumerate(definitions, start=1):
        response = client.post(
            f"/api/images/{page['id']}/regions",
            json={**definition, "reading_order": index, "font_size": 18},
        )
        assert response.status_code == 201, response.text
        regions.append(response.json())

    geometry_fields = (
        "bbox",
        "polygon",
        "translated_bbox",
        "translated_polygon",
        "rotation",
        "perspective_warp",
    )
    expected_geometry = {region["id"]: {field: region[field] for field in geometry_fields} for region in regions}
    region_ids = [region["id"] for region in regions]
    requests = [
        {"start_stage": "ocr", "end_stage": "ocr", "options": {"region_ids": region_ids}},
        {"start_stage": "translation", "end_stage": "translation", "options": {"region_ids": region_ids}},
        {"start_stage": "mask", "end_stage": "inpainting", "options": {"region_ids": region_ids}},
        {"start_stage": "rendering", "end_stage": "rendering", "options": {"region_ids": region_ids}},
    ]
    for request in requests:
        task = client.post(
            f"/api/images/{page['id']}/process",
            json={**request, "force": True},
        ).json()
        task = wait_for_task(client, task["id"])
        assert task["status"] == "COMPLETED", task
        persisted = {region["id"]: region for region in client.get(f"/api/images/{page['id']}/regions").json()}
        for region_id, geometry in expected_geometry.items():
            assert {field: persisted[region_id][field] for field in geometry_fields} == geometry

    assert [region["source_text"] for region in persisted.values()] == ["source-1", "source-2"]
    assert [region["translated_text"] for region in persisted.values()] == ["translated-1", "translated-2"]


@pytest.mark.parametrize("history_clean", [False, True])
def test_region_inpaint_only_reprocesses_selected_mask_and_clears_its_old_area(
    client, monkeypatch, history_clean
) -> None:
    fake_inpainter = RecordingColorInpainter([(20, 180, 40), (210, 40, 50)])
    original_provider = PipelineProcessor._provider

    def use_recording_inpainter(processor, kind, requested):
        if kind == "inpainting":
            return fake_inpainter, "recording-inpainter"
        return original_provider(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", use_recording_inpainter)
    project = client.post("/api/projects", json={"name": "single-region-inpaint"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("single.png", solid_page(), "image/png")},
    ).json()[0]
    polygons = [
        [[15, 20], [45, 20], [45, 60], [15, 60]],
        [[125, 20], [155, 20], [155, 60], [125, 60]],
    ]
    regions = [
        client.post(
            f"/api/images/{page['id']}/regions",
            json={"polygon": polygon, "reading_order": index + 1},
        ).json()
        for index, polygon in enumerate(polygons)
    ]
    old_selected_mask = np.asarray(Image.open(rectangular_mask(180, 100, (15, 20, 45, 60))))
    unselected_mask = np.asarray(Image.open(rectangular_mask(180, 100, (125, 20, 155, 60))))
    for region, box in zip(regions, [(15, 20, 45, 60), (125, 20, 155, 60)], strict=True):
        response = client.put(
            f"/api/regions/{region['id']}/mask",
            files={"file": ("mask.png", rectangular_mask(180, 100, box), "image/png")},
        )
        assert response.status_code == 200, response.text

    initial = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "inpainting", "end_stage": "inpainting", "force": True},
    ).json()
    initial = wait_for_task(client, initial["id"])
    assert initial["status"] == "COMPLETED", initial
    initial_page = client.get(f"/api/images/{page['id']}").json()
    with Image.open(BytesIO(client.get(initial_page["clean_url"]).content)) as image:
        clean_before = np.asarray(image.convert("RGB")).copy()

    if history_clean:
        client.patch(f"/api/regions/{regions[0]['id']}", json={"font_size": 38})
        head = client.get(f"/api/projects/{project['id']}/history").json()
        restored = client.post(
            f"/api/projects/{project['id']}/history/undo",
            json={"expected_entry_id": head["undo_id"], "current_image_id": page["id"]},
        ).json()
        initial_page = restored["page"]
        assert "/history/assets/" in initial_page["clean_url"]

    unchanged = client.patch(f"/api/regions/{regions[0]['id']}", json={"polygon": polygons[0]})
    assert unchanged.status_code == 200, unchanged.text
    assert client.get(f"/api/images/{page['id']}").json()["clean_url"] == initial_page["clean_url"]

    moved_polygon = [[60, 20], [90, 20], [90, 60], [60, 60]]
    moved = client.patch(f"/api/regions/{regions[0]['id']}", json={"polygon": moved_polygon})
    assert moved.status_code == 200, moved.text
    assert client.get(f"/api/images/{page['id']}").json()["clean_url"] is None
    rerun = client.post(f"/api/regions/{regions[0]['id']}/inpaint", json={"force": True}).json()
    rerun = wait_for_task(client, rerun["id"])
    assert rerun["status"] == "COMPLETED", rerun

    assert len(fake_inpainter.calls) == 2
    selected_rerun_mask = fake_inpainter.calls[1]
    assert selected_rerun_mask[40, 75] > 8
    assert selected_rerun_mask[40, 30] == 0
    assert selected_rerun_mask[40, 140] == 0
    rerun_page = client.get(f"/api/images/{page['id']}").json()
    with Image.open(BytesIO(client.get(rerun_page["clean_url"]).content)) as image:
        clean_after = np.asarray(image.convert("RGB")).copy()

    assert np.array_equal(clean_after[unselected_mask > 8], clean_before[unselected_mask > 8])
    old_only = (old_selected_mask > 8) & (selected_rerun_mask <= 8)
    assert np.all(clean_after[old_only] == (90, 100, 110))
    assert np.all(clean_after[selected_rerun_mask > 8] == (210, 40, 50))

    fake_inpainter.fail_after = 2
    failed = client.post(f"/api/regions/{regions[0]['id']}/inpaint", json={"force": True}).json()
    failed = wait_for_task(client, failed["id"])
    assert failed["status"] == "FAILED", failed
    failed_page = client.get(f"/api/images/{page['id']}").json()
    with Image.open(BytesIO(client.get(failed_page["clean_url"]).content)) as image:
        clean_after_failure = np.asarray(image.convert("RGB")).copy()
    assert np.array_equal(clean_after_failure, clean_after)


@pytest.mark.parametrize("new_box", [(25, 25, 40, 50), (60, 20, 95, 65), None])
def test_changed_manual_mask_repair_keeps_other_clean_pixels_and_is_reversible(client, monkeypatch, new_box):
    inpainter = RecordingColorInpainter([(20, 180, 40), (210, 40, 50)], threshold=0)
    original_provider = PipelineProcessor._provider
    monkeypatch.setattr(
        PipelineProcessor,
        "_provider",
        lambda processor, kind, requested: (
            (inpainter, "recording") if kind == "inpainting" else original_provider(processor, kind, requested)
        ),
    )
    project = client.post("/api/projects", json={"name": "pending-mask-repair"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images", files={"files": ("page.png", solid_page(), "image/png")}
    ).json()[0]
    regions = [
        # Target deliberately isn't first in API reading order. Match by ID,
        # never by timestamp order (the host clock can move backwards).
        client.post(f"/api/images/{page['id']}/regions", json={"bbox": box, "reading_order": 1-index}).json()
        for index, box in enumerate(([15, 20, 30, 40], [125, 20, 30, 40]))
    ]
    for region, box in zip(regions, [(15, 20, 45, 60), (125, 20, 155, 60)], strict=True):
        mask_file = rectangular_mask(180, 100, box)
        if region["id"] == regions[1]["id"]:
            soft = Image.open(mask_file).copy()
            ImageDraw.Draw(soft).rectangle((122, 20, 124, 60), fill=4)
            mask_file = BytesIO()
            soft.save(mask_file, "PNG")
            mask_file.seek(0)
        uploaded = client.put(f"/api/regions/{region['id']}/mask", files={"file": ("mask.png", mask_file, "image/png")})
        assert uploaded.status_code == 200, uploaded.text
    initial = client.post(
        f"/api/images/{page['id']}/process", json={"start_stage": "inpainting", "end_stage": "inpainting"}
    ).json()
    assert wait_for_task(client, initial["id"])["status"] == "COMPLETED"
    before_page = client.get(f"/api/images/{page['id']}").json()
    before_bytes = client.get(before_page["clean_url"]).content
    before_regions = {row["id"]: row for row in client.get(f"/api/images/{page['id']}/regions").json()}
    if new_box is None:
        encoded = BytesIO()
        Image.new("L", (180, 100), 0).save(encoded, "PNG")
        encoded.seek(0)
    else:
        encoded = rectangular_mask(180, 100, new_box)
    edited = client.put(
        f"/api/regions/{regions[0]['id']}/mask", files={"file": ("mask.png", encoded, "image/png")}
    ).json()
    assert edited["layout_data"]["mask_generation"]["repair_pending"] is True
    assert client.get(f"/api/images/{page['id']}").json()["clean_url"] == before_page["clean_url"]
    saved_mask = client.get(edited["mask_url"]).content
    task = client.post(
        f"/api/images/{page['id']}/process",
        json={
            "start_stage": "inpainting",
            "end_stage": "inpainting",
            "force": True,
            "options": {"region_ids": [edited["id"]], "repair_geometry": "source"},
        },
    ).json()
    assert wait_for_task(client, task["id"])["status"] == "COMPLETED"
    after_regions = {row["id"]: row for row in client.get(f"/api/images/{page['id']}/regions").json()}
    assert after_regions.keys() == before_regions.keys()
    assert after_regions[edited["id"]]["mask_url"] == edited["mask_url"]
    assert after_regions[edited["id"]]["layout_data"]["mask_generation"]["repair_pending"] is False
    assert {key: value for key, value in after_regions[regions[1]["id"]].items() if key != "editor_history"} == {
        key: value for key, value in before_regions[regions[1]["id"]].items() if key != "editor_history"
    }
    assert client.get(after_regions[edited["id"]]["mask_url"]).content == saved_mask
    clean_url = client.get(f"/api/images/{page['id']}").json()["clean_url"]
    with Image.open(BytesIO(client.get(clean_url).content)) as clean:
        assert clean.getpixel((140, 40)) == (20, 180, 40)
        assert clean.getpixel((123, 40)) == (20, 180, 40)  # Keep low-alpha repairs too.
        assert clean.getpixel((16, 21)) == (90, 100, 110)  # Erased old mask is restored.
        if new_box is not None:
            x, y, right, bottom = new_box
            assert clean.getpixel(((x + right) // 2, (y + bottom) // 2)) == (210, 40, 50)
    assert all(mask[40, 140] == 0 for mask in inpainter.calls[1:])
    head = client.get(f"/api/projects/{project['id']}/history").json()
    restored = client.post(
        f"/api/projects/{project['id']}/history/undo",
        json={"expected_entry_id": head["undo_id"], "current_image_id": page["id"]},
    ).json()
    assert next(row for row in restored["regions"] if row["id"] == edited["id"])["layout_data"]["mask_generation"]["repair_pending"] is True
    assert client.get(restored["page"]["clean_url"]).content == before_bytes
    # Undo restores immutable history assets, not the live clean/clean.png.
    # Repeating the mask-exit repair must reuse that composite as well.
    assert "/history/assets/" in restored["page"]["clean_url"]
    inpainter.colors.append((30, 40, 200))
    repeated = client.post(
        f"/api/images/{page['id']}/process",
        json={
            "start_stage": "inpainting",
            "end_stage": "inpainting",
            "force": True,
            "options": {"region_ids": [edited["id"]], "repair_geometry": "source"},
        },
    ).json()
    assert wait_for_task(client, repeated["id"])["status"] == "COMPLETED"
    repeated_page = client.get(f"/api/images/{page['id']}").json()
    with Image.open(BytesIO(client.get(repeated_page["clean_url"]).content)) as clean:
        # Compare all untouched pixels, not just a single sample in the bubble.
        before = np.asarray(Image.open(BytesIO(before_bytes)).convert("RGB"))
        after = np.asarray(clean.convert("RGB"))
        assert np.array_equal(after[:, 110:], before[:, 110:])
        assert clean.getpixel((16, 21)) == (90, 100, 110)


def test_legacy_edited_mask_without_pending_flag_is_exposed_for_auto_repair(client, monkeypatch):
    from app.core.database import SessionLocal
    from app.models import TextRegion

    inpainter = RecordingColorInpainter([(20, 180, 40)])
    original_provider = PipelineProcessor._provider
    monkeypatch.setattr(
        PipelineProcessor,
        "_provider",
        lambda processor, kind, requested: (
            (inpainter, "recording") if kind == "inpainting" else original_provider(processor, kind, requested)
        ),
    )
    project = client.post("/api/projects", json={"name": "legacy-pending-mask"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images", files={"files": ("page.png", solid_page(), "image/png")}
    ).json()[0]
    region = client.post(f"/api/images/{page['id']}/regions", json={"bbox": [15, 20, 30, 40]}).json()
    client.put(
        f"/api/regions/{region['id']}/mask",
        files={"file": ("mask.png", rectangular_mask(180, 100, (15, 20, 45, 60)), "image/png")},
    )
    with SessionLocal() as db:
        old = db.get(TextRegion, region["id"])
        layout = dict(old.layout_data)
        generation = dict(layout["mask_generation"])
        generation.pop("repair_pending")
        old.layout_data = {**layout, "mask_generation": generation}
        db.commit()
    current = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert current["layout_data"]["mask_generation"]["repair_pending"] is True
    task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "inpainting", "end_stage": "inpainting", "options": {"region_ids": [region["id"]]}},
    ).json()
    assert wait_for_task(client, task["id"])["status"] == "COMPLETED"
    repaired = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert repaired["layout_data"]["mask_generation"]["repair_pending"] is False
    assert client.get(f"/api/images/{page['id']}").json()["clean_url"]


def test_pending_manual_mask_survives_a_failed_partial_repair(client, monkeypatch):
    inpainter = RecordingColorInpainter([(20, 180, 40)])
    original_provider = PipelineProcessor._provider
    monkeypatch.setattr(
        PipelineProcessor,
        "_provider",
        lambda processor, kind, requested: (
            (inpainter, "recording") if kind == "inpainting" else original_provider(processor, kind, requested)
        ),
    )
    project = client.post("/api/projects", json={"name": "pending-mask-failure"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images", files={"files": ("page.png", solid_page(), "image/png")}
    ).json()[0]
    region = client.post(f"/api/images/{page['id']}/regions", json={"bbox": [15, 20, 30, 40]}).json()
    client.put(
        f"/api/regions/{region['id']}/mask",
        files={"file": ("mask.png", rectangular_mask(180, 100, (15, 20, 45, 60)), "image/png")},
    )
    initial = client.post(
        f"/api/images/{page['id']}/process", json={"start_stage": "inpainting", "end_stage": "inpainting"}
    ).json()
    assert wait_for_task(client, initial["id"])["status"] == "COMPLETED"
    clean_url = client.get(f"/api/images/{page['id']}").json()["clean_url"]
    before_bytes = client.get(clean_url).content
    edited = client.post(
        f"/api/regions/{region['id']}/mask/operation", json={"operation": "dilate", "amount": 3}
    ).json()
    inpainter.fail_after = 1
    task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "inpainting", "end_stage": "inpainting", "options": {"region_ids": [region["id"]]}},
    ).json()
    assert wait_for_task(client, task["id"])["status"] == "FAILED"
    current = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert current["layout_data"]["mask_generation"]["repair_pending"] is True
    assert current["mask_url"] == edited["mask_url"]
    assert client.get(client.get(f"/api/images/{page['id']}").json()["clean_url"]).content == before_bytes


@pytest.mark.parametrize("invalid_cache", ["missing", "foreign_history", "wrong_dimensions"])
def test_partial_repair_never_discards_unselected_pixels_when_clean_cache_is_invalid(tmp_path, invalid_cache):
    from app.storage.files import StorageManager

    processor = PipelineProcessor(SimpleNamespace())
    processor.storage = StorageManager(tmp_path)
    directory = processor.storage.page_dir("project", "page")
    original = directory / "original" / "page.png"
    final = directory / "clean" / "clean.png"
    Image.new("RGB", (180, 100), (90, 100, 110)).save(original)
    Image.new("RGB", (180, 100), (20, 180, 40)).save(final)
    before = final.read_bytes()
    selected_mask = directory / "masks" / "selected.png"
    other_mask = directory / "masks" / "other.png"
    with Image.open(rectangular_mask(180, 100, (15, 20, 45, 60))) as mask:
        mask.save(selected_mask)
    with Image.open(rectangular_mask(180, 100, (125, 20, 155, 60))) as mask:
        mask.save(other_mask)
    selected = SimpleNamespace(
        id="selected", region_key="R001", pixel_mask_path=processor.storage.relative(selected_mask)
    )
    other = SimpleNamespace(id="other", region_key="R002", pixel_mask_path=processor.storage.relative(other_mask))
    if invalid_cache == "foreign_history":
        cached = processor.storage.project_dir("another-project") / "history" / "assets" / "clean.png"
        cached.parent.mkdir(parents=True)
        Image.new("RGB", (180, 100), "red").save(cached)
    else:
        cached = directory / "clean" / "invalid.png"
        if invalid_cache == "wrong_dimensions":
            Image.new("RGB", (20, 20), "red").save(cached)
    page = SimpleNamespace(
        id="page",
        project_id="project",
        original_path=processor.storage.relative(original),
        clean_path=processor.storage.relative(cached),
        regions=[selected, other],
        metadata_json={},
    )
    provider = RecordingColorInpainter([(210, 40, 50)])
    with pytest.raises(ValueError):
        processor._inpaint_selected(page, provider, [selected], directory)
    assert provider.calls == []
    assert final.read_bytes() == before


def test_multi_region_inpaint_only_sends_selected_mask_union_to_provider(client, monkeypatch) -> None:
    fake_inpainter = RecordingColorInpainter([(30, 110, 200), (240, 170, 20)])
    original_provider = PipelineProcessor._provider

    def use_recording_inpainter(processor, kind, requested):
        if kind == "inpainting":
            return fake_inpainter, "recording-inpainter"
        return original_provider(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", use_recording_inpainter)
    project = client.post("/api/projects", json={"name": "multi-region-inpaint"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("multi.png", solid_page(), "image/png")},
    ).json()[0]
    boxes = [(10, 20, 30, 60), (70, 20, 90, 60), (130, 20, 150, 60)]
    regions = []
    for index, (left, top, right, bottom) in enumerate(boxes):
        polygon = [[left, top], [right, top], [right, bottom], [left, bottom]]
        region = client.post(
            f"/api/images/{page['id']}/regions",
            json={"polygon": polygon, "reading_order": index + 1},
        ).json()
        regions.append(region)
        response = client.put(
            f"/api/regions/{region['id']}/mask",
            files={"file": ("mask.png", rectangular_mask(180, 100, boxes[index]), "image/png")},
        )
        assert response.status_code == 200, response.text

    initial = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "inpainting", "end_stage": "inpainting", "force": True},
    ).json()
    initial = wait_for_task(client, initial["id"])
    assert initial["status"] == "COMPLETED", initial
    initial_page = client.get(f"/api/images/{page['id']}").json()
    with Image.open(BytesIO(client.get(initial_page["clean_url"]).content)) as image:
        clean_before = np.asarray(image.convert("RGB")).copy()

    moved_polygons = [
        [[35, 20], [55, 20], [55, 60], [35, 60]],
        [[150, 20], [170, 20], [170, 60], [150, 60]],
    ]
    moved_translated_polygons = [
        [[38, 18], [58, 22], [54, 63], [34, 59]],
        [[147, 18], [172, 21], [168, 64], [145, 60]],
    ]
    expected_geometry = {}
    for region, polygon, translated_polygon in zip(
        (regions[0], regions[2]), moved_polygons, moved_translated_polygons, strict=True
    ):
        response = client.patch(
            f"/api/regions/{region['id']}",
            json={"polygon": polygon, "translated_polygon": translated_polygon},
        )
        assert response.status_code == 200, response.text
        updated = response.json()
        expected_geometry[region["id"]] = {
            "bbox": updated["bbox"],
            "polygon": updated["polygon"],
            "translated_bbox": updated["bbox"],
            "translated_polygon": updated["polygon"],
        }

    rerun = client.post(
        f"/api/images/{page['id']}/process",
        json={
            "start_stage": "mask",
            "end_stage": "inpainting",
            "force": True,
            "options": {"region_ids": [regions[0]["id"], regions[2]["id"]]},
        },
    ).json()
    rerun = wait_for_task(client, rerun["id"])
    assert rerun["status"] == "COMPLETED", rerun

    persisted_regions = {region["id"]: region for region in client.get(f"/api/images/{page['id']}/regions").json()}
    for region_id, geometry in expected_geometry.items():
        assert {
            "bbox": persisted_regions[region_id]["bbox"],
            "polygon": persisted_regions[region_id]["polygon"],
            "translated_bbox": persisted_regions[region_id]["translated_bbox"],
            "translated_polygon": persisted_regions[region_id]["translated_polygon"],
        } == geometry

    assert len(fake_inpainter.calls) == 2
    selected_union = fake_inpainter.calls[1]
    assert selected_union[40, 45] > 8
    assert selected_union[40, 160] > 8
    assert selected_union[40, 80] == 0
    rerun_page = client.get(f"/api/images/{page['id']}").json()
    with Image.open(BytesIO(client.get(rerun_page["clean_url"]).content)) as image:
        clean_after = np.asarray(image.convert("RGB")).copy()
    unselected_mask = np.asarray(Image.open(rectangular_mask(180, 100, boxes[1])))
    assert np.array_equal(clean_after[unselected_mask > 8], clean_before[unselected_mask > 8])
    assert np.all(clean_after[selected_union > 8] == (240, 170, 20))


def test_mask_to_render_pipeline_and_export(client, monkeypatch) -> None:
    # This smoke test validates pipeline orchestration without downloading the
    # production LaMa weights into the lightweight unit-test environment.
    def load_fake_lama(provider: SimpleLaMaInpainter) -> None:
        provider._device = "cpu"
        provider._model = lambda image, _mask: image.copy()

    monkeypatch.setattr(SimpleLaMaInpainter, "load", load_fake_lama)
    project = client.post("/api/projects", json={"name": "pipeline-smoke"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("smoke.png", manga_page(), "image/png")},
    ).json()[0]
    region_response = client.post(
        f"/api/images/{page['id']}/regions",
        json={
            "bbox": [135, 105, 50, 145],
            "polygon": [[135, 105], [185, 105], [185, 250], [135, 250]],
            "source_text": "学校だよ",
            "translated_text": "去学校",
            "confidence": 0.95,
            "orientation": "vertical",
            "font_size": 24,
        },
    )
    assert region_response.status_code == 201, region_response.text
    task_response = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "mask", "end_stage": "rendering", "force": True, "options": {}},
    )
    assert task_response.status_code == 202, task_response.text
    task_id = task_response.json()["id"]
    task = task_response.json()
    for _ in range(300):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.03)
    assert task["status"] == "COMPLETED", task
    processed = client.get(f"/api/images/{page['id']}").json()
    assert processed["clean_url"]
    assert processed["rendered_url"]
    assert processed["text_layer_url"]
    assert processed["text_layer_region_ids"] == [region_response.json()["id"]]
    metadata_only_update = client.patch(f"/api/images/{page['id']}/ocr-exempt?exempt=true").json()
    assert metadata_only_update["clean_url"] == processed["clean_url"]
    assert metadata_only_update["rendered_url"] == processed["rendered_url"]
    assert metadata_only_update["text_layer_url"] == processed["text_layer_url"]
    updated_region = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert updated_region["mask_url"]
    assert updated_region["layout_data"]["placements"]
    snapshot = updated_region["layout_data"]["render_input"]
    assert snapshot["font_size"] == updated_region["font_size"]
    assert snapshot["translated_text"] == updated_region["translated_text"]
    assert "locked" not in snapshot
    exported = client.post(
        f"/api/projects/{project['id']}/export",
        json={"formats": ["translated", "clean", "text_layer", "json", "masks", "project"]},
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    assert len(exported.content) > 1000

    deleted = client.delete(f"/api/regions/{region_response.json()['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["rebuild_task"] is None
    assert client.get(f"/api/images/{page['id']}/regions").json() == []
    restored = client.get(f"/api/images/{page['id']}").json()
    assert restored["clean_url"] and restored["rendered_url"] is None and restored["text_layer_url"] is None
    original = Image.open(BytesIO(client.get(restored["original_url"]).content)).convert("RGB")
    clean = Image.open(BytesIO(client.get(restored["clean_url"]).content)).convert("RGB")
    assert ImageChops.difference(original, clean).getbbox() is None

    # Cached translated outputs are invalidated rather than blocking deletion;
    # export still rebuilds the current text layer without invoking inpainting.
    exported = client.post(f"/api/projects/{project['id']}/export", json={"formats": ["translated", "text_layer"]})
    assert exported.status_code == 200, exported.text
    restored = client.get(f"/api/images/{page['id']}").json()
    assert restored["rendered_url"] and restored["text_layer_url"]
    assert restored["text_layer_region_ids"] == []

    reset = client.post(f"/api/images/{page['id']}/reset")
    assert reset.status_code == 200, reset.text
    reset_page = reset.json()
    assert reset_page["status"] == "UPLOADED"
    assert reset_page["clean_url"] is None
    assert reset_page["rendered_url"] is None
    assert reset_page["text_layer_url"] is None
    assert reset_page["text_layer_region_ids"] is None
