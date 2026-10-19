from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services.base import DetectionResult
from app.services.detection.layout import build_layout_regions
from app.services.detection.text_roles import LayoutPrediction
from app.services.layout.text_rotation import (
    TEXT_ROTATION_KEY,
    effective_text_rotation,
    free_text_angle,
    initialize_free_text_rotation,
    record_user_rotation,
)
from app.services.regions import save_revision, synchronize_translation_geometry_after_repair
from app.utils.geometry import bbox_to_polygon
from PIL import Image


def evidence(angle=-28., role="free_text"):
    return {
        "detector": "rfdetr", "text_angle": angle,
        "text_role": {"version": 1, "label": role},
        "text_classification": {"status": "classified", "label": "text", "excluded": False},
        "balloon_assignment": {"status": "unassigned", "bubble_id": None, "method": "text_mask_containment"},
    }


def frame(box=None, **kwargs):
    box = box or [20., 20., 80., 30.]
    return SimpleNamespace(
        **({"bbox": box, "polygon": bbox_to_polygon(box), "translated_bbox": list(box),
            "translated_polygon": bbox_to_polygon(box), "rotation": 0., "bubble_id": None,
            "orientation": "horizontal", "locked": False, "perspective_warp": False,
            "layout_data": {"manual": True}} | kwargs),
    )


def candidate(box=None, **kwargs):
    box = box or [20, 20, 80, 30]
    return DetectionResult(
        **({"bbox": list(box), "polygon": bbox_to_polygon(box), "confidence": .95,
            "orientation": "horizontal", "metadata": evidence(),
            "text_mask": np.ones((box[3], box[2]), np.uint8),
            "text_mask_origin": (box[0], box[1])} | kwargs),
    )


@pytest.mark.parametrize("label", ["dialogue", "unknown", "onomatopoeia", ""])
def test_non_free_roles_never_initialize_rotation(label):
    region = frame(layout_data={"detection": evidence(role=label)})
    before = deepcopy(vars(region))
    assert not initialize_free_text_rotation(region)
    assert vars(region) == before


@pytest.mark.parametrize("damage", ["role", "assignment", "classification", "detector", "angle"])
def test_missing_provenance_is_unknown_not_free_text(damage):
    data = evidence()
    data.pop({"role": "text_role", "assignment": "balloon_assignment", "classification": "text_classification",
              "detector": "detector", "angle": "text_angle"}[damage])
    assert free_text_angle(data) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 46, -46, True, "-28", None])
def test_unusable_angles_are_not_adopted(value):
    assert free_text_angle(evidence(value)) is None


@pytest.mark.parametrize("protection", ["rotation", "zero", "perspective", "locked", "target", "dialogue"])
def test_authored_typography_and_dialogue_are_unchanged(protection):
    region = frame()
    if protection == "rotation":
        region.rotation = 12.
    elif protection == "zero":
        record_user_rotation(region)
    elif protection == "perspective":
        region.perspective_warp = True
    elif protection == "locked":
        region.locked = True
    elif protection == "target":
        region.translated_polygon[0][0] += 2
    else:
        region.layout_data["detection"] = evidence(role="dialogue")
    before = deepcopy(vars(region))
    assert not initialize_free_text_rotation(region, evidence())
    assert vars(region) == before


def test_only_initializes_once_without_coupling_later_frame_changes():
    region = frame()
    before = deepcopy(vars(region))
    assert initialize_free_text_rotation(region, evidence())
    assert region.rotation == -28.
    assert region.layout_data[TEXT_ROTATION_KEY]["source"] == "detector"
    for key in ("polygon", "bbox", "translated_polygon", "translated_bbox"):
        assert getattr(region, key) == before[key]
    region.polygon[0][0] += 1
    assert not initialize_free_text_rotation(region, evidence(18.))
    assert region.rotation == -28.


def test_perspective_uses_automatic_angle_once_and_restores_standard_without_mutation():
    region = frame()
    assert initialize_free_text_rotation(region, evidence())
    assert effective_text_rotation(region) == -28.
    before = deepcopy(vars(region))
    region.perspective_warp = True
    assert effective_text_rotation(region) == 0.
    assert effective_text_rotation(region, perspective=False) == -28.  # Invalid warp fallback.
    region.perspective_warp = False
    assert effective_text_rotation(region) == -28.
    assert vars(region) == before


@pytest.mark.parametrize("angle", [0., -28., 17.])
def test_user_rotation_in_perspective_remains_additive_even_if_equal_to_detector(angle):
    region = frame()
    assert initialize_free_text_rotation(region, evidence())
    region.perspective_warp, region.rotation = True, angle
    record_user_rotation(region)
    assert effective_text_rotation(region) == angle


def test_perspective_does_not_silently_erase_untracked_rotation_changes():
    region = frame()
    assert initialize_free_text_rotation(region, evidence())
    region.perspective_warp, region.rotation = True, 12.
    assert effective_text_rotation(region) == 12.


