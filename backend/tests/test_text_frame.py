from math import cos, radians, sin

import numpy as np
import pytest
from app.services.layout.text_frame import aligned_text_frame
from app.services.layout.text_rotation import record_user_rotation
from app.services.rendering import PillowRenderer
from app.utils.geometry import polygon_to_bbox
from PIL import Image
from test_layout import simple_render_region


def rotated_box(angle, width=140, height=180):
    cosine, sine = cos(radians(angle)), sin(radians(angle))
    return [[180+x*cosine-y*sine, 180+x*sine+y*cosine]
            for x, y in [(-width/2, -height/2), (width/2, -height/2),
                         (width/2, height/2), (-width/2, height/2)]]


@pytest.mark.parametrize("angle", [-45, -12, 12, 45, 90, 180])
def test_text_frame_measures_local_axes_without_reordering_reading_direction(angle):
    polygon = rotated_box(angle)
    for points in (polygon, polygon[::-1], polygon[2:]+polygon[:2]):
        assert aligned_text_frame(points, angle) == [110., 90., 140., 180.]
    assert aligned_text_frame(polygon, 0) is None
    assert aligned_text_frame(polygon, angle+5) is None


def test_text_frame_rejects_irregular_degenerate_or_invalid_geometry():
    points = rotated_box(12)
    points[2][0] += 10
    assert aligned_text_frame(points, 12) is None
    assert aligned_text_frame(points[:3], 12) is None
    assert aligned_text_frame([[0, 0]]*4, 12) is None
    assert aligned_text_frame(rotated_box(12), float("nan")) is None
    points[0][1] = float("inf")
    assert aligned_text_frame(points, 12) is None


@pytest.mark.parametrize("angle", [-35, 12, 45])
@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_slanted_text_fits_before_rotation_without_shrinking_or_clipping(tmp_path, angle, orientation):
    bg, out, layer = [tmp_path/name for name in ("bg.png", "out.png", "layer.png")]
    Image.new("RGB", (360, 360), "white").save(bg)
    region = simple_render_region()
    region.orientation, region.translated_text = orientation, "MANGA TEXT"
    region.polygon = rotated_box(0)
    region.bbox = polygon_to_bbox(region.polygon)
    renderer = PillowRenderer()
    upright = renderer.render(bg, [region], out, layer)["layouts"][region.id]
    upright_alpha = np.asarray(Image.open(layer))[:, :, 3].sum()
    region.polygon, region.rotation = rotated_box(angle), angle
    region.bbox = polygon_to_bbox(region.polygon)
    slanted = renderer.render(bg, [region], out, layer)["layouts"][region.id]
    assert not slanted["overflow"] and not slanted["render_skipped"]
    assert not slanted["perspective_warp_applied"]
    assert slanted["font_size"] == upright["font_size"]
    assert slanted["lines"] == upright["lines"]
    alpha = np.asarray(Image.open(layer))[:, :, 3]
    # Rotating the tile must not cut off glyphs at its old horizontal edges.
    assert alpha.sum() / float(upright_alpha) == pytest.approx(1, abs=.03)
    ys, xs = np.nonzero(alpha > 8)
    assert xs.size
    cosine, sine = cos(radians(angle)), sin(radians(angle))
    local_x = (xs-180)*cosine+(ys-180)*sine
    local_y = -(xs-180)*sine+(ys-180)*cosine
    assert np.max(np.abs(local_x)) <= 72
    assert np.max(np.abs(local_y)) <= 92
    preview = Image.alpha_composite(Image.open(bg).convert("RGBA"), Image.open(layer)).convert("RGB")
    np.testing.assert_array_equal(np.asarray(preview), np.asarray(Image.open(out)))


def test_slanted_manual_font_size_can_overflow_its_frame(tmp_path):
    bg, out, layer = [tmp_path/name for name in ("bg.png", "out.png", "layer.png")]
    Image.new("RGB", (360, 360), "white").save(bg)
    region = simple_render_region()
    region.polygon, region.rotation = rotated_box(25, 30, 40), 25
    region.bbox = polygon_to_bbox(region.polygon)
    region.auto_font_size, region.font_size = False, 80
    result = PillowRenderer().render(bg, [region], out, layer)["layouts"][region.id]
    assert result["font_size"] == 80 and not result["render_skipped"]
    ys, xs = np.nonzero(np.asarray(Image.open(layer))[:, :, 3] > 8)
    assert xs.size and (xs.max()-xs.min() > 30 or ys.max()-ys.min() > 40)


@pytest.mark.parametrize("auto", [False, True])
@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_perspective_detector_angle_matches_zero_tile_rotation_but_user_angle_still_applies(tmp_path, auto, orientation):
    bg, out, layer = [tmp_path/name for name in ("bg.png", "out.png", "layer.png")]
    Image.new("RGB", (360, 360), "white").save(bg)
    region = simple_render_region()
    region.polygon = rotated_box(-28, 140, 80)
    region.bbox = polygon_to_bbox(region.polygon)
    region.perspective_warp, region.auto_font_size = True, auto
    region.orientation, region.translated_text = orientation, "MANGA"
    renderer = PillowRenderer()
    reference = renderer.render(bg, [region], out, layer)
    expected = np.asarray(Image.open(layer)).copy()
    region.rotation = -28.
    region.layout_data = {**region.layout_data, "text_rotation": {
        "version": 1, "source": "detector", "role": "free_text", "angle": -28.,
    }}
    automatic = renderer.render(bg, [region], out, layer)
    np.testing.assert_array_equal(np.asarray(Image.open(layer)), expected)
    assert automatic["layouts"][region.id]["font_size"] == reference["layouts"][region.id]["font_size"]
    assert automatic["inputs"][region.id]["text_rotation"]["source"] == "detector"
    record_user_rotation(region)
    authored = renderer.render(bg, [region], out, layer)
    assert not np.array_equal(np.asarray(Image.open(layer)), expected)
    assert authored["inputs"][region.id]["text_rotation"]["source"] == "user"
