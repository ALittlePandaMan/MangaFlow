from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.core.config import get_settings
from app.core.database import SessionLocal, engine, initialize_database
from app.core.timing import TimingCollector, current_timings, measure
from app.main import app
from app.models import EditorHistoryEntry, ImagePage, ProcessingTask, Project, TaskExecutionRun, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services import editor_history
from app.services.base import OCRResult
from app.services.execution import ExecutionPolicy, ExecutionRuntime
from app.tasks.manager import TaskManager
from db_assertions import assert_rows_unlocked
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect
from test_execution import Probe
from test_pipeline import wait_for_task
from test_repair_geometry_sync import setup_page


def test_task_history_archives_without_row_locks_and_remains_reversible(client, monkeypatch):
    page, regions = setup_page(client, 2)
    archive = editor_history._archive_paths
    calls = []

    def check(*args, **kwargs):
        assert_rows_unlocked(engine, page["project_id"], page["id"])
        calls.append(1)
        return archive(*args, **kwargs)

    monkeypatch.setattr(editor_history, "_archive_paths", check)
    with SessionLocal() as db:
        task = ProcessingTask(project_id=page["project_id"], image_id=page["id"], task_type="ocr", status="RUNNING")
        db.add(task)
        db.commit()
        task_id = task.id
        entry_id = editor_history.begin_task_history(db, task)
        region = db.get(TextRegion, regions[0]["id"])
        region.source_text = "new OCR"
        db.commit()
        editor_history.finish_task_history(db, task_id, entry_id)
        entry = db.get(EditorHistoryEntry, entry_id)
        assert not entry.pending
        assert entry.before["regions"][0]["source_text"] == regions[0]["source_text"]
        assert entry.after["regions"][0]["source_text"] == "new OCR"
        db.get(ProcessingTask, task_id).status = "COMPLETED"
        db.commit()
    assert len(calls) == 2


def test_history_recaptures_if_page_changes_during_archive(client, monkeypatch):
    page, regions = setup_page(client)
    archive = editor_history._archive_paths
    changed = []

    def mutate_once(*args, **kwargs):
        result = archive(*args, **kwargs)
        if not changed:
            changed.append(1)
            with SessionLocal() as other:
                other.get(TextRegion, regions[0]["id"]).source_text = "concurrent change"
                other.commit()
        return result

    monkeypatch.setattr(editor_history, "_archive_paths", mutate_once)
    with SessionLocal() as db:
        task = ProcessingTask(project_id=page["project_id"], image_id=page["id"], task_type="ocr", status="RUNNING")
        db.add(task)
        db.commit()
        entry_id = editor_history.begin_task_history(db, task)
        entry = db.get(EditorHistoryEntry, entry_id)
        assert entry.before["regions"][0]["source_text"] == "concurrent change"
        task.status = "COMPLETED"
        db.commit()
        editor_history.finish_task_history(db, task.id, entry_id)


def test_maintenance_startup_does_not_touch_queued_tasks_or_history(monkeypatch):
    import app.main as main

    initialize_database()
    with SessionLocal() as db:
        project = Project(name="Maintenance fixture")
        db.add(project)
        db.flush()
        page = ImagePage(project_id=project.id, filename="test.png", original_path="fixture.png", width=10, height=10)
        db.add(page)
        db.flush()
        tasks = [ProcessingTask(project_id=project.id, image_id=page.id, task_type="ocr", status=status)
                 for status in ("QUEUED", "RUNNING", "PAUSED")]
        db.add_all(tasks)
        db.flush()
        entry = EditorHistoryEntry(project_id=project.id, image_id=page.id, task_id=tasks[1].id,
                                   action="pending", before={}, after={}, pending=True)
        db.add(entry)
        db.commit()
        project_id, task_ids, entry_id = project.id, [task.id for task in tasks], entry.id

    def unexpected(*args, **kwargs):
        pytest.fail("Maintenance startup must not initialize, provision, or collect")

    monkeypatch.setattr(get_settings(), "maintenance_mode", True)
    monkeypatch.setattr(main, "initialize_database", unexpected)
    monkeypatch.setattr(main, "provision_models_from_manifest", unexpected)
    try:
        with TestClient(app) as client:
            assert client.get("/health").json()["status"] == "maintenance"
            assert client.get("/api/projects").status_code == 503
            assert client.post("/api/projects", json={"name": "must not write"}).status_code == 503
            with pytest.raises(WebSocketDisconnect) as error:
                with client.websocket_connect(f"/api/ws/tasks/{task_ids[0]}"):
                    pass
            assert error.value.code == 1013
            manager = TaskManager()
            manager.start()
            manager.dispatch(task_ids[0])
            manager.recover_interrupted()
            assert manager.executor is None and not manager.attempts
        with SessionLocal() as db:
            assert [db.get(ProcessingTask, key).status for key in task_ids] == ["QUEUED", "RUNNING", "PAUSED"]
            assert db.get(EditorHistoryEntry, entry_id).pending
    finally:
        with SessionLocal() as db:
            db.delete(db.get(Project, project_id))
            db.commit()


