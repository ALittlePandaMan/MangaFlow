from __future__ import annotations

from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"


def read_app_version() -> str:
    """Return the repository version, with a safe fallback for partial installs."""
    try:
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0+unknown"
    return version or "0.0.0+unknown"


APP_VERSION = read_app_version()
