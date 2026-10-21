from __future__ import annotations

import threading
import time

import pytest
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import (
    EditorHistoryEntry,
    ImagePage,
    ModelConfig,
    ProcessingTask,
    StageCheckpoint,
    TaskWorkflow,
    TextRegion,
)
from app.pipeline import workflow
from app.services.inpainting.providers import SimpleLaMaInpainter
from app.storage import get_storage
from app.tasks import task_manager
from sqlalchemy import func, select
from test_batch_repair_translate import batch
from test_editor_history import apply as undo
from test_pipeline import solid_page, wait_for_task
from test_translation_auto_ocr import providers, setup  # noqa: F401


def stopped(client, task_id, status):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] == status and not task_manager.is_active(task_id):
            return task
        time.sleep(.03)
    pytest.fail(f"Task did not stop as {status}: {task}")


@pytest.fixture(autouse=True)
def checkpoint_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "checkpoint_pipeline_enabled", True)
    monkeypatch.setattr(get_settings(), "parallel_repair_translation", True)
    monkeypatch.setattr(get_settings(), "text_segmentation_enabled", False)

    def fake_lama(provider):
        provider._device = "cpu"
        provider._model = lambda image, _mask: image.copy()

    monkeypatch.setattr(SimpleLaMaInpainter, "load", fake_lama)


def test_checkpointed_batch_publishes_once_and_undo_restores_whole_page(client, providers):  # noqa: F811
    ocr, translator, _ = providers
    page, regions = setup(client, ["", "已有原文"])
    original_history = client.get(f"/api/projects/{page['project_id']}/history").json()["undo_count"]
    task = batch(client, page["project_id"]).json()[0]
    done = wait_for_task(client, task["id"])
    assert done["status"] == "COMPLETED", done
    assert len(translator.calls) == 1 and len(ocr.calls) == 1
    rows = client.get(f"/api/images/{page['id']}/regions").json()
    assert [row["id"] for row in rows] == [row["id"] for row in regions]
    assert rows[0]["source_text"] == "识别的原文" and rows[0]["translated_text"] == "译文:识别的原文"
    finished = client.get(f"/api/images/{page['id']}").json()
    assert "/workflows/" in finished["rendered_url"]
    assert client.get(finished["rendered_url"]).status_code == 200
    head = client.get(f"/api/projects/{page['project_id']}/history").json()
    assert head["undo_count"] == original_history + 1
    restored = undo(client, page["project_id"])
    assert restored["regions"][0]["source_text"] == restored["regions"][0]["translated_text"] == ""
    redone = undo(client, page["project_id"], "redo")
    assert redone["regions"][0]["translated_text"] == rows[0]["translated_text"]
    assert client.get(redone["page"]["rendered_url"]).content == client.get(finished["rendered_url"]).content
    with SessionLocal() as db:
        run = db.scalar(select(TaskWorkflow).where(TaskWorkflow.task_id == task["id"]))
        assert run.status == "PUBLISHED"
        assert db.scalar(select(func.count()).select_from(EditorHistoryEntry).where(EditorHistoryEntry.task_id == task["id"])) == 1


@pytest.mark.parametrize("start,end,expected",[
    ("ocr","ocr",{"ocr"}),
    ("translation","translation",{"ocr","translation"}),
    ("mask","mask",{"mask"}),
    ("mask","inpainting",{"mask","inpainting"}),
    ("rendering","rendering",{"rendering"}),
])
def test_standalone_operations_publish_only_their_own_boundary(client,providers,start,end,expected):  # noqa: F811
    ocr,translator,_ = providers
    page,regions = setup(client,[""])
    previous = client.get(f"/api/projects/{page['project_id']}/history").json()["undo_count"]
    task = client.post(f"/api/images/{page['id']}/process",json={
        "start_stage":start,"end_stage":end,"force":True,"options":{"text_segmentation":False}}).json()
    done = stopped(client,task["id"],"COMPLETED")
    assert done["status"] == "COMPLETED",done
    current = client.get(f"/api/images/{page['id']}").json()
    if "rendering" not in expected:
        assert current["rendered_url"] is None
    if "inpainting" not in expected:
        assert current["clean_url"] is None
    assert bool(ocr.calls) == ("ocr" in expected)
    assert bool(translator.calls) == ("translation" in expected)
    with SessionLocal() as db:
        run = db.scalar(select(TaskWorkflow).where(TaskWorkflow.task_id==task["id"]))
        assert run is not None and run.status=="PUBLISHED"
        assert set(db.scalars(select(StageCheckpoint.stage).where(StageCheckpoint.workflow_id==run.id,
            StageCheckpoint.status=="SUCCEEDED"))) == expected
    assert client.get(f"/api/projects/{page['project_id']}/history").json()["undo_count"] == previous+1
    restored = undo(client,page["project_id"])
    assert restored["regions"][0]["id"]==regions[0]["id"]
    assert restored["regions"][0]["source_text"]==restored["regions"][0]["translated_text"]==""
    assert restored["page"]["clean_url"] is None and restored["page"]["rendered_url"] is None
    redone = undo(client,page["project_id"],"redo")
    assert redone["page"]["status"]==current["status"]


