"""Shared mask evidence, real publication, and independent source/target geometry."""
import base64
import json
import threading
import time
import zlib
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

import cv2
import numpy as np
import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services.base import OCRResult
from app.services.detection.layout import build_layout_regions
from app.services.detection.mask_geometry import points_inside
from app.services.detection.text_angle import analyze_text_mask
from app.services.detection.text_roles import LayoutPrediction
from app.services.layout.text_frame import aligned_text_frame
from app.services.layout.text_rotation import effective_text_rotation
from app.services.layout.typography import (
    ANALYSIS_KEY,
    STATE_KEY,
    analyze_manual_regions,
    automatic_target_is_current,
    geometry_signature,
    oriented_foreground_frame,
    text_evidence,
    typography_patch,
)
from app.services.regions import synchronize_translation_geometry_after_repair
from app.storage import get_storage
from PIL import Image


def lettering(angle=12, vertical=False):
    mask = np.zeros((300, 360), np.uint8)
    for y in (100, 130, 160):
        for x in range(80, 261, 18):
            mask[y:y+10, x:x+9] = 1
    if vertical:
        mask = mask.T.copy()
    h, w = mask.shape
    return cv2.warpAffine(mask, cv2.getRotationMatrix2D((w/2, h/2), -angle, 1), (w, h), flags=cv2.INTER_NEAREST)


def plain_region(mask, **changes):
    h, w = mask.shape
    box = [20., 20., w-40., h-40.]
    poly = [[20., 20.], [w-20., 20.], [w-20., h-20.], [20., h-20.]]
    return SimpleNamespace(**({"id": "frame", "bbox": box, "polygon": poly, "translated_bbox": list(box),
        "translated_polygon": deepcopy(poly), "rotation": 0., "orientation": "vertical", "locked": False,
        "perspective_warp": False, "layout_data": {"manual": True, STATE_KEY: {
            "version": 2, "orientation": "auto", "target": "auto", "target_bbox": list(box),
            "target_polygon": deepcopy(poly), "angle": 0.}}} | changes))


def apply(row, patch):
    for key, value in patch.items():
        setattr(row, key, value)


@pytest.mark.parametrize("vertical", [False, True])
@pytest.mark.parametrize("angle", [-25, -12, 12, 25])
def test_detector_and_manual_polygon_share_angle_direction_and_containing_target(vertical, angle):
    mask = lettering(angle, vertical)
    h, w = mask.shape
    y, x = np.nonzero(mask)
    box = [int(x.min()), int(y.min()), int(x.max()-x.min()+1), int(y.max()-y.min()+1)]
    detected, = build_layout_regions([LayoutPrediction("text", .95, box, mask, (0, 0))], w, h, {}, page_key="same")
    native = detected.metadata[ANALYSIS_KEY]
    manual = text_evidence(mask, (0, 0), w, h, vertical=True, source="segmentation")
    assert native["angle"] == pytest.approx(angle, abs=2)
    assert manual["angle"] == pytest.approx(native["angle"], abs=2)
    row = plain_region(mask)
    before = deepcopy(vars(row))
    apply(row, typography_patch(row, manual))
    assert row.orientation == ("vertical" if vertical else "horizontal")
    assert row.bbox == before["bbox"] and row.polygon == before["polygon"]
    assert aligned_text_frame(row.translated_polygon, row.rotation)
    assert points_inside(x+.5, y+.5, np.asarray(row.translated_polygon)).all()
    assert automatic_target_is_current(row)
    assert effective_text_rotation(row, perspective=True) == 0
    assert effective_text_rotation(row, perspective=False) == row.rotation


