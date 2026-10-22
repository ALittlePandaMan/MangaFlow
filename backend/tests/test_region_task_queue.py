"""Region requests admit immediately; page ownership orders their fresh inputs."""
import threading
from datetime import datetime, timedelta, timezone

import pytest
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import ProcessingTask
from app.pipeline.processor import PipelineProcessor
from app.services.base import OCRResult
from app.tasks.manager import TaskManager
from PIL import Image
from sqlalchemy import delete
from test_pipeline import wait_for_task
from test_translation_auto_ocr import setup


@pytest.mark.parametrize("checkpoint", [False, True])
@pytest.mark.parametrize("scope", ["single", "multi"])
def test_region_ocr_repair_translation_admit_while_busy_and_use_fresh_inputs(client, monkeypatch, checkpoint, scope):
    monkeypatch.setattr(get_settings(), "checkpoint_pipeline_enabled", checkpoint)
    monkeypatch.setattr(get_settings(), "text_segmentation_enabled", False)
    started, release = threading.Event(), threading.Event()
    calls, translated = [], []

    class OCR:
        def ensure_loaded(self):
            pass

        def recognize(self, path, bbox, orientation, padding, *, polygon=None, crop_angle=None):
            if not started.is_set():
                started.set()
                assert release.wait(15), "Test did not release OCR"
            calls.append(("ocr", bbox[0]))
            return OCRResult(f"fresh-{bbox[0]}", .99, orientation)

    class Translator:
        config = {}

        async def translate_regions(self, rows, **kwargs):
            calls.append(("translation", None))
            translated.extend(rows)
            return {key: f"translated:{text}" for key, text in rows}

    original = PipelineProcessor._provider

    def provider(processor, kind, requested):
        if kind == "ocr":
            return OCR(), "test-ocr"
        if kind == "translation":
            return Translator(), "test-translation"
        if kind == "inpainting":
            return object(), "test-repair"
        return original(processor, kind, requested)

    def repair(processor, provider, source, mask, output, regions):
        calls.append(("repair", None))
        output.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            image.save(output, "PNG")

    monkeypatch.setattr(PipelineProcessor, "_provider", provider)
    monkeypatch.setattr(PipelineProcessor, "_repair_masks", repair)
    page, regions = setup(client, ["old-1", "old-2", "untouched"])
    selected = regions[:1] if scope == "single" else regions[:2]
    ids = [region["id"] for region in selected]

    def submit(action, start, end):
        payload = {"start_stage": start, "end_stage": end, "force": True,
                   "options": {"region_ids": ids, "repair_geometry": "source"}}
        url = f"/api/regions/{ids[0]}/{action}" if scope == "single" else f"/api/images/{page['id']}/process"
        response = client.post(url, json=payload)
        assert response.status_code == 202, response.text
        return response.json()

    try:
        first = submit("ocr", "ocr", "ocr")
        assert started.wait(5)
        second = submit("inpaint", "mask", "inpainting")
        third = submit("translate", "translation", "translation")
        # These requests and navigation reads all return while OCR is blocked.
        assert client.get(f"/api/tasks/{second['id']}").json()["status"] == "QUEUED"
        assert client.get(f"/api/tasks/{third['id']}").json()["status"] == "QUEUED"
        assert client.get(f"/api/images/{page['id']}").status_code == 200
        assert calls == []
        release.set()
        for task in [first, second, third]:
            result = wait_for_task(client, task["id"])
            assert result["status"] == "COMPLETED", result
        assert [kind for kind, _ in calls] == ["ocr"] * len(selected) + ["repair", "translation"]
        assert dict(translated) == {region["region_key"]: f"fresh-{region['bbox'][0]}" for region in selected}
        rows = {row["id"]: row for row in client.get(f"/api/images/{page['id']}/regions").json()}
        for region in selected:
            assert rows[region["id"]]["translated_text"] == f"translated:fresh-{region['bbox'][0]}"
        assert rows[regions[2]["id"]]["source_text"] == "untouched"
        assert not rows[regions[2]["id"]]["translated_text"]
    finally:
        release.set()


def test_recovered_queue_keeps_creation_order_not_uuid_or_insertion_order(client, monkeypatch):
    page, _ = setup(client, ["original"])
    now = datetime.now(timezone.utc)
    ids = ["queue-ocr", "queue-repair", "queue-translation"]
    with SessionLocal() as db:
        for index in [2, 0, 1]:
            db.add(ProcessingTask(id=ids[index], project_id=page["project_id"], image_id=page["id"],
                task_type="ocr", status="QUEUED", created_at=now + timedelta(seconds=index)))
        db.commit()
    manager, dispatched = TaskManager(), []
    monkeypatch.setattr(manager, "dispatch", dispatched.append)
    try:
        manager.recover_interrupted()
        assert [identifier for identifier in dispatched if identifier in ids] == ids
    finally:
        with SessionLocal() as db:
            db.execute(delete(ProcessingTask).where(ProcessingTask.id.in_(ids)))
            db.commit()