@pytest.mark.parametrize("scope",["single","multi"])
def test_scoped_translation_checkpoints_only_selected_blank_sources(client,providers,scope):  # noqa: F811
    ocr,translator,_ = providers
    page,regions = setup(client,["","","unselected"])
    payload = {"start_stage":"translation","end_stage":"translation","force":True}
    if scope=="single":
        task = client.post(f"/api/regions/{regions[0]['id']}/translate",json=payload).json()
        selected = [regions[0]]
    else:
        selected = regions[:2]
        payload["options"] = {"region_ids":[row["id"] for row in selected]}
        task = client.post(f"/api/images/{page['id']}/process",json=payload).json()
    done = stopped(client,task["id"],"COMPLETED")
    assert done["status"]=="COMPLETED",done
    assert len(ocr.calls)==len(selected)
    assert {key for key,_ in translator.calls[0][0]}=={row["region_key"] for row in selected}
    rows = client.get(f"/api/images/{page['id']}/regions").json()
    assert rows[-1]["source_text"]=="unselected" and rows[-1]["translated_text"]==""


def test_standalone_translation_save_failure_reuses_paid_result_and_prerequisite_ocr(client,providers,monkeypatch):  # noqa: F811
    ocr,translator,_ = providers
    page,_ = setup(client,[""])
    original = workflow.publish_workflow
    attempts = []

    def fail_once(*args,**kwargs):
        attempts.append(1)
        if len(attempts)==1:
            raise RuntimeError("simulated publication unavailable")
        return original(*args,**kwargs)

    monkeypatch.setattr(workflow,"publish_workflow",fail_once)
    task = client.post(f"/api/images/{page['id']}/process",json={
        "start_stage":"translation","end_stage":"translation","force":True}).json()
    failed = stopped(client,task["id"],"FAILED")
    assert "simulated publication" in failed["error_message"]
    assert len(ocr.calls)==len(translator.calls)==1
    assert not client.get(f"/api/images/{page['id']}/regions").json()[0]["source_text"]
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code==202
    done = stopped(client,task["id"],"COMPLETED")
    assert done["status"]=="COMPLETED",done
    assert len(ocr.calls)==len(translator.calls)==1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TaskWorkflow).where(TaskWorkflow.task_id==task["id"]))==1
        assert db.scalar(select(func.count()).select_from(EditorHistoryEntry).where(EditorHistoryEntry.task_id==task["id"]))==1


