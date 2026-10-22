from __future__ import annotations

import threading
import time

import pytest
from app.core.database import SessionLocal
from app.models import (
    EditorHistoryEntry,
    ImagePage,
    ModelConfig,
    ProcessingBatch,
    ProcessingTask,
    StageCheckpoint,
    TaskExecutionRun,
    TaskWorkflow,
    TextRegion,
)
from app.pipeline import workflow
from app.services.execution import ExecutionPolicy, execution_runtime
from app.tasks import task_manager
from sqlalchemy import select
from test_batch_repair_translate import batch
from test_checkpoint_pipeline import checkpoint_mode as checkpoint_mode
from test_checkpoint_pipeline import stopped
from test_editor_history import apply as apply_history
from test_pipeline import solid_page, wait_for_task
from test_translation_auto_ocr import providers as providers
from test_translation_auto_ocr import setup


def worker_started(task_id: str) -> bool:
    with task_manager.lock:
        attempt = task_manager.attempts.get(task_id)
        return bool(attempt and attempt.started)


def setup_two(client, mode="snapshot"):
    first, first_regions = setup(client, ["前页原文"])
    second = client.post(f"/api/projects/{first['project_id']}/images",
        files={"files": ("second.png", solid_page(), "image/png")}).json()[0]
    second_region = client.post(f"/api/images/{second['id']}/regions", json={
        "polygon": [[10, 10], [50, 10], [50, 90], [10, 90]], "source_text": "后页原文",
    }).json()
    assert client.patch(f"/api/projects/{first['project_id']}", json={
        "settings": {"translation_context_mode": mode}, "translation_context": {"glossary": {"name": "old name"}},
    }).status_code == 200
    return first, second, first_regions[0], second_region


def test_snapshot_context_is_frozen_at_submission_and_private(client, providers, monkeypatch):
    _, translator, _ = providers
    first, second, first_region, _ = setup_two(client)
    dispatched = []
    dispatch = task_manager.dispatch
    monkeypatch.setattr(task_manager, "dispatch", dispatched.append)
    tasks = batch(client, first["project_id"]).json()
    assert len(tasks) == 2 and len({task["batch_id"] for task in tasks}) == 1
    assert all(task["dependency_task_id"] is None for task in tasks)
    assert "encrypted_api_key" not in str(tasks) and "dependency_context" not in str(tasks)
    batch_id = tasks[0]["batch_id"]
    with SessionLocal() as db:
        frozen = db.get(ProcessingBatch, batch_id)
        assert frozen.contexts[second["id"]]["regions"][0]["source"] == "前页原文"
        assert frozen.policy["project"]["target_language"] == "zh-CN"
        model = db.scalar(select(ModelConfig).where(ModelConfig.kind == "inpainting", ModelConfig.is_default.is_(True)))
        if model:
            model.config = {**model.config, "unrelated_later_setting": True}
            db.commit()
    # Simulate another writer finishing an earlier stage after submission;
    # interactive edits of queued pages remain correctly blocked by the API.
    with SessionLocal() as db:
        db.get(TextRegion, first_region["id"]).source_text = "提交后更新前页"
        db.commit()
    assert client.patch(f"/api/projects/{first['project_id']}", json={"target_language": "en", "translation_context": {"new": "changed"}}).status_code == 200
    monkeypatch.setattr(task_manager, "dispatch", dispatch)
    for identifier in dispatched:
        dispatch(identifier)
    for task in tasks:
        done = wait_for_task(client, task["id"])
        assert done["status"] == "COMPLETED", done
    context = next(context for rows, context in translator.calls if dict(rows).get("R001") == "后页原文")
    assert context["previous_page"]["regions"][0]["source"] == "前页原文"
    assert "new" not in context
    status = client.get(f"/api/projects/{first['project_id']}/batches/{batch_id}").json()
    assert status["completed"] == status["total"] == 2 and status["progress"] == 1


