"""Report/cache/parallel failures must not corrupt or replay successful work."""
import pytest
from app.api.helpers import require_task
from app.core.database import SessionLocal
from app.models import ModelConfig, ProcessingTask, StageCheckpoint, TaskWorkflow
from app.pipeline import workflow
from app.services.base import ProviderError
from app.services.checkpoints import CheckpointStore
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from test_batch_repair_translate import batch
from test_checkpoint_pipeline import checkpoint_mode as checkpoint_mode
from test_checkpoint_pipeline import stopped
from test_translation_auto_ocr import providers as providers
from test_translation_auto_ocr import setup


@pytest.fixture
def cloud_translation(client, providers):
    with SessionLocal() as db:
        model = ModelConfig(kind="translation", name="failure-boundary-cloud", provider="openai-compatible",
                            enabled=True, is_default=True, config={})
        db.add(model)
        db.commit()
        identifier = model.id
    try:
        yield providers[1]
    finally:
        with SessionLocal() as db:
            db.delete(db.get(ModelConfig, identifier))
            db.commit()


def test_failed_initial_progress_never_marks_unsent_translation_as_unknown(client, cloud_translation, monkeypatch):
    page, _ = setup(client, ["source"])
    failures = []
    original = workflow.PageWorkflow.report

    def report(self, stage, *, finished=False):
        if stage == "translation" and not finished and not failures:
            failures.append(1)
            raise RuntimeError("progress save failed before request")
        return original(self, stage, finished=finished)

    monkeypatch.setattr(workflow.PageWorkflow, "report", report)
    task = batch(client, page["project_id"]).json()[0]
    failed = stopped(client, task["id"], "FAILED")
    assert "progress save failed" in failed["error_message"]
    assert not cloud_translation.calls
    with SessionLocal() as db:
        checkpoint = db.scalar(select(StageCheckpoint).join(TaskWorkflow).where(
            TaskWorkflow.task_id == task["id"], StageCheckpoint.stage == "translation"))
        assert checkpoint.status == "FAILED"  # No paid request started.
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 202
    assert stopped(client, task["id"], "COMPLETED")["status"] == "COMPLETED"
    assert len(cloud_translation.calls) == 1


def test_both_parallel_errors_are_reported_without_overwriting_the_published_page(client, cloud_translation, monkeypatch):
    page, _ = setup(client, ["source"])
    original = workflow.run_stage
    def run(stage, *args, **kwargs):
        if stage == "translation":
            raise ProviderError("test empty translation response", error_kind="translation_response_empty")
        if stage == "inpainting":
            raise ValueError("test repair model failure")
        return original(stage, *args, **kwargs)
    monkeypatch.setattr(workflow, "run_stage", run)
    task = batch(client, page["project_id"]).json()[0]
    failed = stopped(client, task["id"], "FAILED")
    assert "翻译失败：test empty translation response" in failed["error_message"]
    assert "背景修复失败：test repair model failure" in failed["error_message"]
    current = client.get(f"/api/images/{page['id']}").json()
    assert current["clean_url"] is None and current["rendered_url"] is None


@pytest.mark.parametrize("failed_stage", ["translation", "inpainting"])
def test_failure_checkpoint_save_error_does_not_hide_the_original_failure(client, cloud_translation, monkeypatch, failed_stage):
    page, _ = setup(client, ["source"])
    original = workflow.run_stage
    def run(stage, *args, **kwargs):
        if stage == failed_stage:
            raise ProviderError("original model failure", error_kind="test_failure")
        return original(stage, *args, **kwargs)
    def fail(*args, **kwargs):
        raise RuntimeError("secondary checkpoint save failure")
    monkeypatch.setattr(workflow, "run_stage", run)
    monkeypatch.setattr(CheckpointStore, "fail", fail)
    task = batch(client, page["project_id"]).json()[0]
    failed = stopped(client, task["id"], "FAILED")
    assert "original model failure" in failed["error_message"]
    assert "secondary checkpoint save failure" not in failed["error_message"]
    with SessionLocal() as db:
        checkpoints = list(db.scalars(select(StageCheckpoint).join(TaskWorkflow).where(
            TaskWorkflow.task_id == task["id"])))
        assert all(row.status != "RUNNING" for row in checkpoints)
        status = next(row.status for row in checkpoints if row.stage == failed_stage)
        assert status == ("UNKNOWN" if failed_stage == "translation" else "FAILED")
    if failed_stage == "translation":
        assert failed["error_kind"] == "unknown_cloud_outcome"
        assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 409


@pytest.mark.parametrize("write", [False, True])
def test_task_control_reads_lock_only_for_state_changes(client, write):
    page, _ = setup(client, ["source"])
    with SessionLocal() as db:
        task = ProcessingTask(task_type="ocr", status="FAILED", project_id=page["project_id"], image_id=page["id"])
        db.add(task)
        db.commit()
        identifier = task.id
    with SessionLocal() as db:
        old = db.get(ProcessingTask, identifier)
        assert old.status == "FAILED"
        with SessionLocal() as other:
            other.get(ProcessingTask, identifier).status = "CANCELLED"
            other.commit()
        current = require_task(db, identifier, for_update=write)
        assert current.status == ("CANCELLED" if write else "FAILED")
        with SessionLocal() as other:
            query = select(ProcessingTask).where(ProcessingTask.id == identifier).with_for_update(nowait=True)
            if write:
                with pytest.raises(OperationalError) as error:
                    other.execute(query)
                assert error.value.orig.sqlstate == "55P03"
            else:
                other.execute(query)
