from __future__ import annotations

import time
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import app.pipeline.processor as processor_module
import pytest
from app.core.database import SessionLocal
from app.models import TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services.base import OCRResult
from app.services.regions import TRANSLATION_GEOMETRY_SYNC_KEY
from app.services.rendering.snapshot import render_input
from PIL import Image, ImageDraw


def png(*, mask: bool = False) -> BytesIO:
    image = Image.new("L" if mask else "RGB", (200, 200), 255 if mask else "white")
    if not mask:
        ImageDraw.Draw(image).rectangle((20, 20, 30, 65), fill="black")
    output = BytesIO()
    image.save(output, "PNG")
    output.seek(0)
    return output


def wait_task(client, task: dict) -> dict:
    for _ in range(200):
        task = client.get(f"/api/tasks/{task['id']}").json()
        if task["status"] in {"COMPLETED", "FAILED"}:
            return task
        time.sleep(.02)
    pytest.fail(f"Task did not finish: {task}")


@pytest.fixture
def repair_provider(monkeypatch):
    state = SimpleNamespace(fail=False, calls=0)
    original = PipelineProcessor._provider

    class TestOCR:
        def ensure_loaded(self):
            pass

        def recognize(self, _path, _bbox, orientation, _padding, *, polygon=None, crop_angle=None):
            return OCRResult(text="new cropped text", confidence=.99, orientation=orientation)

    class TestTranslator:
        config = {}

        async def translate_regions(self, regions, **_kwargs):
            return {key: f"translated {source}" for key, source in regions}

    class TestRenderer:
        def render(self, background, regions, output, text_layer):
            output.parent.mkdir(parents=True, exist_ok=True)
            text_layer.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(background) as image:
                image.save(output, "PNG")
                Image.new("RGBA", image.size).save(text_layer, "PNG")
            return {"layouts": {}, "inputs": {region.id: render_input(region) for region in regions}}

    def provider(processor, kind, requested):
        if kind == "inpainting":
            return state, "test-repair"
        if kind == "ocr":
            return TestOCR(), "test-ocr"
        if kind == "translation":
            return TestTranslator(), "test-translation"
        if kind == "rendering":
            return TestRenderer(), "test-rendering"
        return original(processor, kind, requested)

    def repair(_processor, _provider, source, _mask, output, _regions):
        if state.fail:
            raise RuntimeError("intentional repair failure")
        state.calls += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            image.save(output, "PNG")

    monkeypatch.setattr(PipelineProcessor, "_provider", provider)
    monkeypatch.setattr(PipelineProcessor, "_repair_masks", repair)
    return state


def setup_page(client, count: int = 1):
    project = client.post("/api/projects", json={"name": "repair-frame-sync"}).json()
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("page.png", png(), "image/png")}).json()[0]
    regions = [client.post(f"/api/images/{page['id']}/regions", json={
        "bbox": [10 + index * 60, 10, 40, 160], "reading_order": index + 1,
        "source_text": "old combined text", "translated_text": "old translation",
    }).json() for index in range(count)]
    return page, regions


def edit_source(client, region):
    x, y, width, _height = region["bbox"]
    polygon = [[x + 4, y], [x + width, y], [x + width - 2, y + 70], [x, y + 65]]
    updated = client.patch(f"/api/regions/{region['id']}", json={"polygon": polygon}).json()
    assert updated["translated_polygon"] == region["translated_polygon"]
    assert updated["layout_data"][TRANSLATION_GEOMETRY_SYNC_KEY]["pending"] is True
    assert updated["layout_data"]["ocr_geometry_stale"] is True
    response = client.put(f"/api/regions/{region['id']}/mask", files={"file": ("mask.png", png(mask=True), "image/png")})
    assert response.status_code == 200, response.text
    return updated


