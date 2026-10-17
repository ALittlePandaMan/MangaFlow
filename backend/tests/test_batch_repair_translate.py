from __future__ import annotations

import pytest
from app.core.database import SessionLocal
from app.models import ProcessingTask
from app.pipeline.processor import PipelineProcessor
from app.services.inpainting.providers import SimpleLaMaInpainter
from test_pipeline import rectangular_mask, solid_page, wait_for_task
from test_translation_auto_ocr import providers, setup  # noqa: F401


def batch(client, project_id, **extra):
    return client.post(f"/api/projects/{project_id}/batch-process", json={
        "start_stage": "translation", "end_stage": "rendering", "force": True,
        "only_with_regions": True,
        "options": {"rebuild_clean": True, "repair_geometry": "source", "text_segmentation": False},
        **extra,
    })


def test_page_counts_follow_create_delete_and_reset(client):
    page, regions = setup(client, ["原文", ""])
    project_id = page["project_id"]
    empty = client.post(f"/api/projects/{project_id}/images",
        files={"files": ("empty.png", solid_page(), "image/png")}).json()[0]
    assert empty["region_count"] == 0
    counts = {item["id"]: item["region_count"] for item in client.get(f"/api/projects/{project_id}/images").json()}
    assert counts == {page["id"]: 2, empty["id"]: 0}
    assert client.get(f"/api/images/{page['id']}").json()["region_count"] == 2
    assert client.delete(f"/api/regions/{regions[0]['id']}").status_code == 200
    assert client.get(f"/api/images/{page['id']}").json()["region_count"] == 1
    assert client.post(f"/api/images/{page['id']}/reset").json()["region_count"] == 0
    assert all(item["region_count"] == 0 for item in client.get(f"/api/projects/{project_id}/images").json())


@pytest.mark.parametrize("active_status", ["QUEUED", "RUNNING", "CANCELLING", "PAUSED"])
def test_batch_skips_empty_and_busy_pages_and_rechecks_duplicates(client, monkeypatch, active_status):
    dispatched = []
    monkeypatch.setattr("app.api.tasks.task_manager.dispatch", dispatched.append)
    page, _ = setup(client, [""])
    project_id = page["project_id"]
    extras = client.post(f"/api/projects/{project_id}/images", files=[
        ("files", (f"page-{i}.png", solid_page(), "image/png")) for i in range(2)
    ]).json()
    busy, empty = extras
    client.post(f"/api/images/{busy['id']}/regions", json={"bbox": [10, 10, 30, 40]})
    # Recognition exemption does not mean a manually authored page cannot be translated.
    client.patch(f"/api/images/{page['id']}/ocr-exempt", params={"exempt": True})
    with SessionLocal() as db:
        task = ProcessingTask(project_id=project_id, image_id=busy["id"], task_type="ocr", status=active_status, payload={})
        db.add(task)
        db.commit()
    try:
        response = batch(client, project_id)
        assert response.status_code == 202, response.text
        tasks = response.json()
        assert [item["image_id"] for item in tasks] == [page["id"]]
        assert dispatched == [tasks[0]["id"]]
        assert "only_with_regions" not in tasks[0]["payload"]
        assert tasks[0]["payload"]["start_stage"] == "translation"
        assert batch(client, project_id).json() == []
        assert batch(client, project_id, image_ids=[empty["id"]]).json() == []
        assert batch(client, project_id, image_ids=["missing-page"]).status_code == 404
    finally:
        # Never leave fake queued/running rows for lifespan recovery in the next test.
        from sqlalchemy import select
        with SessionLocal() as db:
            for task in db.scalars(select(ProcessingTask).where(ProcessingTask.project_id == project_id)):
                task.status = "CANCELLED"
            db.commit()


def test_batch_translates_existing_frames_auto_ocrs_blanks_and_preserves_manual_masks(client, providers, monkeypatch):  # noqa: F811
    ocr, translator, _ = providers
    page, regions = setup(client, [" \n\u3000", "已有原文", ""])
    ocr.responses[regions[2]["bbox"][0]] = ""
    manual = client.put(f"/api/regions/{regions[0]['id']}/mask",
        files={"file": ("mask.png", rectangular_mask(500, 120, (12, 12, 28, 36)), "image/png")}).json()
    mask_bytes = client.get(manual["mask_url"]).content
    client.patch(f"/api/regions/{regions[1]['id']}", json={"translated_text": "旧译文"})

    def fake_lama(provider):
        provider._device = "cpu"
        provider._model = lambda image, _mask: image.copy()

    monkeypatch.setattr(SimpleLaMaInpainter, "load", fake_lama)
    monkeypatch.setattr(PipelineProcessor, "detect", lambda *args, **kwargs: pytest.fail("Batch must not redetect frames"))
    response = batch(client, page["project_id"])
    assert response.status_code == 202, response.text
    assert len(response.json()) == 1
    result = wait_for_task(client, response.json()[0]["id"])
    assert result["status"] == "COMPLETED", result
    assert sorted(call[0] for call in ocr.calls) == sorted([regions[0]["bbox"], regions[2]["bbox"]])
    assert translator.calls[0][0] == [(regions[0]["region_key"], "识别的原文"), (regions[1]["region_key"], "已有原文")]
    rows = client.get(f"/api/images/{page['id']}/regions").json()
    assert [(row["id"], row["polygon"]) for row in rows] == [(row["id"], row["polygon"]) for row in regions]
    assert rows[1]["translated_text"] == "译文:已有原文"
    assert rows[2]["translated_text"] == ""
    assert "empty_source_text" in rows[2]["review_reasons"]
    assert client.get(rows[0]["mask_url"]).content == mask_bytes
    finished = client.get(f"/api/images/{page['id']}").json()
    assert finished["clean_url"] and finished["rendered_url"]