@pytest.mark.parametrize("fails", [False, True])
def test_task_timings_persist_success_and_failure_without_text_payloads(client, monkeypatch, fails):
    page, _ = setup_page(client)

    class OCR:
        def ensure_loaded(self):
            pass

        def recognize(self, *args, **kwargs):
            if fails:
                raise ValueError("synthetic failure")
            return OCRResult("private text not in metrics", 1, "vertical")

    provider = PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda self, kind, name:
        (OCR(), "test") if kind == "ocr" else provider(self, kind, name))
    task = client.post(f"/api/images/{page['id']}/process", json={
        "start_stage": "ocr", "end_stage": "ocr", "force": True,
    }).json()
    result = wait_for_task(client, task["id"])
    assert result["status"] == ("FAILED" if fails else "COMPLETED")
    for _ in range(200):
        response = client.get(f"/api/tasks/{task['id']}/timings")
        runs = response.json()["runs"]
        if runs and runs[0]["finished_at"]:
            break
        time.sleep(.01)
    assert runs[0]["finished_at"] and runs[0]["status"] == result["status"]
    assert runs[0]["timings"]["stage.ocr"]["calls"] == 1
    minimum_archives = 1 if fails and get_settings().checkpoint_pipeline_enabled else 2
    assert runs[0]["timings"]["history.archive"]["calls"] >= minimum_archives
    assert runs[0]["wall_seconds"] >= runs[0]["timings"]["stage.ocr"]["seconds"]
    assert "private text" not in response.text and "synthetic failure" not in response.text


def test_task_pagination_preserves_default_and_is_stable_for_equal_timestamps(client):
    page, _ = setup_page(client)
    with SessionLocal() as db:
        rows = [ProcessingTask(project_id=page["project_id"], image_id=page["id"], task_type="ocr", status="COMPLETED")
                for _ in range(7)]
        db.add_all(rows)
        db.commit()
        expected = [row.id for row in db.scalars(select(ProcessingTask).where(
            ProcessingTask.image_id == page["id"],
        ).order_by(ProcessingTask.created_at.desc(), ProcessingTask.id.desc()))]
    found = []
    for offset in range(0, 7, 3):
        response = client.get("/api/tasks", params={"image_id": page["id"], "offset": offset, "limit": 3})
        assert response.status_code == 200
        found.extend(row["id"] for row in response.json())
    assert found == expected
    assert client.get("/api/tasks", params={"offset": -1}).status_code == 422


def test_timing_context_isolated_and_propagated_to_cpu_workers():
    runtime = ExecutionRuntime()
    runtime.cpu.probe = Probe()
    runtime.update(ExecutionPolicy(mode="fixed", cpu_workers=2), persist=False)
    barrier = threading.Barrier(2)

    def work(name):
        collector = TimingCollector()
        token = current_timings.set(collector)
        try:
            def job():
                with measure(name):
                    barrier.wait(3)
            assert list(runtime.map_cpu([job], check=lambda: None, estimate=1)) == [(0, None)]
            return collector.snapshot()
        finally:
            current_timings.reset(token)
    try:
        with ThreadPoolExecutor(2) as pool:
            a, b = list(pool.map(work, ("page.a", "page.b")))
        assert "page.a" in a and "page.b" not in a
        assert "page.b" in b and "page.a" not in b
        assert a["cpu.compute"]["calls"] == b["cpu.compute"]["calls"] == 1
        assert current_timings.get() is None
    finally:
        runtime.cpu_pool.shutdown()


def test_startup_reconciles_unfinished_timing_runs(client):
    page, _ = setup_page(client)
    with SessionLocal() as db:
        task = ProcessingTask(project_id=page["project_id"], image_id=page["id"], task_type="ocr", status="FAILED")
        db.add(task)
        db.flush()
        run = TaskExecutionRun(task_id=task.id)
        db.add(run)
        db.commit()
        run_id = run.id
    TaskManager().recover_interrupted()
    with SessionLocal() as db:
        run = db.get(TaskExecutionRun, run_id)
        assert run.status == "INTERRUPTED" and run.finished_at is not None