@pytest.mark.parametrize("scope", ["single", "multi", "page"])
def test_successful_repair_syncs_only_changed_selected_regions(client, monkeypatch, repair_provider, scope):
    page, regions = setup_page(client, 3)
    edited = [edit_source(client, region) for region in regions]
    if scope == "single":
        task = client.post(f"/api/regions/{regions[0]['id']}/inpaint", json={"force": True}).json()
        selected = {regions[0]["id"]}
    else:
        selected = {region["id"] for region in (regions[:2] if scope == "multi" else regions)}
        options = {"region_ids": sorted(selected)} if scope == "multi" else {}
        task = client.post(f"/api/images/{page['id']}/process", json={
            "start_stage": "inpainting", "end_stage": "rendering", "force": True, "options": options,
        }).json()
    task = wait_task(client, task)
    assert task["status"] == "COMPLETED", task
    persisted = {region["id"]: region for region in client.get(f"/api/images/{page['id']}/regions").json()}
    for before, source in zip(regions, edited, strict=True):
        result = persisted[before["id"]]
        if before["id"] in selected:
            assert result["translated_bbox"] == source["bbox"]
            assert result["translated_polygon"] == source["polygon"]
            assert result["layout_data"][TRANSLATION_GEOMETRY_SYNC_KEY]["pending"] is False
            assert result["layout_data"][TRANSLATION_GEOMETRY_SYNC_KEY]["revision"] == 1
            if scope != "single":
                assert result["layout_data"]["render_input"]["translated_polygon"] == source["polygon"]
        else:
            assert result["translated_polygon"] == before["translated_polygon"]
            assert result["layout_data"][TRANSLATION_GEOMETRY_SYNC_KEY]["pending"] is True


def test_failure_keeps_manual_target_and_retry_resolves_pending_only_once(client, repair_provider):
    page, regions = setup_page(client)
    before = client.patch(f"/api/regions/{regions[0]['id']}", json={"rotation": 18, "perspective_warp": True}).json()
    edit_source(client, before)
    repair_provider.fail = True
    failed = wait_task(client, client.post(f"/api/regions/{before['id']}/inpaint", json={"force": True}).json())
    assert failed["status"] == "FAILED", failed
    unchanged = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert unchanged["translated_polygon"] == before["translated_polygon"]
    assert unchanged["rotation"] == 18 and unchanged["perspective_warp"] is True
    assert unchanged["layout_data"][TRANSLATION_GEOMETRY_SYNC_KEY]["pending"] is True
    repair_provider.fail = False
    success = wait_task(client, client.post(f"/api/regions/{before['id']}/inpaint", json={"force": True}).json())
    assert success["status"] == "COMPLETED", success
    synchronized = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert synchronized["translated_polygon"] == before["translated_polygon"]
    assert synchronized["rotation"] == 18 and synchronized["perspective_warp"] is True
    assert synchronized["layout_data"][TRANSLATION_GEOMETRY_SYNC_KEY]["pending"] is False
    assert synchronized["layout_data"][TRANSLATION_GEOMETRY_SYNC_KEY]["revision"] == 0
    custom = client.patch(f"/api/regions/{before['id']}", json={
        "translated_bbox": [40, 30, 80, 90], "rotation": -12, "perspective_warp": True,
    }).json()
    # Mask-only edits must not turn an independent translation back into a source frame.
    client.put(f"/api/regions/{before['id']}/mask", files={"file": ("mask.png", png(mask=True), "image/png")})
    repeated = wait_task(client, client.post(f"/api/regions/{before['id']}/inpaint", json={"force": True}).json())
    assert repeated["status"] == "COMPLETED", repeated
    final = client.get(f"/api/images/{page['id']}/regions").json()[0]
    for field in ("bbox", "polygon", "translated_bbox", "translated_polygon", "rotation", "perspective_warp"):
        assert final[field] == custom[field]
    assert final["layout_data"][TRANSLATION_GEOMETRY_SYNC_KEY]["revision"] == 0


