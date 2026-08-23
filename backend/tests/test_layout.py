import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.services.layout.engine import FontResolver, MangaLayoutEngine
from app.services.rendering import PillowRenderer
from PIL import Image, ImageCms


def available_font() -> Path:
    resolver = FontResolver()
    if not resolver.paths:
        pytest.skip("No system TrueType font available")
    return resolver.paths[0]


def test_horizontal_font_fit_and_overflow() -> None:
    font = available_font()
    engine = MangaLayoutEngine(FontResolver([font.parent]), min_font_size=10)
    fitted = engine.layout(
        "a short manga line",
        [0, 0, 220, 100],
        orientation="horizontal",
        font_family=font.stem,
        preferred_size=30,
        line_spacing=1.1,
        character_spacing=0,
        alignment="center",
        custom_font_path=str(font),
    )
    assert fitted.placements
    assert not fitted.overflow
    impossible = engine.layout(
        "this translation cannot possibly fit in the tiny region",
        [0, 0, 8, 8],
        orientation="horizontal",
        font_family=font.stem,
        preferred_size=24,
        line_spacing=1.1,
        character_spacing=0,
        alignment="center",
        custom_font_path=str(font),
    )
    assert impossible.overflow
    assert "minimum_font_size_reached" in impossible.warnings


def test_vertical_layout_uses_columns_without_rotating_cjk_block() -> None:
    font = available_font()
    engine = MangaLayoutEngine(FontResolver([font.parent]), min_font_size=8)
    layout = engine.layout(
        "漫画翻译需要真正竖排",
        [0, 0, 100, 100],
        orientation="vertical",
        font_family=font.stem,
        preferred_size=20,
        line_spacing=1.1,
        character_spacing=1,
        alignment="center",
        custom_font_path=str(font),
    )
    assert layout.placements
    assert all(glyph.rotate == 0 for glyph in layout.placements)
    assert len({round(glyph.x) for glyph in layout.placements}) >= 2


def test_vertical_layout_wraps_overflow_into_right_to_left_columns() -> None:
    font = available_font()
    engine = MangaLayoutEngine(FontResolver([font.parent]), min_font_size=8)

    layout = engine.layout(
        "甲乙丙丁戊己",
        [0, 0, 60, 70],
        orientation="vertical",
        font_family=font.stem,
        preferred_size=20,
        line_spacing=1.1,
        character_spacing=0,
        alignment="center",
        custom_font_path=str(font),
    )

    assert layout.lines == ["甲乙丙", "丁戊己"]
    first_column_x = {glyph.x for glyph in layout.placements[:3]}
    second_column_x = {glyph.x for glyph in layout.placements[3:]}
    assert len(first_column_x) == len(second_column_x) == 1
    assert first_column_x.pop() > second_column_x.pop()
    assert not layout.overflow


def test_font_resolver_discovers_font_collections(tmp_path: Path) -> None:
    collection = tmp_path / "NotoSansCJK-Regular.TTC"
    collection.write_bytes(b"font collection placeholder")

    resolver = FontResolver([tmp_path])

    assert collection in resolver.paths


def test_font_resolver_discovers_font_added_after_startup(tmp_path: Path) -> None:
    source = available_font()
    resolver = FontResolver([tmp_path])
    added = tmp_path / f"RuntimeAddedFont{source.suffix}"
    shutil.copyfile(source, added)

    assert resolver.resolve("Runtime Added Font") == added.resolve()


def test_renderer_respects_zero_stroke_on_dark_background() -> None:
    background = Image.new("RGBA", (120, 80), "#15191d")
    region = SimpleNamespace(
        bbox=[10, 10, 100, 60],
        text_color="#111111",
        stroke_color="#ffffff",
        stroke_width=0.0,
        opacity=1.0,
    )

    style = PillowRenderer._resolve_render_style(background, region, 24)

    assert style["auto_contrast"] is False
    assert style["stroke_color"] == "#ffffff"
    assert style["stroke_width"] == 0


def test_renderer_keeps_explicit_high_contrast_style() -> None:
    background = Image.new("RGBA", (120, 80), "#f4f4f4")
    region = SimpleNamespace(
        bbox=[10, 10, 100, 60],
        text_color="#111111",
        stroke_color="#ff0000",
        stroke_width=2.0,
        opacity=1.0,
    )

    style = PillowRenderer._resolve_render_style(background, region, 24)

    assert style["auto_contrast"] is False
    assert style["stroke_color"] == "#ff0000"
    assert style["stroke_width"] == 2


def test_renderer_preserves_background_icc_profile(tmp_path: Path) -> None:
    background_path = tmp_path / "background.png"
    output_path = tmp_path / "rendered.png"
    layer_path = tmp_path / "text-layer.png"
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("RGB", (32, 24), "#426688").save(background_path, "PNG", icc_profile=profile)

    PillowRenderer().render(background_path, [], output_path, layer_path)

    with Image.open(output_path) as rendered:
        assert rendered.info["icc_profile"] == profile


def test_renderer_skips_hidden_regions(tmp_path: Path) -> None:
    background_path = tmp_path / "background.png"
    output_path = tmp_path / "rendered.png"
    layer_path = tmp_path / "text-layer.png"
    Image.new("RGB", (32, 24), "#426688").save(background_path, "PNG")
    hidden = SimpleNamespace(visible=False, translated_text="must not be rendered")

    result = PillowRenderer().render(background_path, [hidden], output_path, layer_path)

    assert result["layouts"] == {}
    with Image.open(output_path) as rendered:
        assert rendered.convert("RGB").getpixel((10, 10)) == (66, 102, 136)
    with Image.open(layer_path) as layer:
        assert layer.getbbox() is None