@pytest.mark.parametrize("protection", ["angle", "zero", "target", "direction", "locked", "perspective", "legacy"])
def test_explicit_typography_is_protected(protection):
    mask = lettering()
    row = plain_region(mask)
    if protection in {"angle", "zero"}:
        row.rotation = 15 if protection == "angle" else 0
        row.layout_data["text_rotation"] = {"source": "user", "angle": row.rotation}
    elif protection == "target":
        row.translated_polygon[0][0] += 3
    elif protection == "direction":
        row.layout_data[STATE_KEY]["orientation"] = "manual"
        row.orientation = "vertical"
    elif protection == "legacy":
        row.layout_data = {"manual": True}
    else:
        setattr(row, "perspective_warp" if protection == "perspective" else "locked", True)
    before = deepcopy(vars(row))
    apply(row, typography_patch(row, text_evidence(mask, (0, 0), 360, 300, vertical=False, source="segmentation")))
    if protection == "direction":
        assert row.orientation == before["orientation"]
    else:
        for key in ("rotation", "orientation", "translated_bbox", "translated_polygon", "bbox", "polygon"):
            assert getattr(row, key) == before[key]


def test_out_of_bounds_frame_is_rejected_without_clipping_vertices():
    mask = np.ones((40, 40), np.uint8)
    assert oriented_foreground_frame(mask, (0, 0), 25, dict.fromkeys(("top", "bottom", "left", "right"), 3), 40, 40) is None


def test_uncertain_analysis_preserves_previous_angle_and_geometry():
    row = plain_region(lettering())
    apply(row, typography_patch(row, text_evidence(lettering(), (0, 0), 360, 300, vertical=True, source="segmentation")))
    before = deepcopy(vars(row))
    row.polygon[0][0] += 2
    changed = deepcopy(row.polygon)
    apply(row, typography_patch(row, {"version": 2, "source": "segmentation", "status": "uncertain", "reason": "mixed_directions"}))
    assert row.polygon == changed
    assert row.rotation == before["rotation"] and row.translated_polygon == before["translated_polygon"]


def page_fixture(client, mask, **payload):
    source = BytesIO()
    Image.fromarray(np.where(mask[..., None] > 0, 0, 255).repeat(3, axis=2).astype(np.uint8)).save(source, "PNG")
    project = client.post("/api/projects", json={"name": "adaptive-typography"}).json()
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("slanted.png", source.getvalue(), "image/png")}).json()[0]
    frame = plain_region(mask)
    created = client.post(f"/api/images/{page['id']}/regions", json={"polygon": frame.polygon, "bbox": frame.bbox,
        "orientation": "vertical", "auto_orientation": True, **payload})
    assert created.status_code == 201, created.text
    return project, page, created.json()


@pytest.fixture
def fake_ocr(monkeypatch):
    class OCR:
        def ensure_loaded(self):
            pass

        def recognize(self, source, bbox, orientation, padding, **kwargs):
            return OCRResult("recognized", .99, orientation, {"test": "retained"})
    original = PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda self, kind, name:
        (OCR(), "test") if kind == "ocr" else original(self, kind, name))


def segmentation(monkeypatch, probability):
    from app.services.inpainting import text_segmentation
    calls = Mock(return_value=(probability, {"status": "ready"}))
    monkeypatch.setattr(text_segmentation, "text_probability", calls)
    return calls


def recognize(page_id):
    with SessionLocal() as db:
        page = db.get(ImagePage, page_id)
        PipelineProcessor(db).ocr(page, None, force=True, region_id=None, options={})
        db.commit()