def test_mask_ocr_and_translation_do_not_consume_pending_frame_sync(client, repair_provider):
    page, regions = setup_page(client)
    source = edit_source(client, regions[0])
    for action in ("mask", "ocr", "translation"):
        task = wait_task(client, client.post(f"/api/images/{page['id']}/process", json={
            "start_stage": action, "end_stage": action, "force": True,
        }).json())
        assert task["status"] == "COMPLETED", task
        result = client.get(f"/api/images/{page['id']}/regions").json()[0]
        assert result["translated_polygon"] == regions[0]["translated_polygon"]
        assert result["layout_data"][TRANSLATION_GEOMETRY_SYNC_KEY]["pending"] is True
        if action != "mask":
            assert result["layout_data"]["ocr_geometry_stale"] is False
    task = wait_task(client, client.post(f"/api/regions/{regions[0]['id']}/inpaint", json={"force": True}).json())
    assert task["status"] == "COMPLETED", task
    result = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert result["translated_polygon"] == source["polygon"]


@pytest.mark.parametrize("manually_corrected", [False, True])
def test_legacy_unsynced_frames_recover_without_overwriting_later_target_edits(client, repair_provider, manually_corrected):
    page, regions = setup_page(client)
    edited = edit_source(client, regions[0])
    target = regions[0]
    if manually_corrected:
        target = client.patch(f"/api/regions/{regions[0]['id']}", json={"translated_bbox": [20, 25, 60, 80]}).json()
    with SessionLocal() as db:
        stored = db.get(TextRegion, regions[0]["id"])
        stored.layout_data = {key: value for key, value in stored.layout_data.items() if key != TRANSLATION_GEOMETRY_SYNC_KEY}
        db.commit()
    task = wait_task(client, client.post(f"/api/regions/{regions[0]['id']}/inpaint", json={"force": True}).json())
    assert task["status"] == "COMPLETED", task
    final = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert final["translated_polygon"] == (target["translated_polygon"] if manually_corrected else edited["polygon"])


def test_manual_source_correction_clears_stale_ocr_warning_without_syncing_the_target(client, repair_provider):
    page, regions = setup_page(client)
    edited = edit_source(client, regions[0])
    corrected = client.patch(f"/api/regions/{regions[0]['id']}", json={"source_text": "corrected top bubble"}).json()
    assert corrected["layout_data"]["ocr_geometry_stale"] is False
    assert corrected["layout_data"][TRANSLATION_GEOMETRY_SYNC_KEY]["pending"] is True
    assert corrected["translated_polygon"] == regions[0]["translated_polygon"]
    assert corrected["polygon"] == edited["polygon"]


