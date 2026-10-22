"""Small segmentation holes must not become cuts through text strokes."""
from pathlib import Path

import cv2
import numpy as np
import pytest
from app.services.inpainting.masking import apply_balloon_constraint, create_text_mask


def scene(background=255, ink=0):
    image = np.full((600, 800, 3), background, np.uint8)
    balloon = np.zeros(image.shape[:2], np.uint8)
    balloon[40:560, 40:760] = 255
    glyphs = np.zeros_like(balloon)
    glyphs[300:306, 200:360] = 255
    glyphs[275:330, 230:236] = 255
    glyphs[360:366, 250:410] = 255
    image[glyphs > 0] = ink
    polygon = [[190, 265], [420, 265], [420, 380], [190, 380]]
    return image, balloon, glyphs, polygon


def constrain(image, balloon, glyphs, polygon):
    return apply_balloon_constraint(
        glyphs, balloon, glyph_evidence=glyphs,
        source_lab=cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32),
        source_polygons=[polygon],
    )


@pytest.mark.parametrize("background,ink", [(255, 0), (10, 245)])
def test_small_internal_holes_recover_ink_without_thickening(tmp_path: Path, background, ink):
    image, balloon, glyphs, polygon = scene(background, ink)
    balloon[301:304, 279:282] = 0
    balloon[361:364, 299:302] = 0
    original_balloon = balloon.copy()
    original_glyphs = glyphs.copy()
    path = tmp_path / "mask.png"
    metadata = create_text_mask(
        Path("unused"), polygon, path, source_image=image,
        balloon_mask=balloon, text_probability=(glyphs > 0).astype(np.float32),
    )
    actual = cv2.imread(str(path), 0)

    assert np.array_equal(actual, glyphs)
    assert np.array_equal(balloon, original_balloon)
    assert np.array_equal(glyphs, original_glyphs)
    assert metadata["glyph_expansion_px"] == 0
    constraint = metadata["constraint"]
    assert constraint["recovered_holes"] == 2
    assert constraint["recovered_hole_pixels"] == 18
    assert constraint["glyph_safe_retention"] == 1
    assert constraint["fragmented_glyph_components"] == 0
    assert constraint["status"] in {"applied", "relaxed"}
    assert not np.any(actual[original_balloon == 0] & ~glyphs[original_balloon == 0])


def test_large_internal_hole_is_not_authorized_by_glyph_prediction():
    image, balloon, glyphs, polygon = scene()
    balloon[285:325, 260:310] = 0
    actual, metadata = constrain(image, balloon, glyphs, polygon)

    assert metadata["recovered_holes"] == 0
    assert not np.any(actual[balloon == 0])
    assert metadata["status"] == "partial"
    assert metadata["reason"] in {"glyph_strokes_clipped", "core_glyph_evidence_outside_balloon"}
    assert metadata["fragmented_glyph_components"] >= 1
    assert metadata["glyph_safe_retention"] < 1


def test_unrelated_small_hole_without_local_text_support_stays_protected():
    image, balloon, glyphs, polygon = scene()
    balloon[150:153, 150:153] = 0
    actual, metadata = constrain(image, balloon, glyphs, polygon)

    assert metadata["recovered_holes"] == 0
    assert np.array_equal(actual, glyphs)


@pytest.mark.parametrize("diagonal", [False, True])
def test_open_notches_including_diagonal_connections_are_not_filled(diagonal):
    image, balloon, glyphs, polygon = scene()
    if diagonal:
        # The tiny cavity is connected to the exterior by a diagonal-only gap.
        for offset in range(261):
            balloon[40+offset, 20+offset] = 0
    else:
        balloon[40:304, 279:282] = 0
    actual, metadata = constrain(image, balloon, glyphs, polygon)

    assert metadata["recovered_holes"] == 0
    assert not np.any(actual[balloon == 0])


@pytest.mark.parametrize("missing", ["image", "glyphs"])
def test_hole_recovery_requires_both_source_pixels_and_glyph_evidence(missing):
    image, balloon, glyphs, polygon = scene()
    balloon[301:304, 279:282] = 0
    actual, metadata = apply_balloon_constraint(
        glyphs, balloon,
        glyph_evidence=glyphs if missing != "glyphs" else None,
        source_lab=cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32) if missing != "image" else None,
        source_polygons=[polygon],
    )

    assert metadata["recovered_holes"] == 0
    assert not np.any(actual[balloon == 0])


