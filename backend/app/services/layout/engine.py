from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass, field
from functools import lru_cache
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FontResolver:
    def __init__(self, extra_paths: list[Path] | None = None) -> None:
        roots = [
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path.home() / ".fonts",
            *(extra_paths or []),
        ]
        self.roots = list(dict.fromkeys(roots))
        self.paths: list[Path] = []
        self._coverage: dict[Path, set[int] | None] = {}
        self.refresh()

    def refresh(self) -> None:
        supported_suffixes = {".ttf", ".otf", ".ttc", ".otc"}
        self.paths = list(dict.fromkeys(
            path.resolve()
            for root in self.roots
            if root.exists()
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in supported_suffixes
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
        if custom_path:
            candidate = Path(custom_path).expanduser()
            if candidate.exists():
                return candidate
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
        candidates = preferred + fallbacks + self.paths
        if character:
            codepoint = ord(character)
            for path in candidates:
                coverage = self._font_coverage(path)
                if coverage is None or codepoint in coverage:
                    return path
        if candidates:
            return candidates[0]
        raise FileNotFoundError(
            "No TrueType/OpenType fonts or font collections found; install Noto Sans CJK or set a custom font"
        )

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
    ) -> LayoutResult:
        if not text.strip() or len(bbox) != 4:
            return LayoutResult(self.min_font_size, [], 0, 0, overflow=not bool(text.strip()), warnings=["empty_text"])
        polygon_factor = self._polygon_factor(polygon, bbox)
        available_width = max(1.0, bbox[2] * 0.9 * polygon_factor)
        available_height = max(1.0, bbox[3] * 0.9 * polygon_factor)
        maximum = max(self.min_font_size, round(preferred_size))
        last: LayoutResult | None = None
        for size in range(maximum, self.min_font_size - 1, -1):
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
                )
            last = result
            if not result.overflow:
                return result
        assert last is not None
        last.warnings.extend(["text_overflow", "minimum_font_size_reached"])
        last.overflow = True
        return last

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
    ) -> LayoutResult:
        font_path = self.fonts.resolve(family, custom, text[0], weight)
        font = self._font(font_path, size)
        lines = self._wrap(text, font, width, character_spacing)
        line_height = size * line_spacing
        total_height = max(size, len(lines) * line_height)
        placements: list[GlyphPlacement] = []
        max_width = 0.0
        for line_index, line in enumerate(lines):
            line_width = self._measure(line, font, character_spacing)
            max_width = max(max_width, line_width)
            start_x = {"left": 0.0, "right": width - line_width}.get(alignment, (width - line_width) / 2)
            cursor = max(0.0, start_x)
            for character in line:
                char_path = self.fonts.resolve(family, custom, character, weight)
                char_font = self._font(char_path, size)
                placements.append(GlyphPlacement(character, cursor, line_index * line_height, str(char_path), size))
                cursor += self._measure(character, char_font, 0) + character_spacing
        overflow = max_width > width + 0.5 or total_height > height + 0.5
        return LayoutResult(size, placements, max_width, total_height, overflow=overflow, lines=lines)

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
    ) -> LayoutResult:
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
        total_width = max(size, len(columns) * column_width)
        placements: list[GlyphPlacement] = []
        for column_index, column in enumerate(columns):
            x = total_width - (column_index + 1) * column_width + max(0, (column_width - size) / 2)
            for row, original in enumerate(column):
                vertical_char = original.translate(VERTICAL_FORMS)
                char_path = self.fonts.resolve(family, custom, vertical_char, weight)
                rotate = 90.0 if self._rotate_in_vertical(original) else 0.0
                placements.append(GlyphPlacement(vertical_char, x, row * cell_height, str(char_path), size, rotate))
        used_height = min(capacity, max((len(column) for column in columns), default=0)) * cell_height
        overflow = total_width > width + 0.5 or any(len(column) > capacity for column in columns)
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
        for paragraph in text.replace("\r", "").split("\n"):
            if not paragraph:
                output.append("")
                continue
            current = ""
            for character in paragraph:
                candidate = current + character
                if current and self._measure(candidate, font, spacing) > width:
                    output.append(current)
                    current = character
                else:
                    current = candidate
            if current:
                output.append(current)
        return output or [""]