def test_cancelled_native_ocr_retry_reuses_completed_crops_and_publishes_one_undo_step(client,monkeypatch):
    from app.pipeline.processor import PipelineProcessor
    from app.services.base import OCRResult

    page,regions = setup(client,["","",""])
    started,release = threading.Event(),threading.Event()
    calls = []

    class NativeOCR:
        config = {}

        def ensure_loaded(self):
            pass

        def recognize(self,source,box,orientation,padding):
            calls.append(box[0])
            if len(calls)==2:
                started.set()
                assert release.wait(5)
            return OCRResult(f"native-{box[0]}",.99,orientation)

    provider = NativeOCR()
    original = PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor,"_provider",lambda self,kind,name:
        (provider,"native-checkpoint-test") if kind=="ocr" else original(self,kind,name))
    task = client.post(f"/api/images/{page['id']}/process",json={"start_stage":"ocr","end_stage":"ocr","force":True}).json()
    try:
        assert started.wait(5)
        assert client.post(f"/api/tasks/{task['id']}/cancel").status_code==200
    finally:
        release.set()
    stopped(client,task["id"],"CANCELLED")
    assert len(calls)==2
    assert all(not row["source_text"] for row in client.get(f"/api/images/{page['id']}/regions").json())
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code==202
    done = stopped(client,task["id"],"COMPLETED")
    assert done["status"]=="COMPLETED",done
    assert len(calls)==3 and sorted(calls)==sorted(row["bbox"][0] for row in regions)
    assert all(row["source_text"].startswith("native-") for row in client.get(f"/api/images/{page['id']}/regions").json())
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(EditorHistoryEntry).where(EditorHistoryEntry.task_id==task["id"]))==1
    assert all(not row["source_text"] for row in undo(client,page["project_id"])["regions"])


def test_partial_paid_translation_survives_acknowledged_task_retry_without_rebilling_successes(client,providers,monkeypatch):  # noqa: F811
    import json

    import httpx
    from app.pipeline.processor import PipelineProcessor
    from app.services.translation.providers import OpenAICompatibleTranslator

    page,_ = setup(client,["first source","second source"])
    calls = []

    def handler(request):
        body = json.loads(request.content)
        required = json.loads(body["messages"][1]["content"].split("\n\n")[0])["required_output"]
        calls.append(set(required))
        translated = {key:"translated" for key in required if key=="R001" or len(calls)>1}
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps(translated)}}]})

    http_client = httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kwargs:http_client(transport=httpx.MockTransport(handler),**kwargs))
    config = {"base_url":"https://partial-task.invalid/v1","api_key":"test-key","model":"test","retries":0,"requests_per_minute":6000}
    translator = OpenAICompatibleTranslator(config)
    original = PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor,"_provider",lambda self,kind,name:
        (translator,"partial-paid-test") if kind=="translation" else original(self,kind,name))
    with SessionLocal() as db:
        model = ModelConfig(kind="translation",name=f"partial-{page['id']}",provider="openai-compatible",
            enabled=True,is_default=True,config={})
        db.add(model)
        db.commit()
        model_id = model.id
    try:
        task = client.post(f"/api/images/{page['id']}/process",json={
            "start_stage":"translation","end_stage":"translation","force":True}).json()
        failed = stopped(client,task["id"],"FAILED")
        assert failed["error_kind"]=="unknown_cloud_outcome",failed
        assert calls==[{"R001","R002"}]
        assert client.post(f"/api/tasks/{task['id']}/retry").status_code==409
        assert all(not row["translated_text"] for row in client.get(f"/api/images/{page['id']}/regions").json())
        private = list(get_storage().page_dir(page["project_id"],page["id"]).glob("workflows/*/.requests/translation/*/*.json"))
        assert len(private)==2  # Validated partial IDs plus the privately received response.
        assert all(client.get(f"/media/{get_storage().relative(path)}").status_code==404 for path in private)
        retry = client.post(f"/api/tasks/{task['id']}/retry?allow_unknown_cloud_retry=true")
        assert retry.status_code==202,retry.text
        done = stopped(client,task["id"],"COMPLETED")
        assert done["status"]=="COMPLETED",done
        assert calls==[{"R001","R002"},{"R002"}]
        assert all(row["translated_text"]=="translated" for row in client.get(f"/api/images/{page['id']}/regions").json())
        assert all(not path.exists() for path in private)  # Whole stage is durable, including response replay.
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(EditorHistoryEntry).where(EditorHistoryEntry.task_id==task["id"]))==1
    finally:
        with SessionLocal() as db:
            db.delete(db.get(ModelConfig,model_id))
            db.commit()


