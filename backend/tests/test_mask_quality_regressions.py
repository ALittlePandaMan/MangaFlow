"""End-to-end regressions found while reviewing glyph masks and their audits."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from app.pipeline.processor import _generate_mask_job
from app.services import quality
from app.services.inpainting import masking
from app.services.inpainting.masking import MASK_ALGORITHM_VERSION, apply_balloon_constraint, create_text_mask
from app.services.inpainting.repair import _fill_color


@pytest.mark.parametrize("with_model", [False, True])
def test_small_real_cutout_cannot_authorize_its_own_erasure(tmp_path, with_model):
    image = np.full((600, 800, 3), 255, np.uint8)
    glyphs = np.zeros(image.shape[:2], np.uint8)
    glyphs[300:306, 240:350] = 255
    decoration = np.zeros_like(glyphs)
    cv2.circle(decoration, (380, 350), 3, 255, 1)
    image[(glyphs | decoration) > 0] = 0
    balloon = np.zeros_like(glyphs)
    balloon[40:560, 40:760] = 255
    cv2.circle(balloon, (380, 350), 3, 0, -1)
    output = tmp_path / "real-cutout.png"
    metadata = create_text_mask(
        Path("unused"), [[220, 280], [400, 280], [400, 370], [220, 370]], output,
        source_image=image, balloon_mask=balloon,
        text_probability=(glyphs > 0).astype(np.float32) if with_model else None,
    )
    mask = cv2.imread(str(output), 0)
    assert np.all(mask[decoration > 0] == 0)
    assert np.all(mask[glyphs > 0] == 255)
    assert metadata["constraint"]["recovered_holes"] == 0


def test_real_ink_continuation_can_recover_a_defect_without_a_model(tmp_path):
    image = np.full((600, 800, 3), 255, np.uint8)
    glyphs = np.zeros(image.shape[:2], np.uint8)
    glyphs[300:306, 240:350] = 255
    image[glyphs > 0] = 0
    balloon = np.zeros_like(glyphs)
    balloon[40:560, 40:760] = 255
    balloon[301:304, 279:282] = 0
    output = tmp_path / "stroke.png"
    metadata = create_text_mask(Path("unused"), [[220, 280], [400, 280], [400, 330], [220, 330]], output,
        source_image=image, balloon_mask=balloon)
    mask = cv2.imread(str(output), 0)
    assert np.array_equal(mask, glyphs)
    assert metadata["constraint"]["recovered_holes"] == 1


@pytest.mark.parametrize("decoration", [False, True])
def test_tip_confirmation_does_not_require_paper_to_be_text_or_erase_a_nearby_icon(tmp_path, decoration):
    image = np.full((600, 800, 3), 255, np.uint8)
    glyphs = np.zeros(image.shape[:2], np.uint8)
    glyphs[300:306, 240:350] = 255
    image[glyphs > 0] = 0
    probability = (glyphs > 0).astype(np.float32)
    probability[299:308, 347:360] = np.maximum(probability[299:308,347:360], .3)
    balloon = np.zeros_like(glyphs)
    balloon[40:560, 40:760] = 255
    icon = np.zeros_like(glyphs)
    if decoration:
        cv2.circle(icon, (355, 303), 3, 255, 1)
        image[icon > 0] = 0
        cv2.circle(balloon, (355, 303), 3, 0, -1)
    else:
        image[302, 350] = 210  # Real antialiased tip, next to a mostly white defect.
        balloon[302:305, 352:355] = 0
    output = tmp_path / "tip.png"
    metadata = create_text_mask(Path("unused"), [[220, 280], [400, 280], [400, 330], [220, 330]], output,
        source_image=image, balloon_mask=balloon, text_probability=probability)
    mask = cv2.imread(str(output), 0)
    assert np.all(mask[glyphs > 0] == 255)
    if decoration:
        assert np.all(mask[icon > 0] == 0)
        assert metadata["constraint"]["recovered_holes"] == 0
    else:
        assert mask[302,350] == 255
        assert metadata["constraint"]["recovered_holes"] == 1
        assert metadata["constraint"]["glyph_safe_retention"] == 1


@pytest.mark.parametrize("background,ink", [(200, 0), (20, 245), ((100, 170, 210), (10, 10, 10))])
@pytest.mark.parametrize("conservative", [False, True])
def test_partial_model_keeps_aligned_clear_text_on_uniform_colored_paper(tmp_path, background, ink, conservative):
    image = np.full((220, 220, 3), background, np.uint8)
    glyphs = np.zeros(image.shape[:2], np.uint8)
    glyphs[60:140, 80:86] = 255
    glyphs[60:140, 110:116] = 255
    image[glyphs > 0] = ink
    probability = np.zeros_like(glyphs, dtype=np.float32)
    probability[60:140, 80:86] = .95
    output = tmp_path / "partial.png"
    create_text_mask(Path("unused"), [[60, 40], [140, 40], [140, 160], [60, 160]], output,
        source_image=image, conservative=conservative, text_probability=probability)
    assert np.array_equal(cv2.imread(str(output), 0), glyphs)


def test_partial_model_does_not_slice_one_connected_character(tmp_path):
    image = np.full((220, 220, 3), 200, np.uint8)
    glyphs = np.zeros(image.shape[:2], np.uint8)
    cv2.putText(glyphs, "H", (55, 145), cv2.FONT_HERSHEY_SIMPLEX, 2, 255, 3)
    image[glyphs > 0] = 0
    probability = (glyphs > 0).astype(np.float32)
    probability[:, 90:] = 0
    output = tmp_path / "half-H.png"
    create_text_mask(Path("unused"), [[40, 80], [125, 80], [125, 165], [40, 165]], output,
        source_image=image, conservative=True, text_probability=probability)
    assert np.array_equal(cv2.imread(str(output), 0), glyphs)


def test_unresolved_foreground_is_not_silently_reported_complete(tmp_path):
    image = np.full((220, 220, 3), 200, np.uint8)
    glyphs = np.zeros(image.shape[:2], np.uint8)
    glyphs[60:140, 80:86] = 255
    glyphs[155:158, 125:128] = 255  # Could be punctuation or a small drawing.
    image[glyphs > 0] = 0
    probability = np.zeros_like(glyphs, dtype=np.float32)
    probability[60:140, 80:86] = .95
    output = tmp_path / "uncertain.png"
    metadata = _generate_mask_job(Path("unused"), output, [[60, 40], [145, 40], [145, 175], [60, 175]], [], dict(
        source_image=image, conservative=True, text_probability=probability,
    ), None, {"status": "test"})
    assert metadata["mask_integrity"]["status"] == "uncertain"
    assert metadata["segmentation_disagreement"]["unconfirmed_ink_pixels"] == 9
    assert _fill_color(cv2.imread(str(output), 0), metadata) is None


@pytest.mark.parametrize("background", [200, 240])
def test_color_recovery_does_not_release_disconnected_unrelated_artwork(tmp_path, background):
    image = np.full((180, 150, 3), background, np.uint8)
    image[40:90, 45:51] = 15
    image[115:140, 85:95] = 15
    probability = np.zeros(image.shape[:2], np.float32)
    probability[40:90, 45:51] = 1
    output = tmp_path / "artwork.png"
    create_text_mask(Path("unused"), [[35, 20], [105, 20], [105, 160], [35, 160]], output,
        source_image=image, text_probability=probability)
    mask = cv2.imread(str(output), 0)
    assert np.all(mask[40:90, 45:51] == 255)
    assert np.all(mask[115:140, 85:95] == 0)


@pytest.mark.parametrize("left", [30, 40])
def test_confirmed_letter_near_wall_is_not_itself_the_wall(tmp_path, left):
    image = np.full((220, 300, 3), 255, np.uint8)
    glyphs = np.zeros(image.shape[:2], np.uint8)
    cv2.putText(glyphs, "H", (left, 160), cv2.FONT_HERSHEY_SIMPLEX, 4, 255, 5)
    image[glyphs > 0] = 0
    balloon = np.zeros_like(glyphs)
    balloon[40:180, 40:260] = 255
    x, y, w, h = cv2.boundingRect(cv2.findNonZero(glyphs))
    polygon = [[x-3, y-3], [x+w+3, y-3], [x+w+3, y+h+3], [x-3, y+h+3]]
    output = tmp_path / "H.png"
    metadata = create_text_mask(Path("unused"), polygon, output, source_image=image,
        balloon_mask=balloon, text_probability=(glyphs > 0).astype(np.float32))
    assert np.array_equal(cv2.imread(str(output), 0), glyphs)
    assert metadata["constraint"]["glyph_safe_retention"] == 1
    assert metadata["constraint"]["status"] in {"applied", "relaxed"}


@pytest.mark.parametrize("grouped", [False, True])
def test_final_polygon_clip_reaudits_written_mask_and_disables_trusted_fill(tmp_path, grouped):
    image = np.full((600, 800, 3), 255, np.uint8)
    glyphs = np.zeros(image.shape[:2], np.uint8)
    glyphs[300:306, 240:350] = 255
    image[glyphs > 0] = 0
    balloon = np.zeros_like(glyphs)
    balloon[40:560, 40:760] = 255
    polygon = [[200, 260], [390, 260], [390, 340], [300, 340], [300, 296], [290, 296], [290, 340], [200, 340]]
    sources = [[[220, 280], [370, 280], [370, 325], [220, 325]]] if grouped else []
    output = tmp_path / "final.png"
    metadata = _generate_mask_job(Path("unused"), output, polygon, sources, dict(
        source_image=image, balloon_mask=balloon, text_probability=(glyphs > 0).astype(np.float32),
    ), None, {"status": "test"})
    mask = cv2.imread(str(output), 0)
    inside = np.zeros_like(glyphs)
    cv2.fillPoly(inside, [np.asarray(polygon, np.int32)], 255)
    assert not np.any(mask[inside == 0])
    assert cv2.connectedComponents((mask > 0).astype(np.uint8), connectivity=8)[0] == 3
    constraint = metadata["constraint"]
    assert constraint["final_mask_area"] == cv2.countNonZero(mask)
    assert constraint["glyph_safe_retention"] < 1
    assert constraint["fragmented_glyph_components"] == 1
    assert constraint["status"] == "partial"
    assert metadata["mask_integrity"]["status"] == "partial"
    assert metadata["region_boundary"]["clipped_pixels"] > 0
    assert _fill_color(mask, metadata) is None


def test_partial_final_integrity_cannot_reenable_a_flat_fill():
    mask = np.full((10, 10), 255, np.uint8)
    metadata = dict(version=MASK_ALGORITHM_VERSION, method="balloon_glyph", constraint={"status": "applied"},
        background={"uniform": True}, repair={"strategy": "uniform_fill", "color_rgb": [255, 255, 255],
        "mask_sha256": hashlib.sha256(mask.tobytes()).hexdigest()}, mask_integrity={"status": "partial"})
    assert _fill_color(mask, metadata) is None


def test_safe_margin_prefers_complete_strokes_over_high_total_retention(monkeypatch):
    balloon = np.zeros((1000, 1000), np.uint8)
    balloon[50:950, 50:950] = 255
    glyphs = np.zeros_like(balloon)
    glyphs[498:502, 58:800] = 255
    monkeypatch.setattr(masking, "_estimate_balloon_outline_width", lambda *_args, **_kwargs: (12, True))
    mask, metadata = apply_balloon_constraint(glyphs, balloon, glyph_evidence=glyphs)
    assert metadata["requested_margin_px"] == 12
    assert metadata["safe_margin_px"] == 8
    assert metadata["glyph_safe_retention"] == 1
    assert np.array_equal(mask, glyphs)


def test_final_integrity_warning_is_visible_and_clears_after_regeneration(monkeypatch):
    region = SimpleNamespace(id="r", region_key="R001", confidence=.99, locked=False,
        source_text="正常文字", translated_text="正常译文", font_size=24, layout_warning=False,
        bbox=[10, 10, 40, 70], pixel_mask_path="mask.png", orientation="vertical", bubble_id=None,
        layout_data={"ocr": {"confidence_available": False}, "repair_quality": {"status": "passed"},
            "mask_generation": {"mask_integrity": {"status": "partial"}}})
    page = SimpleNamespace(id="p", project_id="project", width=200, height=200, regions=[region], clean_path="clean.png")
    monkeypatch.setattr(quality, "get_storage", lambda: SimpleNamespace(absolute=Path))
    monkeypatch.setattr(quality, "mask_is_empty", lambda _: False)
    assert "incomplete_text_mask" in {issue.code for issue in quality.evaluate_page(page)}
    assert "incomplete_text_mask" in region.review_reasons
    assert region.needs_review
    region.layout_data["mask_generation"]["mask_integrity"]["status"] = "passed"
    assert "incomplete_text_mask" not in {issue.code for issue in quality.evaluate_page(page)}
    assert not region.needs_review


def test_manual_mask_edits_drop_stale_automatic_diagnostics():
    from app.api.regions import _mark_manual_mask_edit
    region = SimpleNamespace(inpainted_path="old.png", layout_data={"mask_generation": {
        "method": "balloon_glyph", "mask_integrity": {"status": "partial"},
        "segmentation_disagreement": {"unconfirmed_ink_pixels": 9},
    }})
    _mark_manual_mask_edit(region)
    generation = region.layout_data["mask_generation"]
    assert generation["method"] == "manual_mask"
    assert generation["repair_pending"] is True
    assert "mask_integrity" not in generation
    assert "segmentation_disagreement" not in generation
    assert region.inpainted_path is None


@pytest.mark.parametrize("seed", range(32))
def test_generated_masks_remain_bounded_without_painting_protected_art(tmp_path, seed):
    rng = np.random.default_rng(seed)
    image = np.full((600, 800, 3), 255, np.uint8)
    glyphs = np.zeros(image.shape[:2], np.uint8)
    glyphs[300:306, 240:350] = 255
    glyphs[340:346, 240:350] = 255
    decoration = np.zeros_like(glyphs)
    cv2.circle(decoration, (380, 350), 3, 255, 1)
    image[(glyphs | decoration) > 0] = 0
    balloon = np.zeros_like(glyphs)
    balloon[40:560, 40:760] = 255
    balloon[301:304, 279:282] = 0
    cv2.circle(balloon, (380, 350), 3, 0, -1)
    polygons = [
        [[220, 280], [400, 280], [400, 380], [220, 380]],
        [[220, 280], [400, 380], [220, 380], [400, 280]],
        [[220, 280], [400, 280], [400, 380], [300, 380], [300, 296], [290, 296], [290, 380], [220, 380]],
        [[-50, -50], [900, -50], [900, 700], [-50, 700]],
        [[240, 300], [350, 300], [350, 300]],
        [[220, 280], [400, 330], [220, 380]],
    ]
    polygon = polygons[seed] if seed < len(polygons) else rng.integers([180, 250], [420, 400], size=(6, 2)).tolist()
    output = tmp_path / f"stress-{seed}.png"
    original = image.copy()
    metadata = _generate_mask_job(Path("unused"), output, polygon, [], dict(
        source_image=image, balloon_mask=balloon, text_probability=(glyphs > 0).astype(np.float32),
    ), None, {"status": "test"})
    mask = cv2.imread(str(output), 0)
    boundary = np.zeros_like(mask)
    cv2.fillPoly(boundary, [np.asarray(polygon, np.int32)], 255)
    assert not np.any(mask[boundary == 0])
    assert not np.any(mask[glyphs == 0])
    assert not np.any(mask[decoration > 0])
    assert np.array_equal(image, original)
    if np.all(boundary[glyphs > 0] > 0):
        assert np.all(mask[glyphs > 0] > 0)
    if metadata.get("mask_integrity", {}).get("clipped_glyph_pixels"):
        assert metadata["mask_integrity"]["status"] == "partial"
        assert _fill_color(mask, metadata) is None
