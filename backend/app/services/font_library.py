"""Read font identities and invalidate long-lived renderer indexes."""
from pathlib import Path
from threading import Lock

_lock = Lock()
_revision = 0


def font_family(path: Path, *, fallback: str | None = None) -> str:
    from fontTools.ttLib import TTFont

    with TTFont(path, lazy=True) as font:
        for name_id in (16, 1):
            for record in font["name"].names:
                if record.nameID != name_id:
                    continue
                try:
                    family = record.toUnicode().strip()
                except Exception:
                    continue
                if family:
                    return family
    return fallback or path.stem


def font_library_revision() -> int:
    return _revision


def notify_fonts_changed() -> None:
    global _revision
    with _lock:
        _revision += 1