def test_refused_translation_retains_repair_and_explicit_retry_reuses_it(client, providers, monkeypatch):  # noqa: F811
    from app.services.translation.providers import OpenAICompatibleTranslator

    _, translator, _ = providers
    page, _ = setup(client, ["source"])
    calls, repaired = [], []

    async def translate(rows, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return OpenAICompatibleTranslator._extract_content({"choices": [
                {"finish_reason": "prohibited_content", "message": {"content": None}},
            ]}, "openai")
        return {key: "translated" for key, _ in rows}

    monkeypatch.setattr(translator, "translate_regions", translate)
    run_stage = workflow.run_stage
    def stage(name, *args, **kwargs):
        if name == "inpainting":
            repaired.append(1)
        return run_stage(name, *args, **kwargs)
    monkeypatch.setattr(workflow, "run_stage", stage)
    with SessionLocal() as db:
        model = ModelConfig(kind="translation", name=f"refusal-{page['id']}", provider="openai-compatible",
                            enabled=True, is_default=True, config={})
        db.add(model)
        db.commit()
        model_id = model.id
    try:
        task = batch(client, page["project_id"]).json()[0]
        failed = stopped(client, task["id"], "FAILED")
        assert calls == repaired == [1]
        assert "prohibited_content" in failed["error_message"]
        assert "背景修复已完成并保留检查点" in failed["error_message"]
        assert client.get(f"/api/images/{page['id']}").json()["clean_url"] is None
        assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 409
        retry = client.post(f"/api/tasks/{task['id']}/retry?allow_unknown_cloud_retry=true")
        assert retry.status_code == 202, retry.text
        done = stopped(client, task["id"], "COMPLETED")
        assert done["status"] == "COMPLETED", done
        assert calls == [1, 1] and repaired == [1]
        finished = client.get(f"/api/images/{page['id']}").json()
        assert finished["clean_url"] and finished["rendered_url"]
    finally:
        with SessionLocal() as db:
            db.delete(db.get(ModelConfig, model_id))
            db.commit()


def test_redetection_is_atomic_retries_detection_checkpoint_but_new_request_redetects(client,providers,monkeypatch):  # noqa: F811
    from app.pipeline.processor import PipelineProcessor
    from app.services.base import DetectionResult

    ocr,_,_ = providers
    page,regions = setup(client,["old source"])
    detect_calls = []

    class Detector:
        def detect(self,path):
            detect_calls.append(1)
            return [DetectionResult([[100,10],[140,10],[140,80],[100,80]], [100,10,40,70], .99)]

    provider = PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor,"_provider",lambda self,kind,name:
        (Detector(),"fake-detection") if kind=="detection" else provider(self,kind,name))
    original = workflow.run_stage
    ocr_attempts = []

    def fail_first_ocr(stage,*args,**kwargs):
        if stage=="ocr":
            ocr_attempts.append(1)
            if len(ocr_attempts)==1:
                raise RuntimeError("OCR unavailable before inference")
        return original(stage,*args,**kwargs)

    monkeypatch.setattr(workflow,"run_stage",fail_first_ocr)
    payload = {"start_stage":"detection","end_stage":"ocr","force":True}
    task = client.post(f"/api/images/{page['id']}/process",json=payload).json()
    assert stopped(client,task["id"],"FAILED")["status"]=="FAILED"
    rows = client.get(f"/api/images/{page['id']}/regions").json()
    assert rows[0]["id"]==regions[0]["id"] and rows[0]["source_text"]=="old source"
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code==202
    done = stopped(client,task["id"],"COMPLETED")
    assert done["status"]=="COMPLETED",done
    rows = client.get(f"/api/images/{page['id']}/regions").json()
    assert len(detect_calls)==len(ocr.calls)==1
    assert rows[0]["id"]!=regions[0]["id"] and rows[0]["source_text"]=="识别的原文"
    fresh = client.post(f"/api/images/{page['id']}/process",json=payload).json()
    assert stopped(client,fresh["id"],"COMPLETED")["status"]=="COMPLETED"
    assert len(detect_calls)==len(ocr.calls)==2
    assert client.get(f"/api/images/{page['id']}/regions").json()[0]["id"]!=rows[0]["id"]
    undo(client,page["project_id"])
    restored = undo(client,page["project_id"])
    assert restored["regions"][0]["id"]==regions[0]["id"] and restored["regions"][0]["source_text"]=="old source"


