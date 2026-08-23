from __future__ import annotations

import threading
import time
from io import BytesIO

from app.pipeline.processor import PipelineProcessor
from app.services.base import OCRResult
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
