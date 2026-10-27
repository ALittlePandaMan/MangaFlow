"""Batch admission, concurrent review and safe delayed correction over MCP."""
import io
import json
import time
from zipfile import ZipFile

import pytest
from app.core.database import SessionLocal
from app.models import (
    ImagePage,
    MCPOperation,
    MCPWorkflowPage,
    MCPWorkflowRun,
    ProcessingTask,
    StageCheckpoint,
    TaskWorkflow,
)
from app.pipeline.processor import PipelineProcessor
from app.services import mcp_workflows
from app.services.base import DetectionResult, OCRResult
from app.tasks import task_manager
from sqlalchemy import func, select
from test_checkpoint_pipeline import checkpoint_mode as checkpoint_mode
from test_mcp import mutation, page, rpc
from test_mcp_workflows import begin, evidence, ok, region, render_empty, review, state
from test_pipeline import solid_page
from test_translation_auto_ocr import providers as providers


@pytest.fixture(autouse=True)
def configured(client, monkeypatch):
    client.headers["Host"] = "localhost"
    monkeypatch.setattr(mcp_workflows, "blockers", lambda *args, **kwargs: [])
    yield
    # Fake queue rows must not be recovered by the following TestClient.
    with SessionLocal() as db:
        for row in db.scalars(select(ProcessingTask).where(ProcessingTask.status.in_(["QUEUED", "RUNNING", "PAUSED"]))):
            if not task_manager.is_active(row.id):
                row.status = "CANCELLED"
        db.commit()


def book(client, count=3):
    project, first = page(client)
    images = [first]
    if count > 1:
        images += client.post(f"/api/projects/{project['id']}/images", files=[
            ("files", (f"第{i+2}页.png", solid_page(), "image/png")) for i in range(count-1)]).json()
    return project, images


def status(client, run, **kwargs):
    return ok(rpc(client, "workflow_status", {"run_id": run, **kwargs}))


def advance(client, run, images, **kwargs):
    return rpc(client, "workflow_advance", mutation(run_id=run, image_ids=[image["id"] for image in images],
        expected_versions={image["id"]: state(client, image)["version"] for image in images}, **kwargs))


def text_done(client, image, task_id=None):
    with SessionLocal() as db:
        db.get(ImagePage, image["id"]).status = "TRANSLATED"
        if task_id:
            db.get(ProcessingTask, task_id).status = "COMPLETED"
        db.commit()
    region(client, image, source_text="こんにちは", translated_text="你好")


def test_eleven_pages_queue_once_then_review_finished_non_head_page(client, monkeypatch):
    project, images = book(client, 11)
    run = begin(client, project, legacy=False)
    dispatched = []
    monkeypatch.setattr(task_manager, "dispatch", dispatched.append)
    before = status(client, run)
    assert len(json.dumps(rpc(client, "workflow_status", {"run_id": run}), ensure_ascii=False).encode()) <= 6*1024
    call = before["next_steps"][0]
    assert call["tool"] == "workflow_advance" and len(call["arguments"]["image_ids"]) == 11
    response = ok(rpc(client, call["tool"], {**call["arguments"], "operation_id": "batch-11-first"}))
    assert len(response["tasks"]) == len(dispatched) == 11
    assert ok(rpc(client, call["tool"], {**call["arguments"], "operation_id": "batch-11-first"})) == response
    assert len(dispatched) == 11
    with SessionLocal() as db:
        tasks = list(db.scalars(select(ProcessingTask).where(ProcessingTask.project_id == project["id"])))
        assert all(task.payload["end_stage"] == "translation" and task.payload["batch_submission"] for task in tasks)
        task_id = next(task.id for task in tasks if task.image_id == images[5]["id"])
    text_done(client, images[5], task_id)
    current = status(client, run)
    assert current["current_page_id"] == images[5]["id"]
    assert current["next_steps"][0]["tool"] == "page_review_plan"
    version = state(client, images[5])["version"]
    ok(review(client, run, images[5], phase="preflight", disposition="defer",
        issues=[{"category": "ocr", "message": "Needs correction after batch", "status": "open"}]))
    assert state(client, images[5])["version"] == version
    assert status(client, run)["batch"]["correction_pending"] == 1
    assert rpc(client, "workflow_begin_correction", mutation(run_id=run, image_id=images[5]["id"],
        expected_versions={images[5]["id"]: version}))["isError"]
    assert rpc(client, "page_edit", mutation(image_id=images[5]["id"], workflow_run_id=run,
        update=[{"region_id": "R001", "patch": {"source_text": "premature"}}], expected_versions={images[5]["id"]: version}))["isError"]
    assert len(json.dumps(rpc(client, "workflow_status", {"run_id": run}), ensure_ascii=False).encode()) <= 6*1024