@pytest.mark.parametrize("kind", ["free", "assigned", "partial", "empty_text", "empty_bubble"])
def test_native_role_does_not_treat_failed_bubble_assignment_as_free(kind):
    box = [20, 20, 80, 30]
    mask = np.ones((30, 80), np.uint8) if kind != "empty_text" else np.zeros((30, 80), np.uint8)
    text = LayoutPrediction("text", .95, box, mask, (20, 20))
    items = [text]
    if kind != "free":
        bubble_box = [60, 10, 80, 80] if kind == "partial" else [10, 10, 120, 80]
        bx, by, bw, bh = bubble_box
        bubble_mask = np.ones((bh, bw), np.uint8) if kind != "empty_bubble" else np.zeros((bh, bw), np.uint8)
        items.append(LayoutPrediction("bubble", .95, bubble_box, bubble_mask, (bx, by)))
    result, = build_layout_regions(items, 200, 200, {}, page_key="role")
    expected = "free_text" if kind == "free" else "dialogue" if kind == "assigned" else "unknown"
    assert result.metadata["text_role"]["label"] == expected
    assert (free_text_angle(result.metadata, result.bubble_id) is not None) == (kind == "free")


def setup(client):
    project = client.post("/api/projects", json={"name": "free-text-angle-regression"}).json()
    source = BytesIO()
    Image.new("RGB", (200, 200), "white").save(source, "PNG")
    page = client.post(f"/api/projects/{project['id']}/images",
                       files={"files": ("page.png", source.getvalue(), "image/png")}).json()[0]
    region = client.post(f"/api/images/{page['id']}/regions", json={
        "bbox": [20, 20, 80, 30], "orientation": "horizontal",
        "source_text": "original", "translated_text": "translation", "font_size": 32,
    }).json()
    return project, page, region


@pytest.mark.parametrize("protection", [None, "zero", "dialogue", "perspective", "legacy_reset", "locked"])
def test_redetection_rebuilds_typography_from_fresh_evidence_for_every_frame(client, monkeypatch, protection):
    _, page, row = setup(client)
    result = candidate()
    provider = SimpleNamespace(detect=Mock(return_value=[result]),
                               capabilities=SimpleNamespace(extra={"layout_instances": True}))
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda *_: (provider, "rfdetr"))
    if protection == "zero":
        response = client.patch(f"/api/regions/{row['id']}", json={"rotation": 0})
        assert response.status_code == 200
    with SessionLocal() as db:
        stored = db.get(ImagePage, page["id"])
        region = db.get(TextRegion, row["id"])
        region.pixel_mask_path = "unchanged-mask.png"
        region.inpainted_path = "unchanged-repair.png"
        if protection == "dialogue":
            region.layout_data = {**region.layout_data, "detection": evidence(role="dialogue")}
        elif protection == "perspective":
            region.perspective_warp = True
        elif protection == "locked":
            region.locked = True
        elif protection == "legacy_reset":
            region.rotation = 17.
            save_revision(db, region, "updated")
            region.rotation = 0.
        db.commit()
        processor = PipelineProcessor(db)
        processor.detect(stored, None, force=True)
        db.commit()
        assert len(stored.regions) == 1
        rebuilt = stored.regions[0]
        assert rebuilt.id != row['id']
        assert rebuilt.rotation == -28.
        assert not rebuilt.locked and not rebuilt.perspective_warp
        assert rebuilt.source_text == rebuilt.translated_text == ''
        assert rebuilt.pixel_mask_path is None and rebuilt.inpainted_path is None
        result.metadata = evidence(-12.)
        processor.detect(stored, None, force=True)
        db.commit()
        assert stored.regions[0].id != rebuilt.id
        assert stored.regions[0].rotation == -12.


def test_zero_angle_intent_survives_undo_redo(client):
    project, page, row = setup(client)
    response = client.patch(f"/api/regions/{row['id']}", json={"rotation": 0}).json()
    assert response["layout_data"][TEXT_ROTATION_KEY]["source"] == "user"
    history = client.get(f"/api/projects/{project['id']}/history").json()
    undone = client.post(f"/api/projects/{project['id']}/history/undo", json={
        "expected_entry_id": history["undo_id"], "current_image_id": page["id"],
    })
    assert undone.status_code == 200, undone.text
    restored = next(region for region in undone.json()["regions"] if region["id"] == row["id"])
    assert TEXT_ROTATION_KEY not in restored["layout_data"]
    history = undone.json()["history"]
    redone = client.post(f"/api/projects/{project['id']}/history/redo", json={
        "expected_entry_id": history["redo_id"], "current_image_id": page["id"],
    })
    assert redone.status_code == 200, redone.text
    restored = next(region for region in redone.json()["regions"] if region["id"] == row["id"])
    assert restored["rotation"] == 0.
    assert restored["layout_data"][TEXT_ROTATION_KEY]["source"] == "user"


def test_cached_free_angle_initializes_once_and_repair_preserves_it(client):
    _, page, row = setup(client)
    with SessionLocal() as db:
        region = db.get(TextRegion, row["id"])
        region.layout_data = {"detection": evidence()}
        processor = PipelineProcessor(db)
        assert processor._initialize_text_rotation(region)
        region.layout_data = {**region.layout_data, "translation_geometry_sync": {"pending": True}}
        assert synchronize_translation_geometry_after_repair(db, region)
        assert region.rotation == -28.
        assert not processor._initialize_text_rotation(region)
        assert region.layout_data[TEXT_ROTATION_KEY]["source"] == "detector"
