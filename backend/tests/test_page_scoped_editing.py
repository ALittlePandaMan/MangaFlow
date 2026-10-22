"""A finished page is editable while other pages retain their queue ownership."""
import threading

import pytest
from app.core.config import get_settings
from app.pipeline.processor import PipelineProcessor
from app.services.base import OCRResult
from test_pipeline import wait_for_task
from test_repair_geometry_sync import png


@pytest.mark.parametrize("checkpoint", [False, True])
def test_finished_batch_page_edits_do_not_disturb_other_pages(client, monkeypatch, checkpoint):
    monkeypatch.setattr(get_settings(), "checkpoint_pipeline_enabled", checkpoint)
    project = client.post("/api/projects", json={"name": "page-scoped-editing"}).json()
    pages = client.post(f"/api/projects/{project['id']}/images", files=[
        ("files", (f"page-{index}.png", png(), "image/png")) for index in range(3)
    ]).json()
    regions = {}
    for page in pages:
        regions[page["id"]] = [client.post(f"/api/images/{page['id']}/regions", json={
            "bbox": [10 + index * 60, 10, 40, 160], "reading_order": index + 1,
        }).json() for index in range(2)]

    started, release = threading.Event(), threading.Event()

    class OCR:
        def ensure_loaded(self):
            pass

        def recognize(self, path, bbox, orientation, padding, *, polygon=None, crop_angle=None):
            if pages[1]["id"] in str(path):
                started.set()
                assert release.wait(30), "Test did not release the background page"
            return OCRResult(f"recognized-{bbox[0]}", .99, orientation)

    original = PipelineProcessor._provider

    def provider(processor, kind, requested):
        return (OCR(), "test-ocr") if kind == "ocr" else original(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", provider)
    try:
        response = client.post(f"/api/projects/{project['id']}/batch-process", json={
            "start_stage": "ocr", "end_stage": "ocr", "force": True,
            "image_ids": [page["id"] for page in pages], "only_with_regions": True,
        })
        assert response.status_code == 202, response.text
        tasks = {task["image_id"]: task for task in response.json()}
        first = wait_for_task(client, tasks[pages[0]["id"]]["id"])
        assert first["status"] == "COMPLETED", first
        assert started.wait(5)
        first_region, deleted_region = regions[pages[0]["id"]]
        polygon = [[20, 20], [65, 20], [65, 150], [20, 150]]
        moved = client.patch(f"/api/regions/{first_region['id']}", json={"polygon": polygon})
        assert moved.status_code == 200, moved.text
        removed = client.delete(f"/api/regions/{deleted_region['id']}")
        assert removed.status_code == 200, removed.text
        assert removed.json()["rebuild_task"] is None
        assert client.get(f"/api/tasks/{tasks[pages[1]['id']]['id']}").json()["status"] == "RUNNING"
        assert client.get(f"/api/tasks/{tasks[pages[2]['id']]['id']}").json()["status"] == "QUEUED"
        for page in pages[1:]:
            blocked = client.patch(f"/api/regions/{regions[page['id']][0]['id']}", json={"font_size": 99})
            assert blocked.status_code == 409, blocked.text
        release.set()
        for task in tasks.values():
            result = wait_for_task(client, task["id"])
            assert result["status"] == "COMPLETED", result
        final = client.get(f"/api/images/{pages[0]['id']}/regions").json()
        assert len(final) == 1 and final[0]["polygon"] == polygon
        for page in pages[1:]:
            final = client.get(f"/api/images/{page['id']}/regions").json()
            assert len(final) == 2
            assert all(row["source_text"].startswith("recognized-") and row["font_size"] != 99 for row in final)
    finally:
        release.set()
