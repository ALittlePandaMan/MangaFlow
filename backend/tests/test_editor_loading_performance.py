from datetime import datetime, timezone
from io import BytesIO

from app.core.database import SessionLocal
from app.models import ImagePage, ProcessingTask
from app.tasks.manager import TaskManager
from PIL import Image
from sqlalchemy import delete


def make_page(client):
    project = client.post("/api/projects", json={"name": "performance-regression"}).json()
    image = BytesIO()
    Image.new("RGB", (100, 160), "white").save(image, "PNG")
    page = client.post(f"/api/projects/{project['id']}/images",
                       files={"files": ("page.png", image.getvalue(), "image/png")}).json()[0]
    return project, page


def test_project_summary_uses_count_and_earliest_page_including_empty_projects(client):
    project, page = make_page(client)
    empty = client.post("/api/projects", json={"name": "empty-performance"}).json()
    with SessionLocal() as db:
        original = db.get(ImagePage, page["id"])
        original.order_index = 8
        db.add(ImagePage(project_id=project["id"], filename="last.png", original_path="projects/unused.png",
                         width=100, height=160, order_index=20, metadata_json={"diagnostic": "x" * 100_000}))
        db.commit()
    detail = client.get(f"/api/projects/{project['id']}").json()
    assert detail["page_count"] == 2
    assert detail["cover_url"] == page["original_url"]
    rows = {item["id"]: item for item in client.get("/api/projects").json()}
    assert rows[project["id"]] == detail
    assert rows[empty["id"]]["page_count"] == 0
    assert rows[empty["id"]]["cover_url"] is None


def test_page_index_preserves_urls_counts_and_classification_without_full_metadata(client):
    project, page = make_page(client)
    client.post(f"/api/images/{page['id']}/regions", json={"bbox": [10, 20, 30, 50]})
    with SessionLocal() as db:
        stored = db.get(ImagePage, page["id"])
        stored.clean_path = "projects/clean.png"
        stored.rendered_path = "projects/rendered.png"
        stored.text_layer_path = "projects/layer.png"
        stored.metadata_json = {
            "artifact_versions": {"clean": "c1", "rendered": "r1", "text_layer": "t1"},
            "text_classification": {"version": 2, "status": "ready", "excluded_count": 3, "policy": "keep"},
            "text_layer_region_ids": ["region-a"], "ocr_exempt": True,
            "detection_diagnostics": "large-detector-report" * 50_000,
        }
        db.commit()
    response = client.get(f"/api/projects/{project['id']}/images")
    assert response.status_code == 200, response.text
    indexed = response.json()[0]
    full = client.get(f"/api/images/{page['id']}").json()
    for key in ("clean_url", "rendered_url", "text_layer_url", "original_url", "thumbnail_url",
                "thumbnail_2x_url", "text_layer_region_ids", "text_classification", "ocr_exempt", "region_count"):
        assert indexed[key] == full[key], key
    assert indexed["region_count"] == 1
    assert indexed["rendered_url"].endswith("?v=r1")
    assert indexed["text_classification"]["excluded_count"] == 3
    assert indexed["ocr_exempt"] is True
    assert "large-detector-report" not in response.text


def test_status_query_includes_old_terminal_tasks_and_keeps_requested_order(client):
    project, page = make_page(client)
    with SessionLocal() as db:
        tasks = [ProcessingTask(project_id=project["id"], image_id=page["id"], task_type="ocr",
                                status="COMPLETED", created_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
                 for _ in range(505)]
        db.add_all(tasks)
        db.commit()
        ids = [task.id for task in tasks]
    try:
        requested = [ids[-1], ids[0], ids[2], ids[0]]
        response = client.post("/api/tasks/status", json=requested)
        assert response.status_code == 200, response.text
        assert [task["id"] for task in response.json()] == [ids[-1], ids[0], ids[2]]
        assert all(task["status"] == "COMPLETED" for task in response.json())
        assert client.post("/api/tasks/status", json=[]).status_code == 422
        assert client.post("/api/tasks/status", json=ids[:401]).status_code == 422
        assert client.post("/api/tasks/status", json=[ids[0], "missing-task"]).status_code == 404
    finally:
        with SessionLocal() as db:
            db.execute(delete(ProcessingTask).where(ProcessingTask.id.in_(ids)))
            db.commit()


def test_startup_uses_plain_task_ids_after_history_recovery_rolls_back_session(client, monkeypatch):
    project, page = make_page(client)
    with SessionLocal() as db:
        task = ProcessingTask(project_id=project["id"], image_id=page["id"], task_type="ocr", status="QUEUED")
        db.add(task)
        db.commit()
        identifier = task.id
    manager = TaskManager()
    dispatched = []
    monkeypatch.setattr(manager, "dispatch", dispatched.append)
    monkeypatch.setattr("app.services.editor_history.recover_task_history", lambda db: db.rollback())
    try:
        manager.recover_interrupted()
        assert identifier in dispatched
    finally:
        with SessionLocal() as db:
            db.execute(delete(ProcessingTask).where(ProcessingTask.id == identifier))
            db.commit()


def test_canvas_task_list_keeps_active_work_before_newer_history_and_limits_to_five(client):
    project, page = make_page(client)
    with SessionLocal() as db:
        running = ProcessingTask(project_id=project["id"], image_id=page["id"], task_type="ocr",
                                 status="RUNNING", created_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        queued = ProcessingTask(project_id=project["id"], image_id=page["id"], task_type="ocr", status="QUEUED")
        history = [ProcessingTask(project_id=project["id"], image_id=page["id"], task_type="ocr", status="COMPLETED") for _ in range(8)]
        db.add_all([running, queued, *history])
        db.commit()
        running_id, queued_id = running.id, queued.id
    try:
        rows = client.get("/api/tasks", params={"project_id":project["id"], "limit":5, "active_first":True}).json()
        assert len(rows) == 5
        assert [row["id"] for row in rows[:2]] == [running_id, queued_id]
        assert all(row["project_id"] == project["id"] for row in rows)
        normal = client.get("/api/tasks", params={"project_id":project["id"], "limit":5}).json()
        assert running_id not in [row["id"] for row in normal]
    finally:
        with SessionLocal() as db:
            db.execute(delete(ProcessingTask).where(ProcessingTask.project_id == project["id"]))
            db.commit()
