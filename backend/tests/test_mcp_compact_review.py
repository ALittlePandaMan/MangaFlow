"""Compact image evidence preserves coverage, versions and agent-owned judgment."""
import base64
import io

import numpy as np
import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, MCPAsset
from app.services import mcp_tools, mcp_workflows
from app.storage import get_storage
from PIL import Image
from sqlalchemy import func, select
from test_mcp import page, rpc
from test_mcp_native_workflow import image_from, planned_evidence
from test_mcp_workflows import begin, evidence, ok, region, render_empty, review, state


@pytest.fixture(autouse=True)
def configured(client, monkeypatch):
    client.headers["Host"] = "localhost"
    monkeypatch.setattr(mcp_workflows, "blockers", lambda db, run, **kwargs: [])


def plan(client, image, phase="final", evidence_ids=()):
    return ok(rpc(client, "page_review_plan", {"image_id": image["id"], "phase": phase,
        "expected_version": state(client, image)["version"], "evidence_ids": list(evidence_ids)}))


def picture(client, image, **args):
    response = rpc(client, "page_preview", {"image_id": image["id"],
        "expected_version": state(client, image)["version"], **args})
    result = ok(response)
    images = [item for item in response["content"] if item["type"] == "image"]
    assert len(images) == 1 and images[0]["mimeType"] == "image/png"
    data = base64.b64decode(images[0]["data"])
    assert len(data) <= 2*1024**2
    return result, Image.open(io.BytesIO(data))


@pytest.mark.parametrize("legacy", [True, False])
def test_ordinary_page_needs_one_image_per_phase_without_changing_checks(client, legacy):
    project, image = page(client)
    render_empty(image)
    run = begin(client, project, legacy=legacy)
    before = state(client, image)["version"]
    for phase, kind in (("preflight", "annotated"), ("final", "comparison")):
        first = plan(client, image, phase)
        assert len(first["requests"]) == 1
        assert first["requests"][0]["arguments"]["kind"] == kind
        identifiers, ready = planned_evidence(client, image, phase)
        assert len(identifiers) == 1
        assert ready["evidence_ready"] and not ready["visual_review_performed"]
        assert all(value >= .98 for value in ready["readable_coverage"].values())
        ok(review(client, run, image, phase=phase, outcome="passed", evidence_ids=identifiers))
    assert state(client, image)["version"] == before
    assert not plan(client, image)["evidence_ready"]  # A receipt cannot replace image evidence.


@pytest.mark.parametrize("crop,layout", [([10, 20, 60, 100], "side_by_side"), ([10, 20, 160, 70], "stacked")])
def test_comparison_pixels_and_panel_coordinates_match_source_crop(client, crop, layout):
    project, _ = page(client)
    pixels = np.zeros((200, 300, 3), dtype=np.uint8)
    pixels[:, :, 0] = np.arange(300, dtype=np.uint16) % 256
    pixels[:, :, 1] = np.arange(200)[:, None]
    image = image_from(client, project, Image.fromarray(pixels))
    with SessionLocal() as db:
        row = db.get(ImagePage, image["id"])
        target = get_storage().absolute(row.original_path).with_name("comparison-render.png")
        Image.new("RGB", (300, 200), "#ff00ff").save(target)
        row.rendered_path = get_storage().relative(target)
        db.commit()
    metadata, result = picture(client, image, kind="comparison", crop=crop)
    assert metadata["layout"] == layout
    assert metadata["crop"] == crop and metadata["display_size"] == list(result.size)
    assert "original_pixels_per_display_pixel" not in metadata  # Whole composite is NOT a page coordinate plane.
    assert metadata["coordinate_space"] == "original_image_pixels"
    for panel, color in zip(metadata["panels"], [(crop[0], crop[1], 0), (255, 0, 255)], strict=True):
        x, y, width, height = panel["display_bounds"]
        assert panel["crop"] == crop
        assert panel["display_size"] == [width, height]
        assert panel["original_pixels_per_display_pixel"] == [crop[2]/width, crop[3]/height]
        assert result.getpixel((x, y)) == color
    with SessionLocal() as db:
        assert db.get(MCPAsset, metadata["preview_id"]).evidence["panels"] == metadata["panels"]