def test_ocr_publication_adapts_manual_frame_retains_source_mask_and_repair_target(client, monkeypatch, fake_ocr):
    mask = lettering()
    _, page, created = page_fixture(client, mask)
    calls = segmentation(monkeypatch, mask.astype(np.float32))
    with SessionLocal() as db:
        row = db.get(TextRegion, created["id"])
        row.pixel_mask_path = "manual-mask-unchanged.png"
        row.layout_data = {**row.layout_data, "mask_generation": {"method": "manual_mask"}}
        db.commit()
    recognize(page["id"])
    with SessionLocal() as db:
        row = db.get(TextRegion, created["id"])
        assert row.rotation == pytest.approx(12, abs=2)
        assert row.orientation == "horizontal" and row.source_text == "recognized"
        assert row.polygon == created["polygon"] and row.bbox == created["bbox"]
        assert row.pixel_mask_path == "manual-mask-unchanged.png"
        assert row.layout_data["mask_generation"]["method"] == "manual_mask"
        assert row.layout_data["ocr"] == {"test": "retained"}
        assert row.layout_data["ocr_geometry_stale"] is False
        target = deepcopy(row.translated_polygon)
        row.layout_data = {**row.layout_data, "translation_geometry_sync": {"pending": True, "revision": 1}}
        synchronize_translation_geometry_after_repair(db, row)
        assert row.translated_polygon == target
        assert row.layout_data["translation_geometry_sync"]["pending"] is False
    assert calls.call_count == 1


def test_concave_manual_frame_ignores_all_foreground_outside_selection(monkeypatch):
    mask = lettering()
    mask[75:, 230:] = 1  # Large distracting foreground outside concave selection.
    row = plain_region(mask)
    row.polygon = [[20, 20], [300, 20], [300, 70], [215, 70], [215, 280], [20, 280]]
    calls = segmentation(monkeypatch, mask.astype(np.float32))
    from app.core.config import get_settings
    one = analyze_manual_regions(get_storage().root / "unused", [row, deepcopy(row)], get_settings(), lambda: None)
    mask[75:, 230:] = 0
    calls.return_value = mask.astype(np.float32), {"status": "ready"}
    two = analyze_manual_regions(get_storage().root / "unused", [row], get_settings(), lambda: None)
    assert one[row.id]["fingerprint"] == two[row.id]["fingerprint"]
    assert one[row.id]["angle"] == two[row.id]["angle"]
    assert calls.call_count == 2  # One segmentation call per pass, not per frame.


def test_missing_segmentation_is_nonfatal_and_preserves_typography(client, monkeypatch, fake_ocr):
    _, page, created = page_fixture(client, lettering())
    segmentation(monkeypatch, None)
    recognize(page["id"])
    with SessionLocal() as db:
        row = db.get(TextRegion, created["id"])
        assert row.source_text == "recognized"
        assert row.rotation == 0 and row.orientation == created["orientation"]
        assert row.translated_polygon == created["translated_polygon"]
        assert row.layout_data[ANALYSIS_KEY]["status"] == "unavailable"


def test_stale_evidence_and_ocr_cannot_overwrite_concurrent_manual_edit(client):
    mask = lettering()
    _, page, created = page_fixture(client, mask)
    with SessionLocal() as db:
        row = db.get(TextRegion, created["id"])
        version = row.updated_at
        evidence = text_evidence(mask, (0, 0), 360, 300, vertical=True, source="segmentation")
        evidence["source_geometry"] = geometry_signature(row)
        response = client.patch(f"/api/regions/{row.id}", json={"rotation": 0, "source_text": "user corrected"})
        assert response.status_code == 200
        assert not PipelineProcessor(db)._save_ocr_result(db.get(ImagePage, page["id"]), row.id,
            OCRResult("late", .99, "horizontal"), expected_version=version, typography=evidence)
        db.expire_all()
        row = db.get(TextRegion, row.id)
        assert row.source_text == "user corrected" and row.rotation == 0
        assert row.translated_polygon == created["translated_polygon"]


