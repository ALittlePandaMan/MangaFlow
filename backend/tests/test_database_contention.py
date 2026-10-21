from __future__ import annotations

import threading
import time

import pytest
from app.core.database import SessionLocal, engine
from app.core.transactions import is_retryable_write, retry_write
from app.models import EditorHistoryEntry, ProcessingTask, RegionRevision
from app.pipeline.processor import PipelineProcessor
from app.services.base import OCRResult
from app.tasks import task_manager
from app.tasks.manager import TaskManager
from db_assertions import assert_rows_unlocked
from psycopg.errors import LockNotAvailable
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_editor_history import apply as apply_history
from test_pipeline import wait_for_task
from test_repair_geometry_sync import repair_provider as repair_provider
from test_repair_geometry_sync import setup_page


def busy():
    return OperationalError("test write", {}, LockNotAvailable("synthetic lock timeout"))


def test_transaction_replays_writes_after_rollback_not_just_commit(target):
    local = target
    with local.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE values_test (value INTEGER)")
    calls = []
    with Session(local) as db:
        def save():
            calls.append(1)
            db.execute(text("INSERT INTO values_test VALUES (9)"))
            if len(calls) == 1:
                raise busy()
        retry_write(db, save)
        assert db.execute(text("SELECT value FROM values_test")).all() == [(9,)]
    assert len(calls) == 2


def test_non_lock_errors_are_not_retried(target):
    local = target
    calls = []
    with Session(local) as db:
        def save():
            calls.append(1)
            raise ValueError("not transient")
        with pytest.raises(ValueError, match="not transient"):
            retry_write(db, save)
    assert calls == [1]
    assert not is_retryable_write(OperationalError("test", {}, OSError("disk I/O error")))


def test_retry_rejects_already_flushed_writes_instead_of_silently_rolling_them_back(target):
    local = target
    with local.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE values_test (value INTEGER)")
    with Session(local) as db:
        db.execute(text("INSERT INTO values_test VALUES (11)"))
        assert not db.dirty and not db.new
        with pytest.raises(ValueError, match="clean session"):
            retry_write(db, lambda: None)
        db.commit()
        assert db.execute(text("SELECT value FROM values_test")).all() == [(11,)]


@pytest.mark.parametrize("failure_at", ["result", "progress"])
def test_ocr_save_contention_does_not_repeat_inference_or_lose_history(client, monkeypatch, failure_at):
    page, regions = setup_page(client, 3)
    calls = []
    injected = []

    class OCR:
        def ensure_loaded(self):
            pass

        def recognize(self, *args):
            calls.append(1)
            return OCRResult(f"recognized-{len(calls)}", 1, "vertical")

    original = PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda self, kind, name:
        (OCR(), "test") if kind == "ocr" else original(self, kind, name))

    def fail_once(connection, cursor, statement, parameters, context, executemany):
        if injected:
            return
        values = parameters if executemany else [parameters]
        values = [tuple(row.values()) if isinstance(row, dict) else row for row in values]
        result = failure_at == "result" and statement.startswith("UPDATE text_regions SET") and any("recognized-1" in row for row in values)
        progress = failure_at == "progress" and statement.startswith("UPDATE processing_tasks SET") and any("OCR 1/3" in row for row in values)
        if result or progress:
            injected.append(1)
            raise busy()

    event.listen(engine, "before_cursor_execute", fail_once)
    try:
        task = client.post(f"/api/images/{page['id']}/process", json={
            "start_stage": "ocr", "end_stage": "ocr", "force": True,
        }).json()
        assert wait_for_task(client, task["id"])["status"] == "COMPLETED"
    finally:
        event.remove(engine, "before_cursor_execute", fail_once)
    assert injected == [1]
    assert len(calls) == 3
    rows = client.get(f"/api/images/{page['id']}/regions").json()
    assert [row["source_text"] for row in rows] == ["recognized-1", "recognized-2", "recognized-3"]
    with SessionLocal() as db:
        revisions = db.scalars(select(RegionRevision).where(
            RegionRevision.region_id.in_([region["id"] for region in regions]), RegionRevision.action == "ocr",
        )).all()
        assert len(revisions) == 3
    undone = apply_history(client, page["project_id"])["regions"]
    assert [row["source_text"] for row in undone] == [region["source_text"] for region in regions]
    redone = apply_history(client, page["project_id"], "redo")["regions"]
    assert [row["source_text"] for row in redone] == [row["source_text"] for row in rows]


