"""Box padding/search margins must never become extra repair-mask thickness."""
from pathlib import Path

import cv2
import numpy as np
import pytest
from app.services.detection.layout import build_layout_regions
from app.services.detection.text_roles import LayoutPrediction
from app.services.inpainting.masking import create_text_mask, create_text_mask_union
from app.services.inpainting.text_segmentation import learned_text_mask


@pytest.mark.parametrize("in_balloon", [False, True])
@pytest.mark.parametrize("segmented", [False, True])
@pytest.mark.parametrize("background, ink", [(245, 15), (15, 245)])
def test_frame_padding_does_not_thicken_glyphs(tmp_path, in_balloon, segmented, background, ink):
    image = np.full((280, 320, 3), background, np.uint8)
    glyphs = np.zeros(image.shape[:2], np.uint8)
    glyphs[100:160, 120:123] = 255
    glyphs[100:160, 128:131] = 255  # Preserve even this narrow inter-stroke gap.
    image[glyphs > 0] = ink
    balloon = np.zeros_like(glyphs)
    cv2.ellipse(balloon, (160, 140), (145, 125), 0, 0, 360, 255, -1)
    predictions = [LayoutPrediction("text", .95, [105, 80, 45, 100], glyphs, (0, 0))]
    if in_balloon:
        predictions.append(LayoutPrediction("bubble", .99, [15, 15, 290, 250], balloon, (0, 0)))
    for index, padding in enumerate((0, 3, 8, 50, {"top": 0, "bottom": 25, "left": 10, "right": 40})):
        detected, = build_layout_regions(predictions, 320, 280, {"text_box_padding": padding}, page_key="test")
        polygon = detected.polygon
        output = tmp_path / f"mask-{index}.png"
        metadata = create_text_mask(Path("unused"), polygon, output, source_image=image,
            balloon_mask=balloon if in_balloon else None,
            text_probability=(glyphs > 0).astype(np.float32) if segmented else None)

        assert np.array_equal(cv2.imread(str(output), 0), glyphs)
        assert metadata["glyph_expansion_px"] == 0


@pytest.mark.parametrize("in_balloon", [False, True])
def test_union_does_not_expand_individual_lines(tmp_path, in_balloon):
    image = np.full((240, 300, 3), 245, np.uint8)
    glyphs = np.zeros(image.shape[:2], np.uint8)
    glyphs[80:160, 106:110] = 255
    glyphs[80:160, 166:170] = 255
    image[glyphs > 0] = 0
    balloon = np.zeros_like(glyphs)
    cv2.ellipse(balloon, (150, 120), (130, 105), 0, 0, 360, 255, -1)
    polygons = [[[90, 60], [130, 60], [130, 180], [90, 180]],
                [[150, 60], [190, 60], [190, 180], [150, 180]]]
    output = tmp_path / "union.png"
    metadata = create_text_mask_union(Path("unused"), polygons, output, source_image=image,
        balloon_mask=balloon if in_balloon else None)

    assert np.array_equal(cv2.imread(str(output), 0), glyphs)
    assert metadata["glyph_expansion_px"] == 0


@pytest.mark.parametrize("margin", [0, 2, 6, 12])
def test_model_search_margin_never_adds_unsegmented_pixels(margin):
    probability = np.zeros((100, 100), np.float32)
    probability[30:70, 40:42] = .95
    probability[40:60, 39] = .4  # Connected weak antialiasing is still evidence.
    region = np.zeros(probability.shape, np.uint8)
    region[20:80, 25:75] = 255

    assert np.array_equal(learned_text_mask(probability, region, search_margin=margin),
                          (probability >= .25).astype(np.uint8) * 255)
