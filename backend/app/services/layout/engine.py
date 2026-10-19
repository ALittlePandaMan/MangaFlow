from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from functools import cache, lru_cache
from math import ceil, cos, radians, sin
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
    # Automatic layout positions the actual ink rectangle, not the font's em
    # square. Bounds are measured at an alphabetic baseline (Pillow 'ls').
    ink_box: tuple[float, float, float, float] | None = None


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
        # The editor's visible target frame is the sizing contract. Polygon
        # area is not a linear text inset, and must not silently shrink it.
        padding = 1 + ceil(max(0, stroke_width)) if auto_fit else 0
        available_width = max(1.0, bbox[2] - padding * 2)
        available_height = max(1.0, bbox[3] - padding * 2)
        resolve_font = self.fonts.prepare(font_family, custom_font_path, font_weight)
        def candidate(size: int) -> LayoutResult:
            result = self._fit_ink(text, available_width, available_height, size,
                                   orientation, line_spacing, character_spacing, alignment, resolve_font)
            if auto_fit and (bbox[2] <= padding * 2 or bbox[3] <= padding * 2):
                result.overflow = True
            if auto_fit and abs(rotation) > .01 and not result.overflow:
                angle = radians(rotation)
                offset_x = (bbox[2] - (result.coordinate_width or result.width)) / 2
                offset_y = (bbox[3] - result.height) / 2
                for glyph in result.placements:
                    gx, gy = glyph.x + offset_x, glyph.y + offset_y
                    ink = glyph.ink_box
                    gw, gh = ink[2] - ink[0], ink[3] - ink[1]
                    if glyph.rotate:
                        gw, gh = gh, gw
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
        # Lowercase, punctuation and narrow glyphs can have ink smaller than
        # one em. A cap based on frame height would leave that space unused.
        maximum = 512
        best = candidate(1)
        # Wrapping/height is monotonic for ordinary spacing. Negative spacing
        # can reverse glyph advances, so use exact descending search there.
        if character_spacing < 0 or abs(rotation) > .01:
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

    def _fit_ink(self, text, width, height, size, orientation, line_spacing,
                 character_spacing, alignment, resolve_font) -> LayoutResult:
        """Wrap and fit real glyph bounds; keep user-entered paragraph breaks.

        Metrics are cached per candidate so repeated dialogue costs one font
        lookup/measurement per distinct glyph, not per growing line prefix.
        """
        vertical = orientation == "vertical"
        step = max(1., size + character_spacing)
        pitch = size * line_spacing

        @cache
        def metrics(original):
            character = original.translate(VERTICAL_FORMS) if vertical else original
            path = resolve_font(character)
            font = self._font(path, size)
            ink = self._ink_bounds(path, size, character)
            rotate = 90. if vertical and self._rotate_in_vertical(original) else 0.
            w, h = ink[2] - ink[0], ink[3] - ink[1]
            if rotate:
                w, h = h, w
            return character, path, ink, rotate, w, h, float(font.getlength(character))

        lines = []
        for paragraph in text.replace("\r", "").split("\n"):
            current, cursor, left, right = "", 0., float("inf"), float("-inf")
            for character in paragraph:
                _, _, ink, _, _, h, advance = metrics(character)
                new_left, new_right = min(left, cursor + ink[0]), max(right, cursor + ink[2])
                extent = len(current) * step + h if vertical else new_right - new_left
                if current and extent > (height if vertical else width):
                    lines.append(current)
                    current, cursor = "", 0.
                    new_left, new_right = ink[0], ink[2]
                current += character
                cursor += advance + character_spacing
                left, right = new_left, new_right
            lines.append(current)

        placements = []
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        for index, line in enumerate(lines):
            line_glyphs = []
            cursor = 0.
            line_left, line_right = float("inf"), float("-inf")
            for row, original in enumerate(line):
                character, path, ink, rotate, w, h, advance = metrics(original)
                x = -index * pitch + (size-w)/2 if vertical else cursor + ink[0]
                y = row * step if vertical else index * pitch + ink[1]
                glyph = GlyphPlacement(character, x, y, str(path), size, rotate, ink)
                line_glyphs.append((glyph, w, h))
                line_left, line_right = min(line_left, x), max(line_right, x+w)
                cursor += advance + character_spacing
            if not line_glyphs:
                # Preserve blank paragraph spacing, even at the ends.
                if vertical:
                    min_x, max_x = min(min_x, -index*pitch), max(max_x, -index*pitch+size)
                else:
                    min_y, max_y = min(min_y, index*pitch-size), max(max_y, index*pitch)
                continue
            shift = 0. if vertical else {"left": 0., "right": width-(line_right-line_left)}.get(
                alignment, (width-(line_right-line_left))/2) - line_left
            for glyph, w, h in line_glyphs:
                glyph.x += shift
                min_x, max_x = min(min_x, glyph.x), max(max_x, glyph.x+w)
                min_y, max_y = min(min_y, glyph.y), max(max_y, glyph.y+h)
                placements.append(glyph)
        if not placements:
            return LayoutResult(size, [], 0, 0, lines=lines)
        used_width, used_height = max_x-min_x, max_y-min_y
        for glyph in placements:
            glyph.x -= min_x if vertical else 0
            glyph.y -= min_y
        return LayoutResult(size, placements, used_width, used_height,
                            overflow=used_width > width or used_height > height, lines=lines,
                            coordinate_width=None if vertical else width)

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
    @lru_cache(maxsize=8192)
    def _ink_bounds(path: Path, size: int, character: str) -> tuple[int, int, int, int]:
        # getbbox includes font side bearings/advance space for some fonts.
        # Raster ink bounds match Canvas actualBoundingBox* and visual centering.
        font = MangaLayoutEngine._font(path, size)
        mask, offset = font.getmask2(character, anchor="ls")
        bounds = mask.getbbox()
        if bounds is None:
            return (0, 0, 0, 0)
        return (bounds[0]+offset[0], bounds[1]+offset[1], bounds[2]+offset[0], bounds[3]+offset[1])

    @staticmethod
    @lru_cache(maxsize=512)
    def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(path), size=size)
