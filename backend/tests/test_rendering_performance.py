from pathlib import Path
from types import SimpleNamespace

import pytest
from app.services.layout import FontResolver, MangaLayoutEngine


def test_missing_family_rescans_at_most_once_per_layout_and_glyphs_share_resolution(tmp_path, monkeypatch):
    resolver = FontResolver([tmp_path])
    engine = MangaLayoutEngine(resolver)
    scans = []
    original = resolver.refresh

    def refresh():
        scans.append(1)
        original()

    monkeypatch.setattr(resolver, "refresh", refresh)
    engine.layout(
        "ABABA " * 20,
        [0, 0, 800, 800],
        orientation="vertical",
        font_family="MissingFamily",
        preferred_size=30,
        line_spacing=1.1,
        character_spacing=0,
        alignment="center",
    )
    assert len(scans) <= 1


def test_active_ink_layout_measures_each_unique_glyph_once_without_changing_line_breaks(monkeypatch):
    calls = []
    font = SimpleNamespace(getlength=lambda character: calls.append(character) or 10.0)
    engine = MangaLayoutEngine.__new__(MangaLayoutEngine)
    monkeypatch.setattr(engine, "_font", lambda path, size: font)
    monkeypatch.setattr(engine, "_ink_bounds", lambda path, size, character: (0, 0, 10, 10))
    text = "ABCD" * 100
    result = engine._fit_ink(text, 50, 2000, 10, "horizontal", 1.1, 1, "center", lambda _: Path("fake.ttf"))
    assert "".join(result.lines) == text
    assert result.lines == ["ABCD"] * 100
    assert not result.overflow
    assert len(calls) <= 4


@pytest.mark.parametrize("spacing", [-2, 0, 0.5, 3])
def test_active_ink_layout_preserves_variable_width_unicode_and_paragraph_breaks(spacing, monkeypatch):
    def advance(character):
        return 5.5 if character.isascii() else 11.0
    font = SimpleNamespace(getlength=advance)
    engine = MangaLayoutEngine.__new__(MangaLayoutEngine)
    monkeypatch.setattr(engine, "_font", lambda path, size: font)
    monkeypatch.setattr(engine, "_ink_bounds", lambda path, size, character: (0, 0, advance(character), 10))
    text = "漫画ABC翻译!?\n\n" + "  漫画 ABC!? " * 30

    for width in [1, 5.5, 17, 44, 180]:
        result = engine._fit_ink(text, width, 10000, 10, "horizontal", 1.1, spacing, "center", lambda _: Path("fake.ttf"))
        # Word/kinsoku wrapping intentionally supersedes the old character-only
        # breaks. Soft-wrap spaces can disappear; text and hard blank lines cannot.
        assert "".join(result.lines).replace(" ", "") == text.replace("\n", "").replace(" ", "")
        assert "" in result.lines


def test_project_font_catalogue_never_walks_page_assets_and_finds_later_uploads(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    fonts = projects / "project" / "fonts"
    fonts.mkdir(parents=True)
    pages = projects / "project" / "pages"
    pages.mkdir()
    (fonts / "MangaFont.ttf").write_bytes(b"font-placeholder")
    (pages / "NotAProjectFont.ttf").write_bytes(b"font-placeholder")
    visited = []
    original = Path.rglob

    def scan(path, pattern):
        visited.append(path)
        return original(path, pattern)

    monkeypatch.setattr(Path, "rglob", scan)
    resolver = FontResolver(project_root=projects)
    assert fonts / "MangaFont.ttf" in resolver.paths
    assert pages / "NotAProjectFont.ttf" not in resolver.paths
    assert projects not in visited and pages not in visited
    (fonts / "AddedFont.ttf").write_bytes(b"font-placeholder")
    assert resolver.resolve("Added Font") == (fonts / "AddedFont.ttf").resolve()


def test_prepared_lookup_preserves_weight_fallback_and_custom_font_priority(tmp_path, monkeypatch):
    regular = tmp_path / "Manga-Regular.ttf"
    bold = tmp_path / "Manga-Bold.ttf"
    fallback = tmp_path / "NotoSansCJK-Regular.ttf"
    for path in (regular, bold, fallback):
        path.write_bytes(b"font-placeholder")
    resolver = FontResolver([tmp_path])
    resolver.paths = [regular, bold, fallback]
    coverage_calls = []

    def coverage(path):
        coverage_calls.append(path)
        return {ord("漫")} if path == fallback else {ord("A")}

    monkeypatch.setattr(resolver, "_font_coverage", coverage)
    choose = resolver.prepare("Manga", weight=700)
    assert choose("A") == bold
    assert choose("漫") == fallback
    count = len(coverage_calls)
    for _ in range(100):
        assert choose("漫") == fallback
    assert len(coverage_calls) == count
    assert resolver.prepare("Manga", str(regular), 700)("漫") == regular