@pytest.mark.parametrize("checkpoints",[False,True])
def test_named_translation_override_does_not_override_repair_or_render_models(client,providers,monkeypatch,checkpoints):  # noqa: F811
    monkeypatch.setattr(get_settings(),"checkpoint_pipeline_enabled",checkpoints)
    _,translator,lookups = providers
    page,_ = setup(client,["existing text"])
    task = batch(client,page["project_id"],provider="test-translator").json()[0]
    done = stopped(client,task["id"],"COMPLETED")
    assert done["status"] == "COMPLETED" and len(translator.calls) == 1
    assert ("translation","test-translator") in lookups
    assert all(requested is None for kind,requested in lookups if kind in {"inpainting","rendering"})


def test_parallel_failure_preserves_page_and_retries_only_failed_branch(client, providers, monkeypatch):  # noqa: F811
    _, translator, _ = providers
    page, _ = setup(client, ["已有原文"])
    original = client.get(f"/api/images/{page['id']}").json()
    calls = []
    run = workflow.run_stage

    def fail_once(stage, *args, **kwargs):
        calls.append(stage)
        if stage == "inpainting" and calls.count(stage) == 1:
            raise ValueError("simulated repair failure")
        return run(stage, *args, **kwargs)

    monkeypatch.setattr(workflow, "run_stage", fail_once)
    task = batch(client, page["project_id"]).json()[0]
    failed = wait_for_task(client, task["id"])
    assert failed["status"] == "FAILED" and "simulated repair" in failed["error_message"], failed
    assert len(translator.calls) == 1
    current = client.get(f"/api/images/{page['id']}").json()
    for key in ("clean_url", "rendered_url", "text_layer_url", "status"):
        assert current[key] == original[key]
    assert not client.get(f"/api/images/{page['id']}/regions").json()[0]["translated_text"]
    # Rollback switches must not silently discard existing paid checkpoints.
    monkeypatch.setattr(get_settings(), "checkpoint_pipeline_enabled", False)
    retry = client.post(f"/api/tasks/{task['id']}/retry")
    assert retry.status_code == 202, retry.text
    completed = wait_for_task(client, task["id"])
    assert completed["status"] == "COMPLETED", completed
    assert len(translator.calls) == 1
    assert calls.count("ocr") == calls.count("mask") == calls.count("translation") == 1
    assert calls.count("inpainting") == 2


def test_translated_geometry_remains_serial_and_initializes_angle_before_masks(client, providers, monkeypatch):  # noqa: F811
    from app.pipeline.processor import PipelineProcessor

    page, _ = setup(client, ["已有原文"])
    stages, angles = [], []
    original, initialize = workflow.run_stage, PipelineProcessor._initialize_text_rotation

    def record_angle(processor, *args, **kwargs):
        angles.append(processor.stage)
        return initialize(processor, *args, **kwargs)

    def ordered(stage, *args, **kwargs):
        stages.append(stage)
        if stage == "mask":
            assert "translation" in angles
        return original(stage, *args, **kwargs)

    monkeypatch.setattr(PipelineProcessor, "_initialize_text_rotation", record_angle)
    monkeypatch.setattr(workflow, "run_stage", ordered)
    task = batch(client, page["project_id"], options={"rebuild_clean": True, "repair_geometry": "translated", "text_segmentation": False}).json()[0]
    done = wait_for_task(client, task["id"])
    assert done["status"] == "COMPLETED", done
    assert stages == ["ocr", "translation", "mask", "inpainting", "rendering"]


def test_real_branches_overlap_and_published_page_is_unchanged_until_join(client, providers, monkeypatch):  # noqa: F811
    page, _ = setup(client, ["已有原文"])
    both = threading.Barrier(2)
    started, release = threading.Event(), threading.Event()
    original = workflow.run_stage

    def gated(stage, *args, **kwargs):
        if stage in {"translation", "mask"}:
            both.wait(5)
            started.set()
            assert release.wait(5)
        return original(stage, *args, **kwargs)

    monkeypatch.setattr(workflow, "run_stage", gated)
    task = batch(client, page["project_id"]).json()[0]
    try:
        assert started.wait(5)
        assert not client.get(f"/api/images/{page['id']}/regions").json()[0]["translated_text"]
        assert client.get(f"/api/images/{page['id']}").json()["rendered_url"] is None
    finally:
        release.set()
    result = wait_for_task(client, task["id"])
    assert result["status"] == "COMPLETED", result