def test_failure_save_can_also_fail_without_leaving_fake_running_task(client, monkeypatch):
    page, _ = setup_page(client)
    release = threading.Event()
    original = TaskManager._persist_failure

    async def fail(self, task_id):
        raise ValueError("inference failed")

    def save_failure(task_id, error):
        if not release.is_set():
            raise busy()
        return original(task_id, error)

    monkeypatch.setattr(PipelineProcessor, "execute", fail)
    from app.pipeline.workflow import PageWorkflow
    def fail_workflow(self):
        raise ValueError("inference failed")
    monkeypatch.setattr(PageWorkflow,"execute",fail_workflow)
    monkeypatch.setattr(TaskManager, "_persist_failure", staticmethod(save_failure))
    task = client.post(f"/api/images/{page['id']}/process", json={
        "start_stage": "ocr", "end_stage": "ocr",
    }).json()
    try:
        visible = wait_for_task(client, task["id"])
        assert visible["status"] == "FAILED"
        assert "恢复保存状态" in visible["message"]
        assert "inference failed" in visible["error_message"]
        assert task_manager.is_active(task["id"])
        with SessionLocal() as db:
            assert db.get(ProcessingTask, task["id"]).status == "RUNNING"
        assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 409
        listed = client.get("/api/tasks", params={"image_id": page["id"]}).json()
        assert listed[0]["status"] == "FAILED"
    finally:
        release.set()
    for _ in range(300):
        if not task_manager.is_active(task["id"]):
            break
        time.sleep(.01)
    assert not task_manager.is_active(task["id"])
    with SessionLocal() as db:
        saved = db.get(ProcessingTask, task["id"])
        assert saved.status == "FAILED" and saved.page.current_stage is None
        assert "inference failed" in saved.error_message
        assert not db.scalar(select(EditorHistoryEntry.id).where(
            EditorHistoryEntry.task_id == task["id"], EditorHistoryEntry.pending.is_(True),
        ))
    assert task_manager.terminal_error(task["id"]) is None


def test_no_row_lock_is_held_during_ocr(client, monkeypatch):
    page, _ = setup_page(client, 3)

    class OCR:
        def ensure_loaded(self):
            self.check()

        def check(self):
            assert_rows_unlocked(engine, page["project_id"], page["id"])

        def recognize(self, *args):
            self.check()
            return OCRResult("recognized", 1, "vertical")

    original = PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda self, kind, name:
        (OCR(), "test") if kind == "ocr" else original(self, kind, name))
    task = client.post(f"/api/images/{page['id']}/process", json={
        "start_stage": "ocr", "end_stage": "ocr", "force": True,
    }).json()
    assert wait_for_task(client, task["id"])["status"] == "COMPLETED"


@pytest.mark.parametrize("start_stage", ["mask", "inpainting"])
def test_target_geometry_and_masks_release_writer_before_model_work(client, monkeypatch, repair_provider, start_stage):
    page, regions = setup_page(client)
    client.patch(f"/api/regions/{regions[0]['id']}", json={"translated_bbox": [15, 12, 50, 140]})
    checked = []

    def check():
        assert_rows_unlocked(engine, page["project_id"], page["id"])
        checked.append(1)

    generate = PipelineProcessor.generate_masks
    repair = PipelineProcessor._repair_masks

    def generate_checked(self, *args, **kwargs):
        check()
        return generate(self, *args, **kwargs)

    def repair_checked(self, *args, **kwargs):
        check()
        return repair(self, *args, **kwargs)

    monkeypatch.setattr(PipelineProcessor, "generate_masks", generate_checked)
    monkeypatch.setattr(PipelineProcessor, "_repair_masks", repair_checked)
    task = client.post(f"/api/images/{page['id']}/process", json={
        "start_stage": start_stage, "end_stage": "inpainting", "force": True,
        "options": {"repair_geometry": "translated", "text_segmentation": False},
    }).json()
    assert wait_for_task(client, task["id"])["status"] == "COMPLETED"
    assert checked == [1, 1]
