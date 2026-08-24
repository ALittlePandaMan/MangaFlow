from __future__ import annotations

import threading
import time
from io import BytesIO

import cv2
import numpy as np
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
    def __init__(self, colors: list[tuple[int, int, int]]) -> None:
        self.colors = colors
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
        image[mask > 8] = color
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(image).save(output_path, "PNG")
        return output_path


def test_forced_detection_preserves_manual_regions_and_replaces_detector_output(client, monkeypatch) -> None:
    class ChangingDetector:
        calls = 0

        def detect(self, _image_path):
            self.calls += 1
            x = 20 if self.calls == 1 else 220
            bbox = [x, 30, 50, 100]
            return [
                DetectionResult(
                    polygon=[[x, 30], [x + 50, 30], [x + 50, 130], [x, 130]],
                    bbox=bbox,
                    confidence=0.95,
                    metadata={"pass": self.calls},
                )
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

    assert manual["id"] in {region["id"] for region in regions}
    assert first_detected["id"] not in {region["id"] for region in regions}
    replacement = next(region for region in regions if "detection" in region["layout_data"])
    assert replacement["bbox"][0] == 220
    assert replacement["layout_data"]["detection"]["pass"] == 2


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
        assert [region["source_text"] for region in partial_regions] == ["recognized-1", ""]
    finally:
        release_second_region.set()

    for _ in range(100):
        completed = client.get(f"/api/tasks/{task['id']}").json()
        if completed["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.03)
    assert completed["status"] == "COMPLETED", completed


def test_cancelling_ocr_stops_before_the_next_region(client, monkeypatch) -> None:
    second_region_started = threading.Event()
    release_second_region = threading.Event()

    class CancelAwareOCR:
        calls = 0

        def ensure_loaded(self) -> None:
            return

        def recognize(self, _image_path, _bbox, orientation, _padding) -> OCRResult:
            self.calls += 1
            if self.calls == 2:
                second_region_started.set()
                release_second_region.wait(timeout=5)
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
    for index in range(3):
        client.post(
            f"/api/images/{page['id']}/regions",
            json={"bbox": [50 + index * 80, 80, 60, 140], "reading_order": index + 1},
        )

    task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "ocr", "end_stage": "ocr", "force": True, "options": {}},
    ).json()
    try:
        assert second_region_started.wait(timeout=3)
        cancelled = client.post(f"/api/tasks/{task['id']}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLED"
    finally:
        release_second_region.set()

    for _ in range(100):
        updated_page = client.get(f"/api/images/{page['id']}").json()
        if updated_page["current_stage"] is None:
            break
        time.sleep(0.03)
    regions = client.get(f"/api/images/{page['id']}/regions").json()
    assert fake_ocr.calls == 2
    assert [region["source_text"] for region in regions] == ["recognized-1", "recognized-2", ""]
    assert updated_page["status"] == "OCR_DONE"


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
    expected_geometry = {
        region["id"]: {field: region[field] for field in geometry_fields} for region in regions
    }
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
        persisted = {
            region["id"]: region for region in client.get(f"/api/images/{page['id']}/regions").json()
        }
        for region_id, geometry in expected_geometry.items():
            assert {field: persisted[region_id][field] for field in geometry_fields} == geometry

    assert [region["source_text"] for region in persisted.values()] == ["source-1", "source-2"]
    assert [region["translated_text"] for region in persisted.values()] == ["translated-1", "translated-2"]


def test_region_inpaint_only_reprocesses_selected_mask_and_clears_its_old_area(client, monkeypatch) -> None:
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

    moved_polygon = [[60, 20], [90, 20], [90, 60], [60, 60]]
    moved = client.patch(f"/api/regions/{regions[0]['id']}", json={"polygon": moved_polygon})
    assert moved.status_code == 200, moved.text
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
            "translated_bbox": updated["translated_bbox"],
            "translated_polygon": updated["translated_polygon"],
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

    persisted_regions = {
        region["id"]: region for region in client.get(f"/api/images/{page['id']}/regions").json()
    }
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
    metadata_only_update = client.patch(f"/api/images/{page['id']}/ocr-exempt?exempt=true").json()
    assert metadata_only_update["clean_url"] == processed["clean_url"]
    assert metadata_only_update["rendered_url"] == processed["rendered_url"]
    assert metadata_only_update["text_layer_url"] == processed["text_layer_url"]
    updated_region = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert updated_region["mask_url"]
    assert updated_region["layout_data"]["placements"]
    exported = client.post(
        f"/api/projects/{project['id']}/export",
        json={"formats": ["translated", "clean", "text_layer", "json", "masks", "project"]},
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    assert len(exported.content) > 1000

    deleted = client.delete(f"/api/regions/{region_response.json()['id']}")
    assert deleted.status_code == 200, deleted.text
    delete_task = deleted.json()["rebuild_task"]
    assert delete_task["payload"]["start_stage"] == "inpainting"
    assert delete_task["payload"]["end_stage"] == "rendering"
    for _ in range(300):
        delete_task = client.get(f"/api/tasks/{delete_task['id']}").json()
        if delete_task["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.03)
    assert delete_task["status"] == "COMPLETED", delete_task
    assert client.get(f"/api/images/{page['id']}/regions").json() == []
    restored = client.get(f"/api/images/{page['id']}").json()
    assert restored["clean_url"] and restored["rendered_url"] and restored["text_layer_url"]
    original = Image.open(BytesIO(client.get(restored["original_url"]).content)).convert("RGB")
    clean = Image.open(BytesIO(client.get(restored["clean_url"]).content)).convert("RGB")
    assert ImageChops.difference(original, clean).getbbox() is None

    reset = client.post(f"/api/images/{page['id']}/reset")
    assert reset.status_code == 200, reset.text
    reset_page = reset.json()
    assert reset_page["status"] == "UPLOADED"
    assert reset_page["clean_url"] is None
    assert reset_page["rendered_url"] is None
    assert reset_page["text_layer_url"] is None