def test_cancelled_native_result_cannot_overwrite_page(client, providers, monkeypatch):  # noqa: F811
    page, _ = setup(client, ["已有原文"])
    started, release = threading.Event(), threading.Event()
    original = workflow.run_stage

    def blocked(stage, *args, **kwargs):
        if stage == "inpainting":
            started.set()
            assert release.wait(5)
        return original(stage, *args, **kwargs)

    monkeypatch.setattr(workflow, "run_stage", blocked)
    task = batch(client, page["project_id"]).json()[0]
    try:
        assert started.wait(5)
        assert client.post(f"/api/tasks/{task['id']}/cancel").status_code == 200
    finally:
        release.set()
    cancelled = wait_for_task(client, task["id"])
    assert cancelled["status"] == "CANCELLED", cancelled
    assert not client.get(f"/api/images/{page['id']}/regions").json()[0]["translated_text"]
    with SessionLocal() as db:
        assert db.get(ImagePage, page["id"]).rendered_path is None
        assert db.get(ProcessingTask, task["id"]).execution_generation == 1
        run = db.scalar(select(TaskWorkflow).where(TaskWorkflow.task_id == task["id"]))
        assert run.status != "PUBLISHED"
        assert not db.scalar(select(StageCheckpoint.id).where(StageCheckpoint.workflow_id == run.id,
            StageCheckpoint.stage == "inpainting", StageCheckpoint.status == "SUCCEEDED"))


def test_nonforced_existing_translation_and_locked_regions_skip_prerequisite_ocr(client, providers):  # noqa: F811
    ocr, translator, _ = providers
    page, regions = setup(client, ["", "", "已有原文"])
    assert client.patch(f"/api/regions/{regions[0]['id']}", json={"translated_text": "人工译文"}).status_code == 200
    assert client.patch(f"/api/regions/{regions[1]['id']}", json={"locked": True}).status_code == 200
    task = batch(client, page["project_id"], force=False).json()[0]
    result = wait_for_task(client, task["id"])
    assert result["status"] == "COMPLETED", result
    assert not ocr.calls
    assert translator.calls[0][0] == [(regions[2]["region_key"], "已有原文")]
    rows = client.get(f"/api/images/{page['id']}/regions").json()
    assert rows[0]["translated_text"] == "人工译文" and rows[0]["source_text"] == ""


def test_parallel_reprocessing_preserves_previous_output_until_success(client, providers, monkeypatch):  # noqa: F811
    page, _ = setup(client, ["已有原文"])
    first = batch(client, page["project_id"]).json()[0]
    assert wait_for_task(client, first["id"])["status"] == "COMPLETED"
    stopped(client, first["id"], "COMPLETED")
    old = client.get(f"/api/images/{page['id']}").json()
    old_bytes = client.get(old["rendered_url"]).content
    original = workflow.run_stage

    def check_old(stage, *args, **kwargs):
        assert client.get(f"/api/images/{page['id']}").json()["rendered_url"] == old["rendered_url"]
        assert client.get(old["rendered_url"]).content == old_bytes
        return original(stage, *args, **kwargs)

    monkeypatch.setattr(workflow, "run_stage", check_old)
    second = batch(client, page["project_id"]).json()[0]
    done = wait_for_task(client, second["id"])
    assert done["status"] == "COMPLETED", done
    assert client.get(old["rendered_url"]).content == old_bytes


def test_pause_drains_cloud_result_and_resume_does_not_translate_again(client, providers, monkeypatch):  # noqa: F811
    _, translator, _ = providers
    page, _ = setup(client, ["已有原文"])
    started, release = threading.Event(), threading.Event()
    original = workflow.run_stage

    def gated(stage, *args, **kwargs):
        if stage == "translation":
            started.set()
            assert release.wait(5)
        return original(stage, *args, **kwargs)

    monkeypatch.setattr(workflow, "run_stage", gated)
    task = batch(client, page["project_id"]).json()[0]
    try:
        assert started.wait(5)
        assert client.post(f"/api/tasks/{task['id']}/pause").status_code == 200
    finally:
        release.set()
    stopped(client, task["id"], "PAUSED")
    assert len(translator.calls) == 1
    assert not client.get(f"/api/images/{page['id']}/regions").json()[0]["translated_text"]
    with SessionLocal() as db:
        run = db.scalar(select(TaskWorkflow).where(TaskWorkflow.task_id == task["id"]))
        assert db.scalar(select(StageCheckpoint.status).where(StageCheckpoint.workflow_id == run.id,
            StageCheckpoint.stage == "translation")) == "SUCCEEDED"
    resumed = client.post(f"/api/tasks/{task['id']}/resume")
    assert resumed.status_code == 202, resumed.text
    done = wait_for_task(client, task["id"])
    assert done["status"] == "COMPLETED", done
    assert len(translator.calls) == 1