def test_manual_direction_and_target_provenance_survive_undo_redo(client):
    project, page, created = page_fixture(client, lettering())
    updated = client.patch(f"/api/regions/{created['id']}", json={"rotation": 0, "orientation": "horizontal"}).json()
    assert updated["layout_data"][STATE_KEY]["orientation"] == "manual"
    assert updated["layout_data"][STATE_KEY]["target"] == "manual"
    history = client.get(f"/api/projects/{project['id']}/history").json()
    undone = client.post(f"/api/projects/{project['id']}/history/undo", json={"expected_entry_id": history["undo_id"], "current_image_id": page["id"]}).json()
    row, = undone["regions"]
    assert row["layout_data"][STATE_KEY]["target"] == "auto"
    redone = client.post(f"/api/projects/{project['id']}/history/redo", json={"expected_entry_id": undone["history"]["redo_id"], "current_image_id": page["id"]}).json()
    row, = redone["regions"]
    assert row["layout_data"][STATE_KEY]["orientation"] == "manual"
    assert row["layout_data"]["text_rotation"]["source"] == "user"


def test_cached_analysis_is_not_mutable_and_fixed_direction_is_separate():
    mask = lettering()
    first = analyze_text_mask(mask, True)
    first["angle"] = 44
    assert analyze_text_mask(mask, True)["angle"] == pytest.approx(12, abs=2)
    assert analyze_text_mask(mask, True, fixed_direction=True)["orientation"] == "vertical"


def test_single_italic_glyph_does_not_supply_a_text_line_direction_or_angle():
    mask = np.zeros((180, 220), np.uint8)
    cv2.putText(mask, "A", (50, 110), cv2.FONT_HERSHEY_SIMPLEX | cv2.FONT_ITALIC, 2, 1, 3)
    analysis = analyze_text_mask(mask, False)
    assert analysis["orientation"] == "horizontal" and analysis["angle"] == 0
    assert not analysis["orientation_confident"]
    assert analysis["status"] == "uncertain"


def test_long_strips_bound_projection_histograms(monkeypatch):
    from app.services.detection import text_angle
    mask = np.zeros((30, 20_000), np.uint8)
    mask[3:6, 100:19_900] = mask[14:17, 100:19_900] = mask[25:28, 100:19_900] = 1
    original = text_angle.projection_score
    spans = []

    def bounded(projection):
        spans.append(float(np.ptp(projection)))
        return original(projection)

    monkeypatch.setattr(text_angle, "projection_score", bounded)
    analyze_text_mask(mask, False)
    assert max(spans) <= 8192*2**.5


def test_legacy_ocr_without_direction_provenance_preserves_existing_orientation(client):
    _, page, created = page_fixture(client, lettering())
    with SessionLocal() as db:
        row = db.get(TextRegion, created["id"])
        row.layout_data = {"manual": True}
        db.commit()
        processor = PipelineProcessor(db)
        assert processor._save_ocr_result(db.get(ImagePage, page["id"]), row.id,
            OCRResult("legacy source", .99, "horizontal"), expected_version=row.updated_at)
        db.refresh(row)
        assert row.source_text == "legacy source" and row.orientation == "vertical"


def test_real_handwriting_with_disagreeing_sections_does_not_get_a_confident_false_tilt():
    records = json.loads((Path(__file__).parent / "fixtures/typography_handwritten_masks.json").read_text())["records"]
    for record in records:
        data = np.frombuffer(zlib.decompress(base64.b64decode(record["packed"])), np.uint8)
        mask = np.unpackbits(data)[:np.prod(record["shape"])].reshape(record["shape"])
        evidence = analyze_text_mask(mask, True)
        assert evidence["angle"] == 0, record["rotation"]
        assert evidence["status"] == "uncertain"


