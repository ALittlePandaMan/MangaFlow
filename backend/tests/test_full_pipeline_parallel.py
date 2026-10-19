"""Full original-image workflows must use the same safe parallel join."""
import threading

import pytest
from app.core.database import SessionLocal
from app.models import ProcessingBatch, StageCheckpoint, TaskWorkflow
from app.pipeline import workflow
from app.pipeline.processor import PipelineProcessor
from app.services.base import DetectionResult
from sqlalchemy import select
from test_checkpoint_pipeline import checkpoint_mode as checkpoint_mode  # noqa: F401
from test_checkpoint_pipeline import stopped
from test_editor_history import apply as undo
from test_translation_auto_ocr import providers as providers  # noqa: F401
from test_translation_auto_ocr import setup


def detector(monkeypatch):
    calls = []

    class Detector:
        def detect(self, path):
            calls.append(str(path))
            return [DetectionResult([[10, 10], [50, 10], [50, 90], [10, 90]], [10, 10, 40, 80], .99)]

    original = PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda self, kind, requested:
        (Detector(), "test-detection") if kind == "detection" else original(self, kind, requested))
    return calls


@pytest.mark.parametrize("start", ["detection", "ocr", "translation"])
def test_every_whole_page_prefix_reaches_parallel_join_and_one_undo(client, providers, monkeypatch, start):  # noqa: F811
    page, old = setup(client, [""])
    detection_calls = detector(monkeypatch)
    barrier, started, release = threading.Barrier(2), threading.Event(), threading.Event()
    run, calls = workflow.run_stage, []

    def gated(stage, *args, **kwargs):
        calls.append(stage)
        if stage in {"translation", "mask"}:
            barrier.wait(5)
            started.set()
            assert release.wait(5)
        return run(stage, *args, **kwargs)

    monkeypatch.setattr(workflow, "run_stage", gated)
    task = client.post(f"/api/images/{page['id']}/process", json={
        "start_stage": start, "end_stage": "rendering", "force": True,
        "options": {"text_segmentation": False}}).json()
    try:
        assert started.wait(5), calls
        current = client.get(f"/api/images/{page['id']}/regions").json()
        assert current[0]["id"] == old[0]["id"] and not current[0]["translated_text"]
    finally:
        release.set()
    done = stopped(client, task["id"], "COMPLETED")
    assert done["status"] == "COMPLETED", done
    assert bool(detection_calls) == (start == "detection")
    with SessionLocal() as db:
        workflow_id = db.scalar(select(TaskWorkflow.id).where(TaskWorkflow.task_id == task["id"]))
        stages = set(db.scalars(select(StageCheckpoint.stage).where(StageCheckpoint.workflow_id == workflow_id)))
    assert stages == {"ocr", "translation", "mask", "inpainting", "rendering"} | ({"detection"} if start == "detection" else set())
    assert undo(client, page["project_id"])["regions"][0]["id"] == old[0]["id"]


def test_full_pipeline_retry_reuses_detection_ocr_and_successful_sibling(client, providers, monkeypatch):  # noqa: F811
    page, _ = setup(client, [""])
    detect_calls = detector(monkeypatch)
    run, calls = workflow.run_stage, []

    def fail_once(stage, *args, **kwargs):
        calls.append(stage)
        if stage == "inpainting" and calls.count(stage) == 1:
            raise RuntimeError("one repair failure")
        return run(stage, *args, **kwargs)

    monkeypatch.setattr(workflow, "run_stage", fail_once)
    task = client.post(f"/api/images/{page['id']}/process", json={
        "start_stage": "detection", "end_stage": "rendering", "force": True}).json()
    stopped(client, task["id"], "FAILED")
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 202
    stopped(client, task["id"], "COMPLETED")
    assert len(detect_calls) == calls.count("detection") == calls.count("ocr") == calls.count("translation") == calls.count("mask") == 1
    assert calls.count("inpainting") == 2


def test_full_pipeline_target_frame_repair_remains_serial(client, providers, monkeypatch):  # noqa: F811
    page, _ = setup(client, [""])
    detector(monkeypatch)
    run, calls = workflow.run_stage, []

    def record(stage, *args, **kwargs):
        calls.append(stage)
        return run(stage, *args, **kwargs)

    monkeypatch.setattr(workflow, "run_stage", record)
    task = client.post(f"/api/images/{page['id']}/process", json={
        "start_stage": "detection", "end_stage": "rendering", "force": True,
        "options": {"repair_geometry": "translated", "text_segmentation": False}}).json()
    stopped(client, task["id"], "COMPLETED")
    assert calls == ["detection", "ocr", "translation", "mask", "inpainting", "rendering"]


@pytest.mark.parametrize('action', ['pause','cancel'])
def test_full_original_pipeline_drains_branches_and_never_publishes_after_stop(client, providers, monkeypatch, action):  # noqa: F811
    page, old = setup(client,[''])
    detector(monkeypatch)
    started, release = threading.Event(), threading.Event()
    run = workflow.run_stage

    def gate(stage,*args,**kwargs):
        if stage == 'inpainting':
            started.set()
            assert release.wait(8)
        return run(stage,*args,**kwargs)

    monkeypatch.setattr(workflow,'run_stage',gate)
    task=client.post(f"/api/images/{page['id']}/process",json={
        'start_stage':'detection','end_stage':'rendering','force':True}).json()
    try:
        assert started.wait(5)
        assert client.post(f"/api/tasks/{task['id']}/{action}").status_code==200
    finally:
        release.set()
    stopped(client,task['id'],'PAUSED' if action=='pause' else 'CANCELLED')
    actual=client.get(f"/api/images/{page['id']}/regions").json()
    assert actual[0]['id']==old[0]['id'] and not actual[0]['source_text']
    if action=='pause':
        assert client.post(f"/api/tasks/{task['id']}/resume").status_code==202
        stopped(client,task['id'],'COMPLETED')
        assert len(providers[1].calls)==1


@pytest.mark.parametrize("mode", ["snapshot", "previous_page"])
def test_full_batch_freezes_config_and_honors_context_mode(client, providers, monkeypatch, mode):  # noqa: F811
    from app.tasks import task_manager
    from test_pipeline import solid_page

    first, _ = setup(client, [""])
    client.post(f"/api/projects/{first['project_id']}/images",
        files={"files": ("second.png", solid_page(), "image/png")})
    client.patch(f"/api/projects/{first['project_id']}", json={"settings": {"translation_context_mode": mode}})
    detector(monkeypatch)
    dispatch, pending = task_manager.dispatch, []
    monkeypatch.setattr(task_manager, "dispatch", pending.append)
    tasks = client.post(f"/api/projects/{first['project_id']}/batch-process", json={
        "start_stage": "detection", "end_stage": "rendering", "force": True}).json()
    assert len(tasks) == 2 and tasks[0]["batch_id"] == tasks[1]["batch_id"] is not None
    with SessionLocal() as db:
        assert db.get(ProcessingBatch, tasks[0]["batch_id"]).context_mode == mode
    assert bool(tasks[1]["dependency_task_id"]) == (mode == "previous_page")
    monkeypatch.setattr(task_manager, "dispatch", dispatch)
    for identifier in reversed(pending):
        dispatch(identifier)
    for task in tasks:
        stopped(client, task["id"], "COMPLETED")