def test_batch_version_conflict_is_atomic_and_recovery_returns_all_tasks(client, monkeypatch):
    project, images = book(client)
    run = begin(client, project, legacy=False)
    monkeypatch.setattr(task_manager, "dispatch", lambda _: None)
    versions = {image["id"]: state(client, image)["version"] for image in images}
    args = mutation(run_id=run, image_ids=[image["id"] for image in images], expected_versions=versions)
    region(client, images[-1], source_text="manual", translated_text="")
    assert rpc(client, "workflow_advance", args)["isError"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ProcessingTask).where(ProcessingTask.project_id == project["id"])) == 0
    args = mutation(run_id=run, image_ids=[image["id"] for image in images],
        expected_versions={image["id"]: state(client, image)["version"] for image in images})
    original = ok(rpc(client, "workflow_advance", args))
    with SessionLocal() as db:
        row = db.get(MCPOperation, args["operation_id"])
        row.status = "UNKNOWN"
        row.result = {"_request": {"tool": "workflow_advance", "image_ids": args["image_ids"]},
            "_admission": {"task_ids": [task["id"] for task in original["tasks"]], "skipped": []}}
        db.commit()
    recovered = ok(rpc(client, "operation_status", {"operation_id": args["operation_id"]}))
    assert recovered["status"] == "COMPLETED"
    assert {task["id"] for task in recovered["result"]["tasks"]} == {task["id"] for task in original["tasks"]}


def test_deferred_review_requires_round_and_recheck_before_erasure(client, monkeypatch):
    project, images = book(client, 2)
    for image in images:
        text_done(client, image)
    run = begin(client, project, legacy=False)
    ok(review(client, run, images[0], phase="preflight", disposition="defer"))
    ok(review(client, run, images[1], phase="preflight", outcome="passed", selection={"mode": "all_eligible"}))
    monkeypatch.setattr(task_manager, "dispatch", lambda _: None)
    blocked = ok(advance(client, run, [images[0]]))
    assert not blocked["tasks"]
    for expected_round in (1, 2):
        result = ok(rpc(client, "workflow_begin_correction", mutation(run_id=run, image_id=images[0]["id"],
            expected_versions=state(client, images[0])["expected_versions"])))
        assert result["round"] == expected_round
        assert status(client, run)["pages"][0]["execution_stage"] == "correcting"
        ok(rpc(client, "page_edit", mutation(image_id=images[0]["id"], workflow_run_id=run,
            update=[{"region_id": "R001", "patch": {"translated_text": f"Corrected {expected_round}"}}],
            expected_versions=state(client, images[0])["expected_versions"])))
        ok(review(client, run, images[0], phase="preflight", disposition="defer"))
    assert rpc(client, "workflow_begin_correction", mutation(run_id=run, image_id=images[0]["id"],
        expected_versions=state(client, images[0])["expected_versions"]))["isError"]
    with SessionLocal() as db:
        assert db.get(MCPWorkflowRun, run).options["batch_phase"] == "output"
        assert db.scalar(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run,
            MCPWorkflowPage.image_id == images[0]["id"])).correction_rounds == 2
    queued = ok(advance(client, run, [images[1]]))
    with SessionLocal() as db:
        assert db.get(ProcessingTask, queued["tasks"][0]["id"]).payload["start_stage"] == "mask"


def test_defer_can_omit_selection_but_never_pass_or_erase(client):
    project, images = book(client, 1)
    text_done(client, images[0])
    run = begin(client, project, legacy=False)
    args = mutation(run_id=run, image_id=images[0]["id"], phase="preflight", outcome="issues", disposition="defer",
        checks={name: {"status": "issue", "reason": "Observed"} for name in (*mcp_workflows.PREFLIGHT_CHECKS, "translation")},
        note="Record only", evidence_ids=evidence(client, images[0], "preflight"),
        expected_versions=state(client, images[0])["expected_versions"])
    ok(rpc(client, "workflow_review_page", args))
    assert status(client, run)["next_steps"][0]["tool"] == "workflow_begin_correction"


