from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"


def read_app_version() -> str:
    """Use source metadata in checkouts and distribution metadata in wheels."""
    try:
        source_version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        source_version = ""
    if source_version:
        return source_version
    try:
        return version("mangaflow")
    except PackageNotFoundError:
        return "0.0.0+unknown"


APP_VERSION = read_app_version()