def test_stale_page_rejects_publication_and_does_not_capture_other_edit_in_undo(client, providers, monkeypatch):  # noqa: F811
    page, regions = setup(client, ["已有原文"])
    original = workflow.publish_workflow

    def edit_before_publish(*args, **kwargs):
        with SessionLocal() as db:
            db.get(TextRegion, regions[0]["id"]).source_text = "新的用户编辑"
            db.commit()
        return original(*args, **kwargs)

    monkeypatch.setattr(workflow, "publish_workflow", edit_before_publish)
    task = batch(client, page["project_id"]).json()[0]
    stopped(client, task["id"], "FAILED")
    row = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert row["source_text"] == "新的用户编辑" and not row["translated_text"]
    assert client.get(f"/api/images/{page['id']}").json()["rendered_url"] is None
    with SessionLocal() as db:
        assert not db.scalar(select(EditorHistoryEntry.id).where(EditorHistoryEntry.task_id == task["id"]))


def test_unpublished_and_unmanifested_workflow_images_are_private(client, providers, monkeypatch):  # noqa: F811
    page, _ = setup(client, ["已有原文"])
    original = workflow.publish_workflow

    def check_private(store, lease, initial, result, *args, **kwargs):
        path = result["page"]["rendered_path"]
        for method in (client.get, client.head):
            assert method(f"/media/{path}").status_code == 404
        return original(store, lease, initial, result, *args, **kwargs)

    monkeypatch.setattr(workflow, "publish_workflow", check_private)
    task = batch(client, page["project_id"]).json()[0]
    result = wait_for_task(client, task["id"])
    assert result["status"] == "COMPLETED", result
    with SessionLocal() as db:
        published = db.get(ImagePage, page["id"]).rendered_path
    storage = get_storage()
    unlisted = storage.absolute(published).with_name("not-a-published-result.png")
    unlisted.write_bytes(b"private stage output")
    assert client.get(f"/media/{published}").status_code == 200
    assert client.get(f"/media/{storage.relative(unlisted)}").status_code == 404
    link = storage.page_dir(page["project_id"], page["id"]) / "original" / "hidden-link.png"
    link.symlink_to(unlisted)
    assert client.get(f"/media/{storage.relative(link)}").status_code == 404


def test_unknown_cloud_failure_requires_confirmation_even_for_value_error(client, providers, monkeypatch):  # noqa: F811
    page, _ = setup(client, ["已有原文"])
    # The test provider intercepts actual I/O. This config exercises cloud
    # classification for a named, non-default translation override.
    provider_name = "selected-cloud-" + page["id"]
    with SessionLocal() as db:
        db.add(ModelConfig(kind="translation", name=provider_name, provider="openai-compatible", is_default=False))
        db.commit()
    original = workflow.run_stage

    def invalid_response(stage, *args, **kwargs):
        if stage == "translation":
            raise ValueError("Paid response had invalid IDs")
        return original(stage, *args, **kwargs)

    monkeypatch.setattr(workflow, "run_stage", invalid_response)
    task = batch(client, page["project_id"], provider=provider_name).json()[0]
    stopped(client, task["id"], "FAILED")
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 409
    monkeypatch.setattr(task_manager, "dispatch", lambda _id: None)
    with SessionLocal() as db:
        db.get(ProcessingTask, task["id"]).status = "PAUSED"
        db.commit()
    assert client.post(f"/api/tasks/{task['id']}/resume").status_code == 409
    acknowledged = client.post(f"/api/tasks/{task['id']}/resume?allow_unknown_cloud_retry=true")
    assert acknowledged.status_code == 202, acknowledged.text
    # Do not leave a queued fake task for the next TestClient startup.
    with SessionLocal() as db:
        db.get(ProcessingTask, task["id"]).status = "CANCELLED"
        db.commit()