@pytest.mark.parametrize("invert", [False, True])
def test_mcp_creation_ocr_preview_and_cbz_keep_same_adaptive_frame(client, monkeypatch, fake_ocr, invert):
    from test_file_jobs import wait_job
    from test_mcp import mutation, rpc
    from test_mcp_workflows import ok, state
    from test_pipeline import wait_for_task

    mask = lettering(-12)
    project, page, original = page_fixture(client, mask)
    assert client.delete(f"/api/regions/{original['id']}").status_code == 200
    client.headers["Host"] = "localhost"
    if invert:
        with SessionLocal() as db:
            path = get_storage().absolute(db.get(ImagePage, page["id"]).original_path)
        Image.fromarray((mask*255).astype(np.uint8)).convert("RGB").save(path)
    created = ok(rpc(client, "page_edit", mutation(image_id=page["id"], expected_versions=state(client, page)["expected_versions"],
        add=[{"bbox": original["bbox"], "auto_orientation": True}])))
    identifier = created["created"][0]["id"]
    calls = segmentation(monkeypatch, mask.astype(np.float32))
    queued = client.post(f"/api/images/{page['id']}/process", json={"start_stage": "ocr", "end_stage": "ocr", "force": True})
    assert queued.status_code == 202, queued.text
    assert wait_for_task(client, queued.json()["id"])["status"] == "COMPLETED"
    row = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert row["id"] == identifier and row["rotation"] == pytest.approx(-12, abs=2)
    assert row["polygon"] == original["polygon"]
    assert client.patch(f"/api/regions/{identifier}", json={"translated_text": "Translated\ncomic text", "font_family": "DejaVu Sans"}).status_code == 200
    # A clean background fixture isolates typography from repair-model quality.
    with SessionLocal() as db:
        stored = db.get(ImagePage, page["id"])
        background = get_storage().page_dir(project["id"], page["id"]) / "clean" / "fixture.png"
        Image.new("RGB", (360, 300), "black" if invert else "white").save(background)
        stored.clean_path = get_storage().relative(background)
        db.commit()
        processor = PipelineProcessor(db)
        processor.render(stored, None)
        db.commit()
        translated = get_storage().absolute(stored.rendered_path).read_bytes()
        expected = deepcopy(stored.regions[0].translated_polygon)
        layout = stored.regions[0].layout_data
        assert not layout["overflow"] and not layout.get("render_skipped")
    preview = rpc(client, "page_preview", {"image_id": page["id"], "kind": "rendered", "expected_version": state(client, page)["version"]})
    assert any(item["type"] == "image" for item in preview["content"])
    exported = client.post(f"/api/projects/{project['id']}/exports", json={"kind": "cbz"})
    job = wait_job(client, exported.json()["id"])
    assert job["status"] == "COMPLETED", job
    with ZipFile(BytesIO(client.get(job["download_url"]).content)) as archive:
        assert archive.namelist() == ["0001.png"]
        assert archive.read("0001.png") == translated
    with SessionLocal() as db:
        row = db.get(TextRegion, identifier)
        assert row.translated_polygon == expected and row.rotation == pytest.approx(-12, abs=2)
    assert calls.call_count == 1  # Render, MCP preview and export consume saved evidence.


@pytest.mark.parametrize("change", [{"rotation": 0}, {"orientation": "vertical"}, {"translated_bbox": [40, 40, 150, 150]}])
def test_forced_ocr_preserves_explicit_style_and_manually_sized_font(client, monkeypatch, fake_ocr, change):
    _, page, created = page_fixture(client, lettering(), font_size=47)
    assert client.patch(f"/api/regions/{created['id']}", json=change).status_code == 200
    before = client.get(f"/api/images/{page['id']}/regions").json()[0]
    segmentation(monkeypatch, lettering().astype(np.float32))
    recognize(page["id"])
    after = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert after["font_size"] == 47 and after["auto_font_size"] is False
    for key in change:
        assert after[key] == before[key]


def test_source_change_reanalyzes_only_automatic_target(client, monkeypatch, fake_ocr):
    _, page, created = page_fixture(client, lettering())
    calls = segmentation(monkeypatch, lettering().astype(np.float32))
    recognize(page["id"])
    row = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert row["rotation"] == pytest.approx(12, abs=2)
    assert client.patch(f"/api/regions/{row['id']}", json={"bbox": [25, 25, 310, 250]}).status_code == 200
    calls.return_value = lettering(-20).astype(np.float32), {"status": "ready"}
    recognize(page["id"])
    after = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert after["rotation"] == pytest.approx(-20, abs=2)
    assert after["bbox"] == [25, 25, 310, 250]
    assert after["layout_data"][ANALYSIS_KEY]["source_geometry"] != row["layout_data"][ANALYSIS_KEY]["source_geometry"]


