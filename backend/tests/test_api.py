import threading
import time
from io import BytesIO
from pathlib import Path

import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, ProcessingTask
from app.pipeline.processor import PipelineProcessor
from PIL import Image


def image_file() -> BytesIO:
    output = BytesIO()
    Image.new("RGB", (320, 480), "white").save(output, "PNG")
    output.seek(0)
    return output


def installed_ttf() -> Path:
    roots = (Path("/usr/local/lib/python3.12/site-packages"), Path("/usr/share/fonts"))
    font = next((path for root in roots if root.exists() for path in root.rglob("*.ttf")), None)
    if font is None:
        pytest.skip("No TrueType font available for upload test")
    return font


def test_project_page_and_region_crud(client) -> None:
    project_response = client.post("/api/projects", json={"name": "第一话"})
    assert project_response.status_code == 201
    project = project_response.json()
    edited_project = client.patch(
        f"/api/projects/{project['id']}",
        json={"name": "第一话·修订", "description": "项目说明"},
    )
    assert edited_project.status_code == 200, edited_project.text
    assert edited_project.json()["name"] == "第一话·修订"
    assert edited_project.json()["description"] == "项目说明"
    assert project["cover_url"] is None
    upload = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("page-001.png", image_file(), "image/png")},
    )
    assert upload.status_code == 201, upload.text
    page = upload.json()[0]
    assert page["width"] == 320 and page["height"] == 480
    assert page["original_url"].startswith("/media/")
    project_with_cover = client.get(f"/api/projects/{project['id']}")
    assert project_with_cover.status_code == 200, project_with_cover.text
    assert project_with_cover.json()["cover_url"].startswith(page["original_url"].split("?", 1)[0])
    created = client.post(
        f"/api/images/{page['id']}/regions",
        json={"bbox": [100, 40, 80, 140], "orientation": "vertical", "source_text": "こんにちは"},
    )
    assert created.status_code == 201, created.text
    region = created.json()
    assert region["region_key"] == "R001"
    assert len(region["polygon"]) == 4
    assert region["translated_bbox"] == region["bbox"]
    assert region["translated_polygon"] == region["polygon"]
    assert region["perspective_warp"] is False
    assert region["layout_data"]["manual"] is True

    translated_geometry = client.patch(
        f"/api/regions/{region['id']}",
        json={"translated_bbox": [125, 55, 120, 90]},
    )
    assert translated_geometry.status_code == 200, translated_geometry.text
    translated_region = translated_geometry.json()
    assert translated_region["bbox"] == region["bbox"]
    assert translated_region["polygon"] == region["polygon"]
    assert translated_region["translated_bbox"] == [125.0, 55.0, 120.0, 90.0]
    assert translated_region["translated_polygon"] == [
        [125.0, 55.0],
        [245.0, 55.0],
        [245.0, 145.0],
        [125.0, 145.0],
    ]
    with SessionLocal() as db:
        persisted_page = db.get(ImagePage, page["id"])
        assert persisted_page is not None
        persisted_page.rendered_path = "projects/cached-render.png"
        persisted_page.text_layer_path = "projects/cached-layer.png"
        db.commit()
    perspective = client.patch(f"/api/regions/{region['id']}", json={"perspective_warp": True})
    assert perspective.status_code == 200, perspective.text
    assert perspective.json()["perspective_warp"] is True
    invalidated_page = client.get(f"/api/images/{page['id']}").json()
    assert invalidated_page["rendered_url"] is None
    assert invalidated_page["text_layer_url"] is None
    updated = client.patch(f"/api/regions/{region['id']}", json={"translated_text": "你好", "locked": True})
    assert updated.status_code == 200
    assert updated.json()["translated_text"] == "你好"
    hidden = client.patch(f"/api/regions/{region['id']}", json={"visible": False})
    assert hidden.status_code == 200
    assert hidden.json()["visible"] is False
    client.patch(f"/api/regions/{region['id']}", json={"visible": True})
    repair_type = client.patch(
        f"/api/regions/{region['id']}",
        json={"region_type": "background_complex"},
    )
    assert repair_type.status_code == 200
    assert repair_type.json()["layout_data"]["region_type_manual"] is True
    mask_task = client.post(
        f"/api/images/{page['id']}/process",
        json={"start_stage": "mask", "end_stage": "mask", "force": True},
    ).json()
    for _ in range(100):
        mask_task = client.get(f"/api/tasks/{mask_task['id']}").json()
        if mask_task["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.02)
    assert mask_task["status"] == "COMPLETED", mask_task
    persisted_region = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert persisted_region["region_type"] == "background_complex"
    copied = client.post(f"/api/regions/{region['id']}/copy")
    assert copied.status_code == 201
    assert copied.json()["region_key"] == "R002"
    deleted_copy = client.delete(f"/api/regions/{copied.json()['id']}")
    assert deleted_copy.status_code == 200
    assert deleted_copy.json()["rebuild_task"] is None
    revisions = client.get(f"/api/regions/{region['id']}/revisions")
    assert revisions.status_code == 200 and len(revisions.json()) >= 2


def test_batch_recognition_only_enqueues_pages_without_completed_ocr(client, monkeypatch) -> None:
    monkeypatch.setattr("app.api.tasks.task_manager.dispatch", lambda _task_id: None)
    project = client.post("/api/projects", json={"name": "batch-unrecognized-only"}).json()
    pages = client.post(
        f"/api/projects/{project['id']}/images",
        files=[
            ("files", (f"page-{index}.png", image_file(), "image/png"))
            for index in range(5)
        ],
    ).json()

    for page, source_text in zip(pages[1:4], ["recognized", "recognized before later failure", ""], strict=True):
        response = client.post(
            f"/api/images/{page['id']}/regions",
            json={"bbox": [40, 50, 80, 140], "source_text": source_text},
        )
        assert response.status_code == 201, response.text

    with SessionLocal() as db:
        db.get(ImagePage, pages[1]["id"]).status = "OCR_DONE"
        db.get(ImagePage, pages[2]["id"]).status = "FAILED"
        db.get(ImagePage, pages[3]["id"]).status = "FAILED"
        db.commit()

    exempt = client.patch(f"/api/images/{pages[4]['id']}/ocr-exempt", params={"exempt": True})
    assert exempt.status_code == 200, exempt.text
    assert exempt.json()["ocr_exempt"] is True

    response = client.post(
        f"/api/projects/{project['id']}/batch-process",
        json={
            "start_stage": "detection",
            "end_stage": "ocr",
            "force": False,
            "only_unrecognized": True,
            "image_ids": [page["id"] for page in pages],
        },
    )
    assert response.status_code == 202, response.text
    tasks = response.json()
    assert {task["image_id"] for task in tasks} == {pages[0]["id"], pages[3]["id"]}

    with SessionLocal() as db:
        for task in tasks:
            queued = db.get(ProcessingTask, task["id"])
            assert queued is not None
            queued.status = "CANCELLED"
        db.commit()


def test_image_page_can_be_reordered_and_deleted(client) -> None:
    project = client.post("/api/projects", json={"name": "page-management"}).json()
    pages = [
        client.post(
            f"/api/projects/{project['id']}/images",
            files={"files": (f"page-{index}.png", image_file(), "image/png")},
        ).json()[0]
        for index in range(3)
    ]

    reordered = client.patch(
        f"/api/images/{pages[0]['id']}/order",
        params={"order_index": pages[2]["order_index"]},
    )

    assert reordered.status_code == 200, reordered.text
    listed = client.get(f"/api/projects/{project['id']}/images").json()
    assert [item["id"] for item in listed] == [pages[2]["id"], pages[1]["id"], pages[0]["id"]]
    project_after_reorder = client.get(f"/api/projects/{project['id']}").json()
    assert project_after_reorder["cover_url"].startswith(pages[2]["original_url"].split("?", 1)[0])

    deleted = client.delete(f"/api/images/{pages[1]['id']}")

    assert deleted.status_code == 204, deleted.text
    remaining = client.get(f"/api/projects/{project['id']}/images").json()
    assert [item["id"] for item in remaining] == [pages[2]["id"], pages[0]["id"]]


def test_processing_does_not_block_api_event_loop(client, monkeypatch) -> None:
    project = client.post("/api/projects", json={"name": "non-blocking-task"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", image_file(), "image/png")},
    ).json()[0]
    started = threading.Event()
    release = threading.Event()

    async def blocking_model_work(_processor, _task_id: str) -> None:
        started.set()
        release.wait(timeout=3)

    monkeypatch.setattr(PipelineProcessor, "execute", blocking_model_work)
    try:
        submitted_at = time.perf_counter()
        response = client.post(f"/api/images/{page['id']}/process", json={})
        submit_duration = time.perf_counter() - submitted_at
        assert response.status_code == 202, response.text
        assert submit_duration < 1.0
        assert started.wait(timeout=1)

        health_started_at = time.perf_counter()
        health = client.get("/health")
        health_duration = time.perf_counter() - health_started_at
        assert health.status_code == 200
        assert health.json()["version"] == "0.1.0"
        assert health_duration < 1.0
    finally:
        release.set()

    task_id = response.json()["id"]
    for _ in range(100):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.02)
    assert task["status"] == "COMPLETED", task


def test_bulk_delete_regions_from_one_page(client) -> None:
    project = client.post("/api/projects", json={"name": "bulk-delete"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", image_file(), "image/png")},
    ).json()[0]
    regions = [
        client.post(
            f"/api/images/{page['id']}/regions",
            json={"bbox": [20 + index * 80, 40, 50, 100]},
        ).json()
        for index in range(3)
    ]

    deleted = client.post(
        "/api/regions/bulk-delete",
        json=[regions[0]["id"], regions[2]["id"]],
    )

    assert deleted.status_code == 200, deleted.text
    assert set(deleted.json()["region_ids"]) == {regions[0]["id"], regions[2]["id"]}
    assert deleted.json()["rebuild_task"] is None
    remaining = client.get(f"/api/images/{page['id']}/regions").json()
    assert [region["id"] for region in remaining] == [regions[1]["id"]]


def test_merge_regions_returns_one_region(client) -> None:
    project = client.post("/api/projects", json={"name": "bulk-merge"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", image_file(), "image/png")},
    ).json()[0]
    regions = [
        client.post(
            f"/api/images/{page['id']}/regions",
            json={
                "bbox": [20 + index * 60, 40, 50, 100],
                "source_text": text,
                "translated_text": translated,
                "orientation": "horizontal",
                "reading_order": index + 1,
            },
        ).json()
        for index, (text, translated) in enumerate((("one", "一"), ("two", "二")))
    ]

    response = client.post("/api/regions/merge", json=[region["id"] for region in regions])

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["rebuild_task"] is None
    assert result["region"]["source_text"] == "one two"
    assert result["region"]["translated_text"] == "一 二"
    remaining = client.get(f"/api/images/{page['id']}/regions").json()
    assert [region["id"] for region in remaining] == [result["region"]["id"]]


def test_model_api_never_returns_plaintext_key(client) -> None:
    response = client.post(
        "/api/models/config",
        json={
            "kind": "translation",
            "name": "test-openai",
            "provider": "openai-compatible",
            "is_default": True,
            "config": {"base_url": "http://localhost:11434/v1", "model": "demo"},
            "api_key": "super-secret-value",
        },
    )
    assert response.status_code == 201, response.text
    assert "super-secret-value" not in response.text
    listed = client.get("/api/models")
    assert "super-secret-value" not in listed.text
    configured = next(item for item in listed.json()["configured"] if item["name"] == "test-openai")
    assert configured["has_api_key"] is True


def test_global_font_library_upload_list_and_delete(client) -> None:
    source = installed_ttf()
    with source.open("rb") as stream:
        uploaded = client.post("/api/fonts", files={"file": (source.name, stream, "font/ttf")})
    assert uploaded.status_code == 201, uploaded.text
    font = uploaded.json()
    assert font["name"]
    assert font["path"].startswith("fonts/")
    assert font["url"].startswith("/media/fonts/")

    listed = client.get("/api/fonts")
    assert listed.status_code == 200
    assert any(item["filename"] == font["filename"] for item in listed.json())

    removed = client.delete(f"/api/fonts/{font['filename']}")
    assert removed.status_code == 204
    assert all(item["filename"] != font["filename"] for item in client.get("/api/fonts").json())