def test_region_comparison_includes_moved_rotated_translation_frame(client):
    _, image = page(client)
    text = region(client, image, bbox=[10, 20, 30, 40], translated_bbox=[180, 80, 60, 80], rotation=45)
    render_empty(image)
    metadata, _ = picture(client, image, kind="comparison", region_id=text["id"])
    x, y, width, height = metadata["crop"]
    assert x <= 10 and y <= 20
    assert x+width >= 250 and y+height >= 150


def test_final_plan_reuses_original_evidence_and_legacy_images(client):
    project, image = page(client)
    render_empty(image)
    run = begin(client, project)
    original, _ = picture(client, image, kind="annotated")
    missing = plan(client, image, evidence_ids=[original["preview_id"]])
    assert [item["arguments"]["kind"] for item in missing["requests"]] == ["rendered"]
    old_ids = evidence(client, image)
    assert plan(client, image, evidence_ids=old_ids)["evidence_ready"]
    ok(review(client, run, image, outcome="passed", evidence_ids=old_ids))


def test_unreadable_composite_requires_per_panel_tiles_before_final_admission(client):
    project, _ = page(client)
    image = image_from(client, project, Image.new("RGB", (400, 7000), "white"))
    render_empty(image)
    run = begin(client, project, image_ids=[image["id"]])
    overview, _ = picture(client, image, kind="comparison")
    identifiers = [overview["preview_id"]]
    assert all(max(item["original_pixels_per_display_pixel"]) > 2 for item in overview["panels"])
    assert review(client, run, image, outcome="passed", evidence_ids=identifiers)["isError"]
    missing = plan(client, image, evidence_ids=identifiers)
    assert not missing["evidence_ready"]
    assert all(item["arguments"]["kind"] == "comparison" for item in missing["requests"])
    identifiers, ready = planned_evidence(client, image, "final")
    assert len(identifiers) > 1 and ready["readable_coverage"] == {"original": 1.0, "rendered": 1.0}
    ok(review(client, run, image, outcome="passed", evidence_ids=identifiers))


def test_png_size_reduction_recalculates_each_panel_and_planner_recovers(client):
    project, _ = page(client)
    noise = np.random.default_rng(507).integers(0, 256, (1600, 1600, 3), dtype=np.uint8)
    image = image_from(client, project, Image.fromarray(noise))
    render_empty(image)
    metadata, result = picture(client, image, kind="comparison")
    assert max(result.size) < 2048  # The actual PNG cap forced further shrinking.
    for panel in metadata["panels"]:
        x, y, width, height = panel["display_bounds"]
        assert x+width <= result.width and y+height <= result.height
        assert panel["original_pixels_per_display_pixel"] == [1600/width, 1600/height]
    assert any(max(panel["original_pixels_per_display_pixel"]) > 2 for panel in metadata["panels"])
    identifiers, ready = planned_evidence(client, image, "final")
    assert len(identifiers) > 1
    assert all(coverage >= .98 for coverage in ready["readable_coverage"].values())


def test_comparison_never_substitutes_for_source_frame_inspection(client):
    project, image = page(client)
    render_empty(image)
    run = begin(client, project)
    comparison, _ = picture(client, image, kind="comparison")
    assert review(client, run, image, phase="preflight", outcome="passed",
        evidence_ids=[comparison["preview_id"]])["isError"]
    result = plan(client, image, "preflight", [comparison["preview_id"]])
    assert [item["arguments"]["kind"] for item in result["requests"]] == ["annotated"]


def test_comparison_rejects_concurrent_edits_before_publishing_evidence(client, monkeypatch):
    _, image = page(client)
    render_empty(image)
    original = mcp_tools.comparison_preview

    def changed(page_row, box, max_size):
        result = original(page_row, box, max_size)
        # Simulate a concurrent committed publication, without inheriting the
        # outer MCP request's ContextVars into a nested TestClient request.
        with SessionLocal() as db:
            row = db.get(ImagePage, image["id"])
            row.metadata_json = {**row.metadata_json, "external_edit": True}
            db.commit()
        return result

    monkeypatch.setattr(mcp_tools, "comparison_preview", changed)
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(MCPAsset))
    response = rpc(client, "page_preview", {"image_id": image["id"], "kind": "comparison"})
    assert response["isError"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(MCPAsset)) == before


def test_comparison_missing_output_does_not_fall_back_to_original(client):
    _, image = page(client)
    assert rpc(client, "page_preview", {"image_id": image["id"], "kind": "comparison"})["isError"]
    assert any(block["kind"] == "rendered" for block in plan(client, image)["blockers"])
