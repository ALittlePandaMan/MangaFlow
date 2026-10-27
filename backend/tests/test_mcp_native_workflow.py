"""Native-tool recipes: published-state advice and backend-only image crops."""
import io
from datetime import timedelta

import numpy as np
import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, MCPAsset, ProcessingTask, TextRegion
from app.models.entities import utcnow
from app.services import mcp_workflows
from PIL import Image
from sqlalchemy import func, select
from test_mcp import page, rpc
from test_mcp_workflows import begin, ok, region, review, state


@pytest.fixture(autouse=True)
def configuration(client, monkeypatch):
    client.headers["Host"] = "localhost"
    monkeypatch.setattr(mcp_workflows, "blockers", lambda db, run, **kwargs: [])


def status_page(client, run):
    return ok(rpc(client, "workflow_status", {"run_id": run}))["pages"][0]


def image_from(client, project, picture):
    data = io.BytesIO()
    picture.save(data, "PNG")
    return client.post(f"/api/projects/{project['id']}/images", files={"files": ("漫画.png", data.getvalue())}).json()[0]


def planned_evidence(client, image, phase):
    args = {"image_id": image["id"], "phase": phase, "expected_version": state(client, image)["version"]}
    identifiers = []
    for _ in range(8):
        plan = ok(rpc(client, "page_review_plan", {**args, "evidence_ids": identifiers}))
        assert not plan["blockers"], plan
        assert not plan["visual_review_performed"]
        if plan["evidence_ready"]:
            return identifiers, plan
        batch = list(plan["requests"])
        while plan["next_offset"] is not None:
            plan = ok(rpc(client, "page_review_plan", {**args, "evidence_ids": identifiers, "offset": plan["next_offset"]}))
            batch.extend(plan["requests"])
        assert batch
        for request in batch:
            response = rpc(client, request["tool"], request["arguments"])
            assert any(block["type"] == "image" for block in response["content"])
            identifiers.append(ok(response)["preview_id"])
    pytest.fail("Review image planning did not converge")




def test_planner_is_read_only_and_long_page_crops_satisfy_actual_review(client):
    project, _ = page(client)
    image = image_from(client, project, Image.new("RGB", (400, 7000), "white"))
    run = begin(client, project, image_ids=[image["id"]])
    before = state(client, image)["version"]
    with SessionLocal() as db:
        asset_count = db.scalar(select(func.count()).select_from(MCPAsset))
    first = ok(rpc(client, "page_review_plan", {"image_id": image["id"], "phase": "preflight", "expected_version": before, "limit": 1}))
    assert first["next_offset"] is None and first["request_count"] == 1
    assert first["requests"][0]["arguments"]["kind"] == "annotated"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(MCPAsset)) == asset_count
    identifiers, plan = planned_evidence(client, image, "preflight")
    assert plan["readable_coverage"]["original"] >= .98
    assert len(identifiers) > 2
    ok(review(client, run, image, phase="preflight", outcome="passed", evidence_ids=identifiers))
    assert state(client, image)["version"] == before


def test_plan_adapts_to_real_png_compression_without_client_image_processing(client):
    project, _ = page(client)
    noise = np.random.default_rng(123).integers(0, 256, (1600, 1600, 3), dtype=np.uint8)
    image = image_from(client, project, Image.fromarray(noise))
    identifiers, plan = planned_evidence(client, image, "preflight")
    assert len(identifiers) > 2  # The full PNG was reduced by the actual 2 MiB cap.
    assert plan["readable_coverage"]["original"] >= .98
    assert state(client, image)["version"] == plan["version"]


@pytest.mark.parametrize("failure", ["version", "other_page", "expired", "missing", "limit"])
def test_plan_rejects_stale_wrong_or_excess_evidence(client, failure):
    project, image = page(client)
    version = state(client, image)["version"]
    preview = ok(rpc(client, "page_preview", {"image_id": image["id"]}))
    identifiers = [preview["preview_id"]]
    if failure == "version":
        version = "obsolete"
    elif failure == "other_page":
        _, other = page(client)
        identifiers = [ok(rpc(client, "page_preview", {"image_id": other["id"]}))["preview_id"]]
    elif failure == "expired":
        with SessionLocal() as db:
            db.get(MCPAsset, preview["preview_id"]).created_at = utcnow()-timedelta(hours=25)
            db.commit()
    elif failure == "missing":
        identifiers = ["missing"]
    else:
        identifiers = [f"preview-{i}" for i in range(257)]
    assert rpc(client, "page_review_plan", {"image_id": image["id"], "phase": "preflight",
        "expected_version": version, "evidence_ids": identifiers})["isError"]


