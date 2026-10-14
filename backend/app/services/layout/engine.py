from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from math import cos, radians, sin
from pathlib import Path
from typing import Any

from PIL import ImageFont

VERTICAL_FORM_MAP = {
    "（": "︵", "）": "︶", "(": "︵", ")": "︶",
    "【": "︻", "】": "︼", "「": "﹁", "」": "﹂", "『": "﹃", "』": "﹄",
    ",": "︐", "，": "︐", "、": "︑", ".": "︒", "．": "︒", "。": "︒",
    ":": "︓", "：": "︓", ";": "︔", "；": "︔",
    "!": "︕", "！": "︕", "?": "︖", "？": "︖",
    "…": "︙", "—": "︱", "ー": "︱", "～": "︴", "〜": "︴",
}
VERTICAL_FORMS = str.maketrans(VERTICAL_FORM_MAP)


@dataclass(slots=True)
class GlyphPlacement:
    text: str
    x: float
    y: float
    font_path: str
    font_size: int
    rotate: float = 0.0


@dataclass(slots=True)
class LayoutResult:
    font_size: int
    placements: list[GlyphPlacement]
    width: float
    height: float
    overflow: bool = False
    warnings: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    # Horizontal placements already include alignment inside this coordinate
    # space. The renderer must center that space, not center the text again.
    coordinate_width: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FontResolver:
    def __init__(self, extra_paths: list[Path] | None = None, *, project_root: Path | None = None) -> None:
        roots = [
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path.home() / ".fonts",
            *(extra_paths or []),
        ]
        self.roots = list(dict.fromkeys(roots))
        self.project_root = project_root
        self.paths: list[Path] = []
        self._coverage: dict[Path, set[int] | None] = {}
        self.refresh()

    def refresh(self) -> None:
        supported_suffixes = {".ttf", ".otf", ".ttc", ".otc"}
        # Project font assets live in <project>/fonts, never in page/history
        # directories. Walking every manga PNG on a missing-font lookup can
        # take seconds on Windows-mounted volumes.
        project_fonts = list(self.project_root.glob("*/fonts")) if self.project_root else []
        self.paths = list(dict.fromkeys(
            path.resolve()
            for root in [*self.roots, *project_fonts]
            if root.exists()
            for path in root.rglob("*")
            if path.suffix.lower() in supported_suffixes and path.is_file()
        ))

    @staticmethod
    def _normalize_family(value: str) -> str:
        return "".join(character for character in value.lower() if character.isalnum())

    def resolve(
        self,
        family: str | None = None,
        custom_path: str | None = None,
        character: str | None = None,
        weight: int = 400,
    ) -> Path:
        return self.prepare(family, custom_path, weight)(character)

    def prepare(self, family: str | None = None, custom_path: str | None = None,
                weight: int = 400) -> Callable[[str | None], Path]:
        """Resolve candidate files once per layout, not once per glyph/size.

        The returned cache is local to this layout, so uploads/deletions and
        concurrent renders do not share a stale negative or custom-font cache.
        """
        if custom_path:
            candidate = Path(custom_path).expanduser()
            if candidate.exists():
                return lambda _character: candidate
        normalized = self._normalize_family(family or "")
        family_tokens = [normalized]
        if any(normalized.endswith(locale) for locale in ("sc", "tc", "jp", "kr", "hk")):
            family_tokens.append(normalized[:-2])
        preferred = [
            path for path in self.paths
            if normalized and any(token in self._normalize_family(path.stem) for token in family_tokens)
        ]
        if normalized and (not preferred or any(not path.exists() for path in preferred)):
            # Settings can add/remove fonts while the renderer is running.
            self.refresh()
            preferred = [
                path for path in self.paths
                if any(token in self._normalize_family(path.stem) for token in family_tokens)
            ]
        preferred.sort(key=lambda path: self._weight_rank(path, weight))
        cjk_tokens = ("notosanscjk", "notoserifcjk", "sourcehan", "wqy", "droidsansfallback", "unifont")
        fallbacks = [
            path for path in self.paths if any(token in self._normalize_family(path.stem) for token in cjk_tokens)
        ]
        candidates = list(dict.fromkeys([*preferred, *fallbacks, *self.paths]))
        if not candidates:
            raise FileNotFoundError(
                "No TrueType/OpenType fonts or font collections found; install Noto Sans CJK or set a custom font"
            )
        @lru_cache(maxsize=2048)
        def resolve_character(character: str | None) -> Path:
            if character:
                codepoint = ord(character)
                for path in candidates:
                    coverage = self._font_coverage(path)
                    if coverage is None or codepoint in coverage:
                        return path
            return candidates[0]
        return resolve_character

    @staticmethod
    def _weight_rank(path: Path, weight: int) -> int:
        bold = any(token in path.stem.lower() for token in ("bold", "black", "heavy", "semibold"))
        return 0 if bold == (weight >= 600) else 1

    def _font_coverage(self, path: Path) -> set[int] | None:
        if path in self._coverage:
            return self._coverage[path]
        try:
            from fontTools.ttLib import TTFont

            font = TTFont(path, lazy=True)
            coverage = {codepoint for table in font["cmap"].tables for codepoint in table.cmap}
            font.close()
            self._coverage[path] = coverage
        except Exception:
            self._coverage[path] = None
        return self._coverage[path]