def test_textured_hole_surroundings_are_not_treated_as_plain_paper():
    image, balloon, glyphs, polygon = scene()
    balloon[301:304, 279:282] = 0
    # Visible non-text screen tone near the defect must veto hole recovery.
    image[290:315:2, 268:295:2] = 100
    actual, metadata = constrain(image, balloon, glyphs, polygon)

    assert metadata["recovered_holes"] == 0
    assert not np.any(actual[balloon == 0])


def test_high_total_retention_cannot_hide_a_severed_stroke():
    balloon = np.zeros((1000, 1000), np.uint8)
    balloon[50:950, 50:950] = 255
    glyphs = np.zeros_like(balloon)
    glyphs[498:502, 100:900] = 255
    balloon[500, 500] = 0
    # Missing source pixels forbid automatic recovery, but must not hide loss.
    actual, metadata = apply_balloon_constraint(glyphs, balloon, glyph_evidence=glyphs)

    assert metadata["glyph_safe_retention"] > .98
    assert metadata["glyph_safe_retention"] < 1
    assert metadata["fragmented_glyph_components"] == 1
    assert metadata["status"] == "partial"
    assert metadata["reason"] == "glyph_strokes_clipped"
    assert not np.any(actual[balloon == 0])


def test_high_total_retention_cannot_hide_one_entire_missing_component():
    balloon = np.zeros((1000, 1000), np.uint8)
    balloon[50:950, 50:950] = 255
    glyphs = np.zeros_like(balloon)
    glyphs[348:352, 100:900] = 255
    glyphs[499:502, 499:502] = 255
    balloon[500, 500] = 0
    _, metadata = apply_balloon_constraint(glyphs, balloon, glyph_evidence=glyphs)

    assert metadata["glyph_safe_retention"] > .99
    assert metadata["missing_glyph_components"] == 1
    assert metadata["glyph_component_min_retention"] == 0
    assert metadata["status"] == "partial"


def test_internal_hole_clipping_only_a_stroke_tip_is_still_reported():
    balloon = np.zeros((1000, 1000), np.uint8)
    balloon[50:950, 50:950] = 255
    glyphs = np.zeros_like(balloon)
    glyphs[400:408, 100:800] = 255
    balloon[398, 500] = 0
    _, metadata = apply_balloon_constraint(glyphs, balloon, glyph_evidence=glyphs)

    assert metadata["glyph_safe_retention"] > .99
    assert metadata["fragmented_glyph_components"] == 0
    assert metadata["missing_glyph_components"] == 0
    assert metadata["internal_hole_clipped_pixels"] > 0
    assert metadata["status"] == "partial"
    assert metadata["reason"] == "glyph_strokes_clipped"


def test_outline_ink_and_original_exterior_remain_protected():
    image, balloon, glyphs, polygon = scene()
    # A real wall crosses the source frame and is deliberately overpredicted.
    image[40:560, 40:45] = 0
    glyphs[40:560, 40:45] = 255
    balloon[301:304, 41:44] = 0
    actual, metadata = constrain(image, balloon, glyphs, polygon)

    assert metadata["outline_protected_pixels"] > 0
    assert not np.any(actual[:, 40:45])
    assert not np.any(actual[:40])
    assert not np.any(actual[560:])
    assert not np.any(actual[:, :40])
    assert not np.any(actual[:, 760:])


def test_crop_edge_balloon_has_the_same_hole_recovery_as_padded_page():
    image, balloon, glyphs, polygon = scene()
    balloon[301:304, 279:282] = 0
    actual, metadata = constrain(image, balloon, glyphs, polygon)
    # A compact instance that touches the page edge still has an exterior.
    compact_polygon = [[x-40, y-40] for x, y in polygon]
    compact, compact_meta = constrain(image[40:560,40:760], balloon[40:560,40:760], glyphs[40:560,40:760], compact_polygon)

    assert np.array_equal(compact, actual[40:560,40:760])
    assert metadata["recovered_holes"] == compact_meta["recovered_holes"] == 1
