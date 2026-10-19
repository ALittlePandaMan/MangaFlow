from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.models import TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services import quality
from app.services.detection.text_roles import is_confirmed_free_text
from app.utils.geometry import bbox_to_polygon
from PIL import Image, ImageDraw
from test_free_text_rotation import evidence


@pytest.mark.parametrize("damage", [None, "role", "sfx", "excluded", "assignment", "method", "bubble", "detector"])
def test_mask_generation_only_confirms_authoritative_free_text(tmp_path, damage):
    data = evidence()
    bubble_id = None
    if damage == "role":
        data["text_role"]["label"] = "unknown"
    elif damage == "sfx":
        data["text_classification"]["label"] = "onomatopoeia"
    elif damage == "excluded":
        data["text_classification"]["excluded"] = True
    elif damage == "assignment":
        data["balloon_assignment"]["status"] = "ambiguous"
    elif damage == "method":
        data["balloon_assignment"].pop("method")
    elif damage == "bubble":
        bubble_id = "bubble-1"
    elif damage == "detector":
        data["detector"] = "legacy"
    original = tmp_path / "original.png"
    image = Image.new("RGB", (140, 140), "white")
    ImageDraw.Draw(image).rectangle((48, 30, 54, 90), fill="black")
    image.save(original)
    region = TextRegion(id="region-1", image_id="page-1", region_key="R001", reading_order=0,
        bbox=[30, 20, 50, 90], polygon=bbox_to_polygon([30, 20, 50, 90]), bubble_id=bubble_id,
        layout_data={"detection": data})
    page = SimpleNamespace(id="page-1", project_id="project-1", original_path=str(original),
        width=140, height=140, regions=[region], metadata_json={}, clean_path=None,
        rendered_path=None, text_layer_path=None)
    processor = PipelineProcessor(Mock())
    processor.settings = SimpleNamespace(text_segmentation_enabled=False)
    processor.storage = SimpleNamespace(page_dir=lambda *_: tmp_path, absolute=Path, relative=str)
    processor.generate_masks(page, force=True, region_id=None, options={})
    constraint = region.layout_data["mask_generation"]["constraint"]
    if damage is None:
        assert constraint == {"version": 1, "status": "conservative", "reason": "outside_balloon"}
    else:
        assert constraint["status"] == "skipped"
    assert is_confirmed_free_text(data, bubble_id) == (damage is None)
    assert Path(region.pixel_mask_path).is_file()


def quality_codes(monkeypatch, constraint, *, damage=None, bubble_id=None):
    data = evidence()
    if damage:
        data["text_role"]["label"] = damage
    region = SimpleNamespace(id="region-1", region_key="R001", confidence=.99, locked=False,
        source_text="正常文字", translated_text="正常译文", font_size=24, layout_warning=False,
        bbox=[10, 10, 40, 70], pixel_mask_path="mask.png", orientation="vertical", bubble_id=bubble_id,
        layout_data={"ocr": {"confidence_available": False}, "detection": data,
                     "mask_generation": {"method": "conservative_glyph", "constraint": constraint},
                     "repair_quality": {"status": "passed"}})
    page = SimpleNamespace(id="page-1", project_id="project-1", width=200, height=200,
                           regions=[region], clean_path="clean.png")
    monkeypatch.setattr(quality, "get_storage", lambda: SimpleNamespace(absolute=Path))
    monkeypatch.setattr(quality, "mask_is_empty", lambda _: False)
    return {issue.code for issue in quality.evaluate_page(page)}


def test_existing_free_text_false_warning_is_corrected_without_regenerating_masks(monkeypatch):
    constraint = {"status": "skipped", "reason": "balloon_assignment_unassigned"}
    assert "repair_constraint_uncertain" not in quality_codes(monkeypatch, constraint)
    for damage in ["unknown", "dialogue", "onomatopoeia"]:
        assert "repair_constraint_uncertain" in quality_codes(monkeypatch, constraint, damage=damage)
    assert "repair_constraint_uncertain" in quality_codes(monkeypatch, constraint, bubble_id="bubble-1")


@pytest.mark.parametrize("constraint", [
    {"status": "fallback", "reason": "geometry_artifact_invalid"},
    {"status": "partial", "reason": "balloon_assignment_unassigned"},
    {"status": "skipped", "reason": "ambiguous_balloon_assignment"},
    {"status": "skipped", "reason": "geometry_unavailable"},
])
def test_real_constraint_failures_still_warn_for_free_text(monkeypatch, constraint):
    assert "repair_constraint_uncertain" in quality_codes(monkeypatch, constraint)


def test_false_warning_exemption_never_hides_outside_mask_pixels(monkeypatch):
    codes = quality_codes(monkeypatch, {"status": "skipped", "reason": "balloon_assignment_unassigned",
                                        "outside_pixels_after": 2})
    assert "repair_constraint_uncertain" not in codes
    assert "repair_outside_balloon" in codes