def test_extreme_page_cannot_silently_exceed_evidence_budget(client):
    project, image = page(client)
    preview = ok(rpc(client, "page_preview", {"image_id": image["id"]}))
    with SessionLocal() as db:
        row = db.get(ImagePage, image["id"])
        row.width, row.height = 1, 1_000_000
        db.commit()
    version = state(client, image)["version"]
    # Metadata-only test of planning limits; no million-pixel image is allocated.
    with SessionLocal() as db:
        asset = db.get(MCPAsset, preview["preview_id"])
        asset.evidence = {**asset.evidence, "version": version, "crop": [0, 0, 1, 1_000_000],
            "original_pixels_per_display_pixel": [1, 625]}
        db.commit()
    plan = ok(rpc(client, "page_review_plan", {"image_id": image["id"], "phase": "preflight",
        "expected_version": version, "evidence_ids": [preview["preview_id"]]}))
    assert not plan["evidence_ready"] and not plan["requests"]
    assert any(item["code"] == "evidence_limit" for item in plan["blockers"])


def test_new_zero_text_and_manual_pages_receive_distinct_next_steps(client):
    project, image = page(client)
    run = begin(client, project)
    initial = status_page(client, run)
    assert initial["execution_stage"] == "detection"
    assert initial["next_steps"][0]["tool"] == "workflow_advance"
    with SessionLocal() as db:
        db.get(ImagePage, image["id"]).status = "OCR_DONE"
        db.commit()
    assert status_page(client, run)["execution_stage"] == "preflight_review"
    ok(review(client, run, image, phase="preflight", outcome="passed", region_ids=[]))
    assert status_page(client, run)["execution_stage"] == "rendering"
    _, other = page(client)
    region(client, other, source_text="人工校正", translated_text="Manual translation")
    other_project = {"id": other["project_id"]}
    manual_run = begin(client, other_project)
    assert status_page(client, manual_run)["execution_stage"] == "preflight_review"


@pytest.mark.parametrize("task_status", ["QUEUED", "RUNNING", "PAUSED", "CANCELLED", "FAILED"])
def test_next_steps_wait_for_actual_tasks_and_do_not_resume_cancelled_work(client, task_status):
    project, image = page(client)
    run = begin(client, project)
    with SessionLocal() as db:
        task = ProcessingTask(project_id=project["id"], image_id=image["id"], task_type="full", status=task_status,
            payload={"start_stage": "detection", "end_stage": "ocr", "options": {"workflow_run_id": run}})
        db.add(task)
        db.commit()
        task_id = task.id
    result = status_page(client, run)
    assert result["next_steps"][0]["arguments"]["task_id"] == task_id
    assert not any(item["tool"] in {"images_process_image", "tasks_resume_task"} for item in result["next_steps"])
    if task_status in {"CANCELLED", "PAUSED", "FAILED"}:
        assert not any(item["automatic"] for item in result["next_steps"])
    if task_status == "CANCELLED":
        assert not any(item["tool"] == "task_retry" for item in result["next_steps"])












def test_successful_empty_detector_receipt_does_not_repeat_detection(client):
    project, image = page(client)
    run = begin(client, project)
    with SessionLocal() as db:
        db.add(ProcessingTask(project_id=project["id"], image_id=image["id"], task_type="full", status="COMPLETED",
            payload={"start_stage": "detection", "end_stage": "ocr", "options": {"workflow_run_id": run}}))
        db.commit()
    assert status_page(client, run)["execution_stage"] == "preflight_review"


def test_successful_empty_ocr_result_is_inspected_instead_of_retried_forever(client):
    project, image = page(client)
    text = region(client, image, source_text="", translated_text="")
    run = begin(client, project)
    with SessionLocal() as db:
        db.get(TextRegion, text["id"]).layout_data = {"ocr": {}, "ocr_geometry_stale": False}
        db.commit()
    assert status_page(client, run)["execution_stage"] == "preflight_review"