def test_previous_page_waits_for_translation_not_repair_and_takes_no_capacity(client, providers, monkeypatch):
    _, translator, _ = providers
    first, second, _, _ = setup_two(client, "previous_page")
    unrelated, _ = setup(client, ["无关项目"])
    first_translation, first_mask, second_translation = threading.Event(), threading.Event(), threading.Event()
    release_translation, release_mask = threading.Event(), threading.Event()
    original = workflow.run_stage
    runtime, policy = execution_runtime(), execution_runtime().policy
    runtime.update(ExecutionPolicy(mode="fixed", max_pages=2, cpu_workers=2), persist=False)

    def gated(stage, state, *args, **kwargs):
        if state["page"]["id"] == first["id"]:
            if stage == "translation":
                first_translation.set()
                assert release_translation.wait(12)
            elif stage == "mask":
                first_mask.set()
                assert release_mask.wait(12)
        elif state["page"]["id"] == second["id"] and stage == "translation":
            second_translation.set()
        return original(stage, state, *args, **kwargs)

    monkeypatch.setattr(workflow, "run_stage", gated)
    tasks = batch(client, first["project_id"]).json()
    try:
        assert tasks[1]["dependency_task_id"] == tasks[0]["id"]
        assert first_translation.wait(5) and first_mask.wait(5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            waiting = client.get(f"/api/tasks/{tasks[1]['id']}").json()
            if waiting["waiting_reason"] and waiting["status"] == "QUEUED" and not worker_started(tasks[1]["id"]):
                with SessionLocal() as db:
                    repaired = db.scalar(select(StageCheckpoint.id).join(TaskWorkflow).where(
                        TaskWorkflow.task_id == tasks[1]["id"],StageCheckpoint.stage == "inpainting",StageCheckpoint.status == "SUCCEEDED"))
                if repaired:
                    break
            time.sleep(.03)
        assert waiting["status"] == "QUEUED" and waiting["waiting_reason"] == "previous_page_translation"
        assert runtime.pages.active == 1
        other = batch(client, unrelated["project_id"]).json()[0]
        assert wait_for_task(client, other["id"])["status"] == "COMPLETED"
        assert not second_translation.is_set()
        release_translation.set()
        assert second_translation.wait(5), "Must not wait for previous-page repair"
        assert client.get(f"/api/images/{first['id']}").json()["rendered_url"] is None
        release_mask.set()
        for task in tasks:
            done = wait_for_task(client, task["id"])
            assert done["status"] == "COMPLETED", done
        context = next(context for rows, context in translator.calls if dict(rows).get("R001") == "后页原文")
        assert context["previous_page"]["regions"][0]["translation"] == "译文:前页原文"
        assert context["previous_page"]["page_id"] == first["id"]
    finally:
        release_translation.set()
        release_mask.set()
        for task in tasks:
            wait_for_task(client, task["id"])
        runtime.update(policy, persist=False)


def test_previous_translation_failure_is_explicit_and_retryable(client, providers, monkeypatch):
    first, _, _, _ = setup_two(client, "previous_page")
    original = workflow.run_stage

    def fail(stage, state, *args, **kwargs):
        if stage == "translation" and state["page"]["id"] == first["id"]:
            raise ValueError("known translation failure")
        return original(stage, state, *args, **kwargs)

    monkeypatch.setattr(workflow, "run_stage", fail)
    # This fixture intercepts a local synthetic translator. Select it by name
    # so unrelated API tests' saved cloud defaults cannot classify this known
    # pre-request failure as an uncertain paid request.
    tasks = batch(client, first["project_id"], provider="test-translator").json()
    for task in tasks:
        stopped(client, task["id"], "FAILED")
    failed = client.get(f"/api/tasks/{tasks[1]['id']}").json()
    assert failed["error_kind"] == "dependency_failed"
    with SessionLocal() as db:
        assert not db.scalar(select(StageCheckpoint.id).join(TaskWorkflow).where(
            TaskWorkflow.task_id == tasks[1]["id"],StageCheckpoint.stage == "translation"))
    monkeypatch.setattr(workflow, "run_stage", original)
    for task in tasks:
        assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 202
        done = wait_for_task(client, task["id"])
        assert done["status"] == "COMPLETED", done
        stopped(client, task["id"], "COMPLETED")


@pytest.mark.parametrize("mode", ["unknown", [], 123, None])
def test_context_mode_rejects_invalid_values(client, mode):
    assert client.post("/api/projects", json={"name": "invalid", "settings": {"translation_context_mode": mode}}).status_code == 422


def test_project_delete_cascades_private_batch_and_checkpoint_rows(client, providers):
    first, _, _, _ = setup_two(client)
    tasks = batch(client, first["project_id"]).json()
    for task in tasks:
        stopped(client, task["id"], "COMPLETED")
    assert client.delete(f"/api/projects/{first['project_id']}").status_code == 204
    with SessionLocal() as db:
        assert db.get(ProcessingBatch, tasks[0]["batch_id"]) is None
        assert all(db.get(ProcessingTask, task["id"]) is None for task in tasks)
        assert not db.scalar(select(StageCheckpoint.id).join(TaskWorkflow).where(TaskWorkflow.task_id.in_([task["id"] for task in tasks])))


def wait_reason(client, identifier, reason):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = client.get(f"/api/tasks/{identifier}").json()
        if value["waiting_reason"] == reason and value["status"] == "QUEUED" and not worker_started(identifier):
            with SessionLocal() as db:
                repaired = db.scalar(select(StageCheckpoint.id).join(TaskWorkflow).where(
                    TaskWorkflow.task_id == identifier,StageCheckpoint.stage == "inpainting",StageCheckpoint.status == "SUCCEEDED"))
            if not repaired:
                time.sleep(.03)
                continue
            return value
        time.sleep(.03)
    pytest.fail(f"Dependency reason did not become {reason}: {value}")


def test_paused_dependency_resumes_or_cancels_without_consuming_a_page_slot(client, providers, monkeypatch):
    first, _, _, _ = setup_two(client, "previous_page")
    dispatch = task_manager.dispatch
    monkeypatch.setattr(task_manager, "dispatch", lambda identifier: None)
    tasks = batch(client, first["project_id"]).json()
    parent, child = tasks
    assert client.post(f"/api/tasks/{parent['id']}/pause").status_code == 200
    monkeypatch.setattr(task_manager, "dispatch", dispatch)
    dispatch(child["id"])
    wait_reason(client, child["id"], "previous_page_paused")
    assert not worker_started(child["id"])
    assert client.post(f"/api/tasks/{child['id']}/pause").status_code == 200
    stopped(client, child["id"], "PAUSED")
    assert client.post(f"/api/tasks/{child['id']}/resume").status_code == 202
    wait_reason(client, child["id"], "previous_page_paused")
    # The parent no longer has an active wrapper. Cancellation still has to
    # wake its child immediately, rather than wait for the 30 s safety poll.
    assert client.post(f"/api/tasks/{parent['id']}/cancel").status_code == 200
    failed = stopped(client, child["id"], "FAILED")
    assert failed["error_kind"] == "dependency_failed"
    for task in tasks:
        assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 202
        stopped(client, task["id"], "COMPLETED")


def test_missing_dependency_never_silently_falls_back_to_old_text(client, providers, monkeypatch):
    first, _, _, _ = setup_two(client, "previous_page")
    dispatch = task_manager.dispatch
    monkeypatch.setattr(task_manager, "dispatch", lambda identifier: None)
    parent, child = batch(client, first["project_id"]).json()
    with SessionLocal() as db:
        db.delete(db.get(ProcessingTask, parent["id"]))
        db.commit()
    dispatch(child["id"])
    failed = stopped(client, child["id"], "FAILED")
    assert failed["error_kind"] == "dependency_missing"
    assert not worker_started(child["id"])


@pytest.mark.parametrize("status", ["RUNNING", "CANCELLING"])
def test_restart_before_workflow_creation_preserves_published_page(client, providers, monkeypatch, status):
    first, _, _, _ = setup_two(client)
    monkeypatch.setattr(task_manager, "dispatch", lambda identifier: None)
    tasks = batch(client, first["project_id"]).json()
    with SessionLocal() as db:
        page = db.get(ImagePage, first["id"])
        page.status, page.current_stage, page.error_message = "COMPLETED", None, None
        db.get(ProcessingTask, tasks[0]["id"]).status = status
        db.commit()
    task_manager.recover_interrupted()
    after = client.get(f"/api/images/{first['id']}").json()
    assert after["status"] == "COMPLETED" and after["error_message"] is None
    result = client.get(f"/api/tasks/{tasks[0]['id']}").json()
    assert result["status"] == ("FAILED" if status == "RUNNING" else "CANCELLED")


def test_recovery_does_not_interrupt_live_stage_or_timing_run(client, providers, monkeypatch):
    first, _, _, _ = setup_two(client)
    entered, release = threading.Event(), threading.Event()
    original = workflow.run_stage

    def gated(stage, state, *args, **kwargs):
        if state["page"]["id"] == first["id"] and stage == "translation":
            entered.set()
            assert release.wait(15)
        return original(stage, state, *args, **kwargs)

    monkeypatch.setattr(workflow, "run_stage", gated)
    tasks = batch(client, first["project_id"], image_ids=[first["id"]]).json()
    identifier = tasks[0]["id"]
    try:
        assert entered.wait(5)
        task_manager.recover_interrupted()
        with SessionLocal() as db:
            assert db.get(ProcessingTask, identifier).status == "RUNNING"
            stage = db.scalar(select(StageCheckpoint).join(TaskWorkflow).where(
                TaskWorkflow.task_id == identifier, StageCheckpoint.stage == "translation"))
            assert stage.status == "RUNNING" and stage.finished_at is None
            run = db.scalar(select(TaskExecutionRun).where(TaskExecutionRun.task_id == identifier))
            assert run.status == "RUNNING" and run.finished_at is None
    finally:
        release.set()
        stopped(client, identifier, "COMPLETED")


def test_previous_page_outside_batch_uses_its_frozen_context(client, providers):
    _, translator, _ = providers
    first, second, _, _ = setup_two(client, "previous_page")
    tasks = batch(client, first["project_id"], image_ids=[second["id"]]).json()
    assert len(tasks) == 1 and tasks[0]["dependency_task_id"] is None
    stopped(client, tasks[0]["id"], "COMPLETED")
    assert translator.calls[0][1]["previous_page"]["regions"][0]["source"] == "前页原文"


def test_queued_pause_can_resume_while_another_page_still_owns_capacity(client,providers,monkeypatch):
    first, _, _, _ = setup_two(client)
    entered,release = threading.Event(),threading.Event()
    original = workflow.run_stage
    runtime,policy = execution_runtime(),execution_runtime().policy
    runtime.update(ExecutionPolicy(mode="fixed",max_pages=1,cpu_workers=2),persist=False)

    def gated(stage,state,*args,**kwargs):
        if state["page"]["id"] == first["id"] and stage == "translation":
            entered.set()
            assert release.wait(10)
        return original(stage,state,*args,**kwargs)

    monkeypatch.setattr(workflow,"run_stage",gated)
    tasks = batch(client,first["project_id"]).json()
    try:
        assert entered.wait(5)
        assert client.post(f"/api/tasks/{tasks[1]['id']}/pause").status_code == 200
        stopped(client,tasks[1]["id"],"PAUSED")
        assert runtime.pages.active == 1
        assert client.post(f"/api/tasks/{tasks[1]['id']}/resume").status_code == 202
        assert client.get(f"/api/tasks/{tasks[1]['id']}").json()["status"] == "QUEUED"
    finally:
        release.set()
        for task in tasks:
            stopped(client,task["id"],"COMPLETED")
        runtime.update(policy,persist=False)


def test_prepared_repair_waits_without_a_worker_and_reuses_all_results_after_parent_resume(client,providers,monkeypatch):
    first,second,_,_ = setup_two(client,"previous_page")
    dispatch = task_manager.dispatch
    monkeypatch.setattr(task_manager,"dispatch",lambda identifier: None)
    parent,child = batch(client,first["project_id"]).json()
    assert client.post(f"/api/tasks/{parent['id']}/pause").status_code == 200
    monkeypatch.setattr(task_manager,"dispatch",dispatch)
    original = workflow.run_stage
    calls = []

    def record(stage,state,*args,**kwargs):
        calls.append((state["page"]["id"],stage))
        return original(stage,state,*args,**kwargs)

    monkeypatch.setattr(workflow,"run_stage",record)
    dispatch(child["id"])
    wait_reason(client,child["id"],"previous_page_paused")
    assert calls == [(second["id"],stage) for stage in ("ocr","mask","inpainting")]
    assert execution_runtime().pages.active == 0 and not worker_started(child["id"])
    assert client.get(f"/api/images/{second['id']}").json()["clean_url"] is None
    with SessionLocal() as db:
        assert not db.scalar(select(EditorHistoryEntry.id).where(EditorHistoryEntry.task_id == child["id"]))
        assert db.scalar(select(TaskExecutionRun.status).where(TaskExecutionRun.task_id == child["id"])) == "WAITING_DEPENDENCY"
    # Pause/resume and a fresh in-memory dispatch must retain durable native
    # results and must not need the parent to own a compute permit.
    assert client.post(f"/api/tasks/{child['id']}/pause").status_code == 200
    stopped(client,child["id"],"PAUSED")
    assert client.post(f"/api/tasks/{child['id']}/resume").status_code == 202
    wait_reason(client,child["id"],"previous_page_paused")
    assert client.post(f"/api/tasks/{parent['id']}/resume").status_code == 202
    for task in (parent,child):
        stopped(client,task["id"],"COMPLETED")
    for stage in ("ocr","mask","inpainting","translation","rendering"):
        assert calls.count((second["id"],stage)) == 1
    with SessionLocal() as db:
        entry = db.scalar(select(EditorHistoryEntry).where(EditorHistoryEntry.task_id == child["id"]))
        assert entry is not None and not entry.pending
        assert len(db.scalars(select(TaskWorkflow).where(TaskWorkflow.task_id == child["id"])).all()) == 1
    # The child has a single publication boundary and therefore a single undo.
    undone = apply_history(client,first["project_id"])
    assert undone["page"]["id"] == second["id"] and undone["page"]["clean_url"] is None
