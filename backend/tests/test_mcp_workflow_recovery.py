"""Exercise recovery and delivery through native tools and the real queues."""
import io
from zipfile import ZipFile

import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services import mcp_workflows
from app.services.regions import STALE_CLEAN_PATH_KEY
from app.services.translation.providers import OpenAICompatibleTranslator
from app.storage import get_storage
from PIL import Image, ImageDraw
from sqlalchemy import select
from test_mcp import mutation, page, rpc
from test_mcp_native_workflow import planned_evidence, status_page
from test_mcp_workflows import begin, ok, region, review, state


@pytest.fixture(autouse=True)
def configured(client, monkeypatch):
    client.headers["Host"] = "localhost"
    monkeypatch.setattr(mcp_workflows, "blockers", lambda db, run, **kwargs: [])


@pytest.fixture
def providers(monkeypatch):
    calls = []

    class Translator(OpenAICompatibleTranslator):
        async def translate_regions(self, regions, **kwargs):
            calls.append(regions)
            return {key: "Goodbye!" for key, _ in regions}

    class Inpainter:
        def inpaint(self, image_path, mask_path, output_path, region_type):
            with Image.open(image_path) as source:
                source.save(output_path)
            return output_path

    original = PipelineProcessor._provider

    def provider(processor, kind, requested):
        if kind == "ocr":
            pytest.fail("Retranslation must preserve nonempty corrected OCR")
        if kind == "translation":
            return Translator({}), "recovery-test"
        if kind == "inpainting":
            return Inpainter(), "recovery-test"
        return original(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", provider)
    return calls


def wait(client, task):
    result = ok(rpc(client, "task_wait", {"task_id": task["id"], "kind": "processing", "seconds": 20}))
    assert result["status"] == "COMPLETED", (result["status"], result.get("error_message"), result.get("error_kind"))


def process(client, image, run=None):
    if run:
        response = ok(rpc(client, "workflow_advance", mutation(run_id=run, image_id=image["id"],
            expected_versions=state(client, image)["expected_versions"])))
        task = response["task"]
    else:
        response = client.post(f"/api/images/{image['id']}/render", json={})
        assert response.status_code == 202, response.text
        task = response.json()
    wait(client, task)
    return task


def published(client, *, extra=False):
    project, image = page(client)
    text = region(client, image, orientation="horizontal")
    other = region(client, image, bbox=[185, 20, 90, 100], translated_text="Keep manual text") if extra else None
    with SessionLocal() as db:
        page_row = db.get(ImagePage, image["id"])
        clean = get_storage().page_dir(project["id"], image["id"]) / "clean" / "fixture-clean.png"
        with Image.open(get_storage().absolute(page_row.original_path)) as original:
            original.save(clean)
        page_row.clean_path = get_storage().relative(clean)
        for value in (text, other):
            if value is None:
                continue
            row = db.get(TextRegion, value["id"])
            mask = Image.new("L", (300, 200), 0)
            ImageDraw.Draw(mask).rectangle((35, 50, 75, 70), fill=255)
            path = get_storage().page_dir(project["id"], image["id"]) / "masks" / f"{row.id}.png"
            mask.save(path)
            row.pixel_mask_path = get_storage().relative(path)
            row.layout_data = {**(row.layout_data or {}), "mask_generation": {"method": "manual_mask", "repair_pending": False}}
        db.commit()
    process(client, image)
    return project, image, text, other


def correction(client, run, image):
    return ok(rpc(client, "workflow_begin_correction", mutation(run_id=run, image_id=image["id"],
        expected_versions=state(client, image)["expected_versions"])))


def patch(client, run, image, text, body):
    return ok(rpc(client, "page_edit", mutation(image_id=image["id"], workflow_run_id=run,
        expected_versions=state(client, image)["expected_versions"], update=[{"region_id": text["id"], "patch": body}])))


def test_corrected_ocr_retranslates_only_affected_text_and_keeps_masks(client, providers):
    project, image, text, other = published(client, extra=True)
    run = begin(client, project)
    ok(review(client, run, image))
    correction(client, run, image)
    patch(client, run, image, text, {"source_text": "さようなら"})
    stale = state(client, image)
    assert next(row for row in stale["regions"] if row["id"] == text["id"])["state"]["translation_source_stale"]
    assert status_page(client, run)["execution_stage"] == "preflight_review"
    ok(review(client, run, image, phase="preflight", outcome="passed", region_ids=[text["id"], other["id"]]))
    advice = status_page(client, run)
    assert advice["execution_stage"] == "translation"
    assert advice["next_steps"][0]["tool"] == "workflow_advance"
    with SessionLocal() as db:
        old_masks = {row.id: row.pixel_mask_path for row in db.scalars(select(TextRegion).where(TextRegion.image_id == image["id"]))}
    process(client, image, run)
    assert status_page(client, run)["execution_stage"] == "rendering"
    process(client, image, run)
    rows = {row["id"]: row for row in state(client, image)["regions"]}
    assert providers == [[(text["region_key"], "さようなら")]]
    assert rows[text["id"]]["source_text"] == "さようなら"
    assert rows[text["id"]]["translated_text"] == "Goodbye!"
    assert not rows[text["id"]]["state"]["translation_source_stale"]
    assert rows[other["id"]]["translated_text"] == "Keep manual text"
    with SessionLocal() as db:
        assert {row.id: row.pixel_mask_path for row in db.scalars(select(TextRegion).where(TextRegion.image_id == image["id"]))} == old_masks
    ok(review(client, run, image, outcome="passed"))


def test_agent_can_confirm_existing_translation_after_ocr_change(client):
    project, image, text, _ = published(client)
    run = begin(client, project, translation_mode="agent")
    patch(client, run, image, text, {"source_text": "こんにちは！"})
    assert status_page(client, run)["execution_stage"] == "preflight_review"
    ok(review(client, run, image, phase="preflight", outcome="passed", region_ids=[text["id"]]))
    advice = status_page(client, run)["next_steps"][0]
    assert advice["tool"] == "page_edit"
    advice["arguments"]["update"][0]["patch"]["translated_text"] = "Hello"
    ok(rpc(client, advice["tool"], mutation(**advice["arguments"])))
    assert not state(client, image)["regions"][0]["state"]["translation_source_stale"]
    assert status_page(client, run)["execution_stage"] == "final_review"


@pytest.mark.parametrize("change", [{"translated_text": "Changed in browser"}, {"bbox": [35, 40, 90, 100]}])
def test_external_edit_recovers_with_current_preflight_and_same_round_budget(client, providers, change):
    project, image, text, _ = published(client)
    run = begin(client, project)
    ok(review(client, run, image, outcome="passed"))
    assert client.patch(f"/api/regions/{text['id']}", json=change).status_code == 200
    assert status_page(client, run)["execution_stage"] == "stale_review"
    denied = rpc(client, "workflow_begin_correction", mutation(run_id=run, image_id=image["id"],
        expected_versions=state(client, image)["expected_versions"]))
    assert denied["isError"]
    evidence_ids, _ = planned_evidence(client, image, "preflight")
    # A source inspection without diagnosed issues must not unlock writes.
    ok(review(client, run, image, phase="preflight", outcome="passed", region_ids=[text["id"]], evidence_ids=evidence_ids))
    assert rpc(client, "workflow_begin_correction", mutation(run_id=run, image_id=image["id"],
        expected_versions=state(client, image)["expected_versions"]))["isError"]
    ok(review(client, run, image, phase="preflight", outcome="issues", region_ids=[text["id"]], evidence_ids=evidence_ids))
    assert correction(client, run, image)["round"] == 1
    process(client, image, run)
    final_ids, _ = planned_evidence(client, image, "final")
    ok(review(client, run, image, outcome="passed", evidence_ids=final_ids))
    assert status_page(client, run)["execution_stage"] == "ready_for_delivery"
    assert not providers


def export(client, run):
    call = ok(rpc(client, "workflow_status", {"run_id": run}))["next_steps"][0]
    assert call["tool"] == "workflow_export", call
    queued = ok(rpc(client, call["tool"], mutation(**call["arguments"])))
    job = ok(rpc(client, "task_wait", {"task_id": queued["job"]["id"], "kind": "file", "seconds": 20}))
    assert job["status"] == "COMPLETED", job
    return job, client.get(queued["report_downloads"]["json"]).json()


def test_delivery_requires_new_export_after_correction_or_report_change(client):
    project, image, text, _ = published(client)
    run = begin(client, project)
    ok(review(client, run, image))
    first, old_report = export(client, run)
    correction(client, run, image)
    patch(client, run, image, text, {"translated_text": "New final translation"})
    process(client, image, run)
    ok(review(client, run, image, outcome="passed"))
    status = ok(rpc(client, "workflow_status", {"run_id": run}))
    assert not status["exports"][0]["current"]
    second, report = export(client, run)
    assert first["id"] != second["id"]
    assert old_report["pages"][0]["version"] != report["pages"][0]["version"] == state(client, image)["version"]
    assert report["fully_verified"]
    status = ok(rpc(client, "workflow_status", {"run_id": run}))
    assert status["exports"][0]["current"] and not status["exports"][1]["current"]
    assert status["next_steps"][0]["arguments"]["task_id"] == second["id"]
    with ZipFile(io.BytesIO(client.get(first["download_path"]).content)) as old, ZipFile(io.BytesIO(client.get(second["download_path"]).content)) as new:
        assert old.read("0001.png") != new.read("0001.png")
    version = state(client, image)["version"]
    ok(review(client, run, image, outcome="passed", issues=[{"region_id":text["id"],
        "category":"translation", "message":"Correction verified", "status":"resolved"}]))
    assert state(client, image)["version"] == version  # Report-only changes also invalidate old delivery advice.
    assert ok(rpc(client, "workflow_status", {"run_id": run}))["next_steps"][0]["tool"] == "workflow_export"


def test_preserve_all_art_can_be_reviewed_and_exported_without_erasure(client):
    project, image = page(client)
    art = region(client, image, region_type="sfx", translated_text="")
    run = begin(client, project)
    ok(review(client, run, image, phase="preflight", outcome="passed", region_ids=[],
        preserved=[{"region_id": art["id"], "reason": "Preserve integrated artwork"}]))
    process(client, image, run)
    evidence_ids, _ = planned_evidence(client, image, "final")
    clean = ok(rpc(client, "page_preview", {"image_id": image["id"], "kind": "clean"}))
    assert clean["clean_uses_original"]
    ok(review(client, run, image, outcome="passed", evidence_ids=evidence_ids))
    job, report = export(client, run)
    assert report["fully_verified"] and len(report["pages"][0]["preserved"]) == 1
    with ZipFile(io.BytesIO(client.get(job["download_path"]).content)) as comic:
        with Image.open(io.BytesIO(comic.read("0001.png"))) as result:
            assert result.size == (300, 200) and result.convert("RGB").getpixel((0, 0)) == (255, 255, 255)


@pytest.mark.parametrize("reason", ["nonempty_mask", "missing_mask", "stale_clean"])
def test_original_cannot_masquerade_as_clean_when_repairs_are_needed(client, reason):
    _, image, text, _ = published(client)
    with SessionLocal() as db:
        page_row = db.get(ImagePage, image["id"])
        page_row.clean_path = None
        if reason == "missing_mask":
            db.get(TextRegion, text["id"]).pixel_mask_path = "missing-mask.png"
        if reason == "stale_clean":
            page_row.metadata_json = {**page_row.metadata_json, STALE_CLEAN_PATH_KEY: page_row.original_path}
            db.get(TextRegion, text["id"]).pixel_mask_path = None
        db.commit()
    plan = ok(rpc(client, "page_review_plan", {"image_id": image["id"], "phase": "final",
        "expected_version": state(client, image)["version"]}))
    assert any(item["kind"] == "clean" for item in plan["blockers"])
    assert rpc(client, "page_preview", {"image_id": image["id"], "kind": "clean"})["isError"]






def test_failed_current_export_is_inspected_without_automatic_resubmission(client, monkeypatch):
    from app.services import file_export
    project, image, _, _ = published(client)
    run = begin(client, project)
    ok(review(client, run, image))

    def fail(*args, **kwargs):
        raise ValueError("Deterministic archive failure")

    monkeypatch.setattr(file_export, "export_snapshot", fail)
    queued = ok(rpc(client, "workflow_export", mutation(run_id=run)))
    job = ok(rpc(client, "task_wait", {"task_id": queued["job"]["id"], "kind": "file", "seconds": 20}))
    assert job["status"] == "FAILED"
    status = ok(rpc(client, "workflow_status", {"run_id": run}))
    assert not status["exports"][0]["current"]
    assert status["exports"][0]["status"] == "FAILED"
    assert [item["tool"] for item in status["next_steps"]] == ["workflow_export"]
    assert not status["next_steps"][0]["automatic"]
