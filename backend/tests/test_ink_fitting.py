import numpy as np
import pytest
from PIL import Image

from app.services.layout.engine import FontResolver, MangaLayoutEngine
from app.services.rendering import PillowRenderer
from test_layout import available_font, simple_render_region


@pytest.fixture
def engine():
    return MangaLayoutEngine(FontResolver([available_font().parent]))


def fitted(engine, text, box=(160, 140), **kwargs):
    return engine.layout(text, [0, 0, *box], orientation=kwargs.pop("orientation", "horizontal"),
        font_family="", custom_font_path=str(available_font()), preferred_size=kwargs.pop("preferred_size", 28),
        line_spacing=1.15, character_spacing=kwargs.pop("character_spacing", 0), alignment="center", **kwargs)


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
@pytest.mark.parametrize("text", ["x", "漫画", "MANGA TEXT", "天地玄黄宇宙洪荒", "Hello\n\nWORLD!", "…!?ABC"])
def test_fits_actual_glyphs_and_next_size_would_overflow(engine, text, orientation):
    result = fitted(engine, text, orientation=orientation)
    assert not result.overflow
    assert result.width <= 158 and result.height <= 138
    assert all(g.ink_box is not None for g in result.placements)
    # Same algorithm without the search, tested in the effective frame.
    bigger = fitted(engine, text, box=(158, 138), orientation=orientation,
                    preferred_size=result.font_size+1, auto_fit=False)
    assert bigger.overflow


def test_single_lowercase_glyph_is_not_limited_to_em_height(engine):
    result = fitted(engine, "x", box=(100, 50))
    assert result.font_size > 50
    assert max(result.width/98, result.height/48) > .96


def test_visible_frame_not_polygon_area_controls_size(engine):
    reference = fitted(engine, "漫画翻译")
    triangle = fitted(engine, "漫画翻译", polygon=[[0, 0], [160, 0], [80, 140]])
    assert triangle.font_size == reference.font_size


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_explicit_paragraphs_and_user_selected_size_survive(engine, orientation):
    result = fitted(engine, "甲乙\n\n丙丁", orientation=orientation)
    assert "" in result.lines
    manual = fitted(engine, "甲乙\n\n丙丁", box=(158, 138), orientation=orientation,
                    preferred_size=result.font_size, auto_fit=False)
    assert manual.lines == result.lines
    assert manual.width == result.width and manual.height == result.height
    assert manual.placements == result.placements


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
@pytest.mark.parametrize("stroke", [0, 1.5, 6])
@pytest.mark.parametrize("text", ["x", "ABC!?", "漫画翻译"])
def test_ink_export_fills_frame_with_no_cropped_pixels(tmp_path, text, stroke, orientation):
    region = simple_render_region()
    region.bbox = [40, 40, 160, 140]
    region.polygon = [[40,40], [200,40], [200,180], [40,180]]
    region.translated_text, region.stroke_width, region.orientation = text, stroke, orientation
    bg, out, layer = (tmp_path/name for name in ("bg.png", "out.png", "layer.png"))
    Image.new("RGB", (240, 220), "white").save(bg)
    renderer = PillowRenderer()
    result = renderer.render(bg, [region], out, layer)["layouts"][region.id]
    assert not result["overflow"] and not result["render_skipped"]
    alpha = np.asarray(Image.open(layer))[:, :, 3]
    ys, xs = np.nonzero(alpha)
    assert 40 < xs.min() <= xs.max() < 200
    assert 40 < ys.min() <= ys.max() < 180
    assert max((xs.max()-xs.min()+1)/160, (ys.max()-ys.min()+1)/140) > .83


def test_repeated_glyphs_share_ink_measurement(engine, monkeypatch):
    calls = []
    original = engine._ink_bounds
    monkeypatch.setattr(engine, "_ink_bounds", lambda path, size, text: calls.append((size,text)) or original(path,size,text))
    fitted(engine, "漫画"*60)
    assert len(calls) == len(set(calls))
    assert len(calls) <= 22