class MangaLayoutEngine:
    def __init__(self, font_resolver: FontResolver | None = None, min_font_size: int = 10) -> None:
        self.fonts = font_resolver or FontResolver()
        self.min_font_size = min_font_size

    def layout(
        self,
        text: str,
        bbox: list[float],
        *,
        orientation: str,
        font_family: str,
        preferred_size: float,
        line_spacing: float,
        character_spacing: float,
        alignment: str,
        custom_font_path: str | None = None,
        font_weight: int = 400,
        polygon: list[list[float]] | None = None,
        auto_fit: bool = True,
        stroke_width: float = 0,
        rotation: float = 0,
    ) -> LayoutResult:
        if not text.strip() or len(bbox) != 4:
            return LayoutResult(self.min_font_size, [], 0, 0, overflow=not bool(text.strip()), warnings=["empty_text"])
        polygon_factor = self._polygon_factor(polygon, bbox)
        padding = 2 + max(0, stroke_width) if auto_fit else 0
        factor = polygon_factor if auto_fit else 1
        available_width = max(1.0, bbox[2] * factor - padding * 2)
        available_height = max(1.0, bbox[3] * factor - padding * 2)
        resolve_font = self.fonts.prepare(font_family, custom_font_path, font_weight)
        def candidate(size: int) -> LayoutResult:
            if orientation == "vertical":
                result = self._vertical(
                    text,
                    available_width,
                    available_height,
                    size,
                    font_family,
                    line_spacing,
                    character_spacing,
                    custom_font_path,
                    font_weight,
                    resolve_font,
                )
            else:
                result = self._horizontal(
                    text,
                    available_width,
                    available_height,
                    size,
                    font_family,
                    line_spacing,
                    character_spacing,
                    alignment,
                    custom_font_path,
                    font_weight,
                    resolve_font,
                )
            if auto_fit and (bbox[2] * factor <= padding * 2 or bbox[3] * factor <= padding * 2):
                result.overflow = True
            if auto_fit and abs(rotation) > .01 and not result.overflow:
                angle = radians(rotation)
                offset_x = (bbox[2] - (result.coordinate_width or result.width)) / 2
                offset_y = (bbox[3] - result.height) / 2
                for glyph in result.placements:
                    ink = self._font(Path(glyph.font_path), size).getbbox(glyph.text, anchor="lt")
                    gx, gy = glyph.x + offset_x, glyph.y + offset_y
                    gw, gh = (size,size) if glyph.rotate else (ink[2],ink[3])
                    for px,py in ((gx,gy),(gx+gw,gy),(gx+gw,gy+gh),(gx,gy+gh)):
                        dx,dy = px-bbox[2]/2,py-bbox[3]/2
                        rx = bbox[2]/2 + dx*cos(angle)-dy*sin(angle)
                        ry = bbox[3]/2 + dx*sin(angle)+dy*cos(angle)
                        if not (padding <= rx <= bbox[2]-padding and padding <= ry <= bbox[3]-padding):
                            result.overflow = True
                            break
                    if result.overflow:
                        break
            return result
        if not auto_fit:
            result = candidate(max(1, min(512, round(preferred_size))))
            if result.overflow:
                result.warnings.append("manual_font_size_overflow")
            return result
        maximum = max(1, min(512, int(available_height if orientation != "vertical" else min(available_width, available_height))))
        best = candidate(1)
        # Wrapping/height is monotonic for ordinary spacing. Negative spacing
        # can reverse glyph advances, so use exact descending search there.
        if character_spacing < 0:
            for size in range(maximum, 0, -1):
                result = candidate(size)
                if not result.overflow:
                    return result
        else:
            low, high = 1, maximum
            while low <= high:
                size = (low + high) // 2
                result = candidate(size)
                if result.overflow:
                    high = size - 1
                else:
                    best = result
                    low = size + 1
        if best.overflow:
            best.warnings.extend(["text_overflow", "minimum_font_size_reached"])
        return best

    def _horizontal(
        self,
        text: str,
        width: float,
        height: float,
        size: int,
        family: str,
        line_spacing: float,
        character_spacing: float,
        alignment: str,
        custom: str | None,
        weight: int,
        resolve_font: Callable[[str | None], Path] | None = None,
    ) -> LayoutResult:
        resolve_font = resolve_font or self.fonts.prepare(family, custom, weight)
        font_path = resolve_font(text[0])
        font = self._font(font_path, size)
        lines = self._wrap(text, font, width, character_spacing)
        line_height = size * line_spacing
        total_height = max(size, (len(lines) - 1) * line_height + size)
        placements: list[GlyphPlacement] = []
        max_width = 0.0
        for line_index, line in enumerate(lines):
            glyphs = []
            cursor = 0.0
            ink_left, ink_right = float("inf"), float("-inf")
            for character in line:
                char_path = resolve_font(character)
                char_font = self._font(char_path, size)
                ink = char_font.getbbox(character, anchor="lt")
                ink_left, ink_right = min(ink_left, cursor + ink[0]), max(ink_right, cursor + ink[2])
                total_height = max(total_height, line_index * line_height + ink[3])
                glyphs.append((character, cursor, char_path))
                cursor += self._measure(character, char_font, 0) + character_spacing
            line_width = ink_right - ink_left if glyphs else 0.0
            max_width = max(max_width, line_width)
            start_x = {"left": 0.0, "right": width - line_width}.get(alignment, (width - line_width) / 2)
            for character, cursor, char_path in glyphs:
                placements.append(GlyphPlacement(character, cursor + start_x - ink_left, line_index * line_height, str(char_path), size))
        overflow = max_width > width + 0.5 or total_height > height + 0.5
        return LayoutResult(size, placements, max_width, total_height, overflow=overflow, lines=lines,
                            coordinate_width=width)

    def _vertical(
        self,
        text: str,
        width: float,
        height: float,
        size: int,
        family: str,
        line_spacing: float,
        character_spacing: float,
        custom: str | None,
        weight: int,
        resolve_font: Callable[[str | None], Path] | None = None,
    ) -> LayoutResult:
        resolve_font = resolve_font or self.fonts.prepare(family, custom, weight)
        paragraphs = text.replace("\r", "").split("\n")
        # Negative character spacing may tighten a column, but it must never
        # reverse the top-to-bottom flow or create a zero-height cell.
        cell_height = max(1.0, size + character_spacing)
        capacity = max(1, int(max(1.0, height + character_spacing) // cell_height))
        columns: list[str] = []
        for paragraph in paragraphs:
            columns.extend(paragraph[index : index + capacity] for index in range(0, len(paragraph), capacity))
            if not paragraph:
                columns.append("")
        column_width = size * line_spacing
        total_width = max(size, (len(columns) - 1) * column_width + size)
        placements: list[GlyphPlacement] = []
        for column_index, column in enumerate(columns):
            x = total_width - size - column_index * column_width
            for row, original in enumerate(column):
                vertical_char = original.translate(VERTICAL_FORMS)
                char_path = resolve_font(vertical_char)
                rotate = 90.0 if self._rotate_in_vertical(original) else 0.0
                placements.append(GlyphPlacement(vertical_char, x, row * cell_height, str(char_path), size, rotate))
        rows = max((len(column) for column in columns), default=0)
        used_height = (rows - 1) * cell_height + size if rows else 0
        overflow = total_width > width + 0.5 or used_height > height + .5
        return LayoutResult(size, placements, total_width, used_height, overflow=overflow, lines=columns)

    @staticmethod
    def _rotate_in_vertical(character: str) -> bool:
        # Characters with Unicode vertical presentation forms must stay
        # upright. Rotating them produces a sideways question/exclamation
        # mark in exported vertical dialogue.
        if character in VERTICAL_FORM_MAP:
            return False
        category = unicodedata.category(character)
        return character.isascii() and (character.isalnum() or category.startswith("P"))

    @staticmethod
    def _polygon_factor(polygon: list[list[float]] | None, bbox: list[float]) -> float:
        """Approximate an inscribed text area from polygon coverage.

        An oval bubble occupies about 79% of its bbox, so the square root converts area
        coverage into a conservative linear dimension without rasterizing large pages.
        """
        if not polygon or len(polygon) < 3 or bbox[2] <= 0 or bbox[3] <= 0:
            return 1.0
        area = (
            abs(
                sum(
                    polygon[index][0] * polygon[(index + 1) % len(polygon)][1]
                    - polygon[(index + 1) % len(polygon)][0] * polygon[index][1]
                    for index in range(len(polygon))
                )
            )
            / 2
        )
        coverage = max(0.2, min(1.0, area / (bbox[2] * bbox[3])))
        return coverage**0.5

    @staticmethod
    @lru_cache(maxsize=512)
    def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(path), size=size)

    @staticmethod
    def _measure(text: str, font: ImageFont.FreeTypeFont, spacing: float) -> float:
        if not text:
            return 0.0
        return sum(float(font.getlength(character)) for character in text) + max(0, len(text) - 1) * spacing

    def _wrap(self, text: str, font: ImageFont.FreeTypeFont, width: float, spacing: float) -> list[str]:
        output: list[str] = []
        advances: dict[str, float] = {}
        for paragraph in text.replace("\r", "").split("\n"):
            if not paragraph:
                output.append("")
                continue
            current = ""
            glyph_width = 0.0
            for character in paragraph:
                if character not in advances:
                    advances[character] = float(font.getlength(character))
                advance = advances[character]
                candidate_glyph_width = glyph_width + advance
                # Same sum of individual glyph advances and spacing as
                # _measure(), without remeasuring every growing line prefix.
                if current and candidate_glyph_width + len(current) * spacing > width:
                    output.append(current)
                    current = character
                    glyph_width = advance
                else:
                    current += character
                    glyph_width = candidate_glyph_width
            if current:
                output.append(current)
        return output or [""]