@pytest.mark.parametrize("previous_angle", [0, 12])
def test_upright_ocr_updates_automatic_target_before_acknowledging_source_sync(client, monkeypatch, fake_ocr, previous_angle):
    _, page, created = page_fixture(client, lettering(previous_angle))
    calls = segmentation(monkeypatch, lettering(previous_angle).astype(np.float32))
    recognize(page["id"])
    assert client.patch(f"/api/regions/{created['id']}", json={"bbox": [25, 25, 310, 250]}).status_code == 200
    calls.return_value = lettering(0).astype(np.float32), {"status": "ready"}
    recognize(page["id"])
    with SessionLocal() as db:
        row = db.get(TextRegion, created["id"])
        assert row.layout_data[ANALYSIS_KEY]["status"] == "upright"
        assert row.translated_bbox == row.bbox == [25, 25, 310, 250]
        assert row.translated_polygon == row.polygon and row.rotation == 0
        assert row.layout_data[STATE_KEY]["target_orientation"] == row.orientation
        assert row.layout_data["translation_geometry_sync"]["pending"] is False
        expected = deepcopy(row.translated_polygon)
        synchronize_translation_geometry_after_repair(db, row)
        assert row.translated_polygon == expected


@pytest.mark.parametrize("target_patch", [
    {"translated_bbox": [60, 60, 200, 140]}, {"rotation": 0}, {"rotation": 7},
    {"perspective_warp": True}, {"locked": True},
])
def test_source_ocr_and_repair_preserve_preexisting_manual_typography(client, monkeypatch, fake_ocr, target_patch):
    _, page, created = page_fixture(client, lettering(12))
    manual = client.patch(f"/api/regions/{created['id']}", json=target_patch).json()
    assert client.patch(f"/api/regions/{created['id']}", json={"bbox": [25, 25, 310, 250]}).status_code == 200
    segmentation(monkeypatch, lettering(12).astype(np.float32))
    recognize(page["id"])
    with SessionLocal() as db:
        row = db.get(TextRegion, created["id"])
        synchronize_translation_geometry_after_repair(db, row)
        for key in ("translated_bbox", "translated_polygon", "rotation", "perspective_warp", "orientation"):
            assert getattr(row, key) == manual[key]
        assert row.layout_data["translation_geometry_sync"]["pending"] is False


def test_cancellation_during_segmentation_never_publishes_ocr_or_target(client, monkeypatch, fake_ocr):
    from app.core.config import get_settings
    from app.services.inpainting import text_segmentation
    from app.tasks import task_manager

    monkeypatch.setattr(get_settings(), "checkpoint_pipeline_enabled", True)
    _, page, created = page_fixture(client, lettering())
    entered, release = threading.Event(), threading.Event()

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return lettering().astype(np.float32), {"status": "ready"}

    monkeypatch.setattr(text_segmentation, "text_probability", blocked)
    queued = client.post(f"/api/images/{page['id']}/process", json={"start_stage": "ocr", "end_stage": "ocr", "force": True}).json()
    try:
        assert entered.wait(10)
        assert client.post(f"/api/tasks/{queued['id']}/cancel").status_code == 200
    finally:
        release.set()
    deadline = time.monotonic()+10
    while time.monotonic() < deadline:
        task = client.get(f"/api/tasks/{queued['id']}").json()
        if task["status"] == "CANCELLED" and not task_manager.is_active(queued["id"]):
            break
        time.sleep(.03)
    assert task["status"] == "CANCELLED", task
    row = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert row["source_text"] == "" and row["rotation"] == 0
    assert row["translated_polygon"] == created["translated_polygon"]