@pytest.mark.parametrize("context_mode", ["snapshot", "previous_page"])
def test_real_queue_two_waves_review_export_and_preserved_manual_text(client, monkeypatch, context_mode):
    project, images = book(client, 3)
    assert client.patch(f"/api/projects/{project['id']}", json={"settings": {"translation_context_mode": context_mode}}).status_code == 200
    region(client, images[1], source_text="", translated_text="")
    manual = region(client, images[2], source_text="人工原文", translated_text="Keep manual translation")
    calls = []
    original = PipelineProcessor._provider

    class Detector:
        def detect(self, path):
            calls.append("detection")
            return [DetectionResult([[30, 40], [120, 40], [120, 140], [30, 140]], [30, 40, 90, 100], .99)]

    class OCR:
        def ensure_loaded(self):
            pass

        def recognize(self, *args, **kwargs):
            calls.append("ocr")
            return OCRResult("こんにちは", .99, "horizontal", {})

    class Translator:
        config = {}

        async def translate_regions(self, rows, **kwargs):
            calls.append(("translation", kwargs["context"]))
            return {key: "Hello" for key, _ in rows}

    class Fill:
        def inpaint(self, source, mask, output, kind):
            calls.append("inpainting")
            from PIL import Image
            with Image.open(source) as picture:
                picture.save(output)
            return output

    models = {"detection": Detector(), "ocr": OCR(), "translation": Translator(), "inpainting": Fill()}
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda self, kind, requested:
        (models[kind], "batch-test") if kind in models else original(self, kind, requested))
    run = begin(client, project, legacy=False)
    next_call = status(client, run)["next_steps"][0]
    assert len(next_call["arguments"]["image_ids"]) == 2  # Third page is already translated.
    first = ok(rpc(client, "workflow_advance", {**next_call["arguments"], "operation_id": f"text-{context_mode}"}))
    for task in first["tasks"]:
        done = ok(rpc(client, "task_wait", {"task_id": task["id"], "kind": "processing", "seconds": 20}))
        assert done["status"] == "COMPLETED", done
    with SessionLocal() as db:
        for image in images:
            page_row = db.get(ImagePage, image["id"])
            assert not page_row.clean_path and not page_row.rendered_path
            assert all(not row.pixel_mask_path for row in page_row.regions)
    assert "inpainting" not in calls
    if context_mode == "previous_page":
        contexts = [call[1].get("previous_page") for call in calls if isinstance(call, tuple)]
        assert any(value and value["page_id"] == images[0]["id"] and value["regions"] for value in contexts), contexts
    for image in images:
        ok(review(client, run, image, phase="preflight", outcome="passed", selection={"mode": "all_eligible"}))
    second = ok(advance(client, run, images))
    assert len(second["tasks"]) == 3
    for task in second["tasks"]:
        done = ok(rpc(client, "task_wait", {"task_id": task["id"], "kind": "processing", "seconds": 20}))
        assert done["status"] == "COMPLETED", done
    assert "inpainting" in calls
    assert state(client, images[2])["regions"][0]["translated_text"] == manual["translated_text"]
    for image in images:
        ok(review(client, run, image, outcome="passed"))
    ready = status(client, run)
    assert ready["next_steps"][0]["tool"] == "workflow_export"
    exported = ok(rpc(client, "workflow_export", mutation(run_id=run, kind="cbz")))
    job = ok(rpc(client, "task_wait", {"task_id": exported["job"]["id"], "kind": "file", "seconds": 20}))
    assert job["status"] == "COMPLETED", job
    downloaded = client.get(job["download_path"])
    assert downloaded.status_code == 200
    with ZipFile(io.BytesIO(downloaded.content)) as archive:
        assert archive.namelist() == ["0001.png", "0002.png", "0003.png"]


def test_large_book_queues_next_chunk_before_waiting_and_links_previous_page(client, monkeypatch):
    project, images = book(client, 22)
    client.patch(f"/api/projects/{project['id']}", json={"settings": {"translation_context_mode": "previous_page"}})
    run = begin(client, project, legacy=False)
    monkeypatch.setattr(task_manager, "dispatch", lambda _: None)
    # UI reordering must not change the run's frozen translation context.
    reordered = client.post(f"/api/projects/{project['id']}/images/order", json=[image["id"] for image in reversed(images)])
    assert reordered.status_code == 200, reordered.text
    first = status(client, run)["next_steps"][0]
    assert len(first["arguments"]["image_ids"]) == 20
    ok(rpc(client, "workflow_advance", {**first["arguments"], "operation_id": "chunk-1"}))
    second = status(client, run)["next_steps"][0]
    assert len(second["arguments"]["image_ids"]) == 2
    ok(rpc(client, "workflow_advance", {**second["arguments"], "operation_id": "chunk-2"}))
    with SessionLocal() as db:
        previous = db.scalar(select(ProcessingTask).where(ProcessingTask.image_id == images[19]["id"]))
        following = db.scalar(select(ProcessingTask).where(ProcessingTask.image_id == images[20]["id"]))
        assert following.batch_id != previous.batch_id
        assert following.dependency_task_id == previous.id