def test_publication_failure_rolls_back_page_and_history_and_reuses_all_results(client, providers, monkeypatch):  # noqa: F811
    from app.services import workflow_publish

    _, translator, _ = providers
    page, _ = setup(client, ["已有原文"])
    before = client.get(f"/api/projects/{page['project_id']}/history").json()["undo_count"]
    finish = workflow_publish._finish_entry
    attempts = []

    def fail_after_changes(*args, **kwargs):
        finish(*args, **kwargs)
        attempts.append(True)
        if len(attempts) == 1:
            raise ValueError("simulated publication commit failure")

    monkeypatch.setattr(workflow_publish, "_finish_entry", fail_after_changes)
    task = batch(client, page["project_id"]).json()[0]
    stopped(client, task["id"], "FAILED")
    assert client.get(f"/api/images/{page['id']}").json()["rendered_url"] is None
    assert not client.get(f"/api/images/{page['id']}/regions").json()[0]["translated_text"]
    assert client.get(f"/api/projects/{page['project_id']}/history").json()["undo_count"] == before

    def no_recompute(*_args, **_kwargs):
        pytest.fail("A successful stage was recomputed after a publication-only failure")

    monkeypatch.setattr(workflow, "run_stage", no_recompute)
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 202
    done = wait_for_task(client, task["id"])
    assert done["status"] == "COMPLETED", done
    assert len(translator.calls) == 1
    assert client.get(f"/api/projects/{page['project_id']}/history").json()["undo_count"] == before + 1


def test_retry_keeps_frozen_previous_page_context_after_sibling_changes(client, providers, monkeypatch):  # noqa: F811
    _, translator, _ = providers
    previous, previous_regions = setup(client, ["上一页原文"])
    page = client.post(f"/api/projects/{previous['project_id']}/images",
        files={"files": ("second.png", solid_page(), "image/png")}).json()[0]
    assert client.post(f"/api/images/{page['id']}/regions", json={
        "polygon": [[10, 10], [50, 10], [50, 90], [10, 90]], "source_text": "当前页原文",
    }).status_code == 201
    original = workflow.run_stage

    def fail_repair(stage, *args, **kwargs):
        if stage == "inpainting":
            raise ValueError("temporary repair failure")
        return original(stage, *args, **kwargs)

    monkeypatch.setattr(workflow, "run_stage", fail_repair)
    task = batch(client, page["project_id"], image_ids=[page["id"]]).json()[0]
    stopped(client, task["id"], "FAILED")
    assert len(translator.calls) == 1
    assert translator.calls[0][1]["previous_page"]["regions"][0]["source"] == "上一页原文"
    assert client.patch(f"/api/regions/{previous_regions[0]['id']}", json={"source_text": "上一页后来更新了"}).status_code == 200
    monkeypatch.setattr(workflow, "run_stage", original)
    assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 202
    done = wait_for_task(client, task["id"])
    assert done["status"] == "COMPLETED", done
    assert len(translator.calls) == 1


def test_restart_marks_cloud_unknown_without_resetting_published_page(client):
    page, _ = setup(client, ["原文"])
    with SessionLocal() as db:
        published = db.get(ImagePage, page["id"])
        published.status = "COMPLETED"
        task = ProcessingTask(project_id=page["project_id"], image_id=page["id"], task_type="full",
            status="RUNNING", current_stage="translation", execution_generation=1)
        db.add(task)
        db.flush()
        run = TaskWorkflow(task_id=task.id, generation=1, input_fingerprint="0" * 64,
            request_fingerprint="1" * 64, input_state={}, policy={})
        db.add(run)
        db.flush()
        stage = StageCheckpoint(workflow_id=run.id, stage="translation", attempt=1,
            generation=1, input_fingerprint="2" * 64, cloud_request=True)
        db.add(stage)
        db.commit()
        task_id, stage_id = task.id, stage.id
    task_manager.recover_interrupted()
    with SessionLocal() as db:
        assert db.get(ProcessingTask, task_id).status == "FAILED"
        assert db.get(StageCheckpoint, stage_id).status == "UNKNOWN"
        assert db.get(ImagePage, page["id"]).status == "COMPLETED"
        assert db.get(ImagePage, page["id"]).error_message is None