@pytest.mark.parametrize("scope", ["single", "multi", "page", "direct"])
def test_target_view_repair_adopts_target_before_generating_selected_masks(client, monkeypatch, repair_provider, scope):
    page, regions = setup_page(client, 3)
    targets = []
    for index, region in enumerate(regions):
        # A previous manual mask must be discarded when its source frame moves.
        client.put(f"/api/regions/{region['id']}/mask", files={"file": ("mask.png", png(mask=True), "image/png")})
        targets.append(client.patch(f"/api/regions/{region['id']}", json={
            "translated_polygon": [[18 + index * 60, 18], [46 + index * 60, 22], [45 + index * 60, 75], [16 + index * 60, 72]],
            "rotation": 14, "perspective_warp": True,
        }).json())
        assert targets[-1]["polygon"] == region["polygon"]  # Editing alone does not sync.
    generated = []
    original = processor_module.create_text_mask

    def mask(source, polygon, output, **kwargs):
        generated.append(deepcopy(polygon))
        return original(source, polygon, output, **kwargs)

    monkeypatch.setattr(processor_module, "create_text_mask", mask)
    selected = regions[:1] if scope == "single" else regions[:2] if scope in {"multi", "direct"} else regions
    options = {"repair_geometry": "translated", "text_segmentation": False}
    if scope == "single":
        response = client.post(f"/api/regions/{regions[0]['id']}/inpaint", json={"force": True, "options": options})
    else:
        if scope != "page":
            options["region_ids"] = [region["id"] for region in selected]
        response = client.post(f"/api/images/{page['id']}/process", json={
            "start_stage": "inpainting" if scope == "direct" else "mask", "end_stage": "rendering",
            "force": True, "options": options,
        })
    task = wait_task(client, response.json())
    assert task["status"] == "COMPLETED", task
    assert generated == [target["translated_polygon"] for target in targets[:len(selected)]]
    persisted = {item["id"]: item for item in client.get(f"/api/images/{page['id']}/regions").json()}
    selected_ids = {region["id"] for region in selected}
    for before, target in zip(regions, targets, strict=True):
        result = persisted[before["id"]]
        assert result["translated_polygon"] == target["translated_polygon"]
        assert result["rotation"] == 14 and result["perspective_warp"] is True
        if before["id"] in selected_ids:
            assert result["bbox"] == target["translated_bbox"]
            assert result["polygon"] == target["translated_polygon"]
            assert result["mask_url"] != target["mask_url"]
            assert result["layout_data"]["ocr_geometry_stale"] is True
            assert result["source_text"] == before["source_text"]
            sync = result["layout_data"][TRANSLATION_GEOMETRY_SYNC_KEY]
            assert sync["pending"] is False and sync["revision"] == 1
            assert sync["direction"] == "translated_to_source"
            assert sync["previous_source_polygon"] == before["polygon"]
        else:
            assert result["polygon"] == before["polygon"]
            assert result["mask_url"] == target["mask_url"]
    # Repeating the same explicit repair does not re-sync or reset transforms.
    repeated = wait_task(client, client.post(f"/api/regions/{selected[0]['id']}/inpaint", json={
        "force": True, "options": {"repair_geometry": "translated", "text_segmentation": False},
    }).json())
    assert repeated["status"] == "COMPLETED", repeated
    final = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert final["layout_data"][TRANSLATION_GEOMETRY_SYNC_KEY]["revision"] == 1
    assert final["rotation"] == 14 and final["perspective_warp"] is True


def test_failed_target_repair_can_retry_without_reverting_target_transforms(client, repair_provider):
    page, regions = setup_page(client)
    source = edit_source(client, regions[0])  # Even a pending forward sync must be overridden.
    target = client.patch(f"/api/regions/{source['id']}", json={
        "translated_bbox": [18, 20, 40, 90], "rotation": -17, "perspective_warp": True,
    }).json()
    repair_provider.fail = True
    failed = wait_task(client, client.post(f"/api/regions/{target['id']}/inpaint", json={
        "force": True, "options": {"repair_geometry": "translated", "text_segmentation": False},
    }).json())
    assert failed["status"] == "FAILED", failed
    synchronized = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert synchronized["polygon"] == target["translated_polygon"]
    assert synchronized["layout_data"][TRANSLATION_GEOMETRY_SYNC_KEY]["pending"] is False
    repair_provider.fail = False
    # Retrying from the source view must also preserve the accepted target.
    success = wait_task(client, client.post(f"/api/regions/{target['id']}/inpaint", json={"force": True}).json())
    assert success["status"] == "COMPLETED", success
    final = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert final["polygon"] == final["translated_polygon"] == target["translated_polygon"]
    assert final["rotation"] == -17 and final["perspective_warp"] is True
    assert final["layout_data"][TRANSLATION_GEOMETRY_SYNC_KEY]["revision"] == 1


def test_target_geometry_option_does_not_sync_for_mask_only_operation(client, repair_provider):
    page, regions = setup_page(client)
    target = client.patch(f"/api/regions/{regions[0]['id']}", json={"translated_bbox": [20, 20, 40, 50]}).json()
    task = wait_task(client, client.post(f"/api/images/{page['id']}/process", json={
        "start_stage": "mask", "end_stage": "mask", "force": True,
        "options": {"repair_geometry": "translated", "text_segmentation": False},
    }).json())
    assert task["status"] == "COMPLETED", task
    result = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert result["polygon"] == regions[0]["polygon"]
    assert result["translated_polygon"] == target["translated_polygon"]