def test_agent_mode_writes_only_translations_while_other_pages_run(client, monkeypatch):
    project, images = book(client, 2)
    region(client, images[1], source_text="Recognized", translated_text="")
    run = begin(client, project, legacy=False, translation_mode="agent")
    monkeypatch.setattr(task_manager, "dispatch", lambda _: None)
    ok(advance(client, run, [images[0]]))
    assert status(client, run)["next_steps"][0]["tool"] == "page_edit"
    args = mutation(image_id=images[1]["id"], workflow_run_id=run,
        expected_versions=state(client, images[1])["expected_versions"],
        update=[{"region_id": "R001", "patch": {"translated_text": "Agent translation"}}])
    ok(rpc(client, "page_edit", args))
    assert rpc(client, "page_edit", mutation(image_id=images[1]["id"], workflow_run_id=run,
        expected_versions=state(client, images[1])["expected_versions"],
        update=[{"region_id": "R001", "patch": {"source_text": "premature correction"}}]))["isError"]


def test_text_batch_does_not_require_inpainting_or_rendering_configuration(client, monkeypatch):
    project, images = book(client, 1)
    run = begin(client, project, legacy=False)
    calls = []
    def missing_output(db, run, required=None):
        calls.append(required)
        return [{"kind": kind, "status": "missing"} for kind in {"inpainting", "rendering", "font"} & (required or set())]
    monkeypatch.setattr(mcp_workflows, "blockers", missing_output)
    monkeypatch.setattr(task_manager, "dispatch", lambda _: None)
    queued = ok(advance(client, run, images))
    assert len(queued["tasks"]) == 1
    assert all(not required or not required & {"inpainting", "rendering", "font"} for required in calls)


def test_output_queue_admission_precedes_review_and_deferred_final_blocks_export(client, monkeypatch):
    project, images = book(client, 2)
    with SessionLocal() as db:
        for image in images:
            db.get(ImagePage, image["id"]).status = "OCR_DONE"
        db.commit()
    run = begin(client, project, legacy=False)
    for image in images:
        ok(review(client, run, image, phase="preflight", outcome="passed"))
    monkeypatch.setattr(task_manager, "dispatch", lambda _: None)
    first = ok(advance(client, run, [images[0]]))["tasks"][0]
    with SessionLocal() as db:
        db.get(ProcessingTask, first["id"]).status = "COMPLETED"
        db.commit()
    render_empty(images[0])
    next_call = status(client, run)["next_steps"][0]
    assert next_call["tool"] == "workflow_advance"
    assert next_call["arguments"]["image_ids"] == [images[1]["id"]]
    second = ok(advance(client, run, [images[1]]))["tasks"][0]
    # Finished page remains reviewable while the second output is queued.
    assert status(client, run)["current_page_id"] == images[0]["id"]
    ok(review(client, run, images[0], disposition="defer"))
    with SessionLocal() as db:
        db.get(ProcessingTask, second["id"]).status = "COMPLETED"
        db.commit()
    render_empty(images[1])
    ok(review(client, run, images[1], outcome="passed"))
    assert rpc(client, "workflow_export", mutation(run_id=run))["isError"]
    ok(review(client, run, images[0], disposition="deliver_with_issues"))
    assert status(client, run)["next_steps"][0]["tool"] == "workflow_export"


def test_waiting_previous_translation_prepares_ocr_and_releases_worker(client, monkeypatch, providers):
    project, images = book(client, 2)
    for image in images:
        region(client, image, source_text="", translated_text="")
    client.patch(f"/api/projects/{project['id']}", json={"settings": {"translation_context_mode": "previous_page"}})
    run = begin(client, project, legacy=False)
    dispatch = task_manager.dispatch
    monkeypatch.setattr(task_manager, "dispatch", lambda _: None)
    tasks = ok(advance(client, run, images))["tasks"]
    previous, following = tasks
    try:
        dispatch(following["id"])
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                row = db.get(ProcessingTask, following["id"])
                checkpoints = list(db.scalars(select(StageCheckpoint.stage).join(TaskWorkflow).where(
                    TaskWorkflow.task_id == following["id"], StageCheckpoint.status == "SUCCEEDED")))
                prepared = row.status == "QUEUED" and "ocr" in checkpoints
            if prepared:
                break
            time.sleep(.03)
        assert prepared
        assert checkpoints == ["ocr"]  # No premature erasure or paid translation.
        assert state(client, images[1])["regions"][0]["source_text"] == ""  # Private checkpoint, not published.
        other_project, other = page(client)
        region(client, other, source_text="", translated_text="")
        other_run = begin(client, other_project, legacy=False)
        independent = ok(advance(client, other_run, [other]))["tasks"][0]
        dispatch(independent["id"])
        done = ok(rpc(client, "task_wait", {"task_id": independent["id"], "kind": "processing", "seconds": 20}))
        assert done["status"] == "COMPLETED", done
    finally:
        with SessionLocal() as db:
            db.get(ProcessingTask, previous["id"]).status = "CANCELLED"
            db.commit()
        task_manager.notify_dependency(previous["id"])
        done = ok(rpc(client, "task_wait", {"task_id": following["id"], "kind": "processing", "seconds": 20}))
        assert done["status"] == "FAILED", done
