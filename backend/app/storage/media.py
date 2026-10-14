"""Serve editing assets, never the database, configuration or private metadata."""
from pathlib import Path, PurePosixPath

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
FONT_SUFFIXES = {".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2"}
PAGE_ASSETS = {"original", "masks", "bubbles", "clean", "rendered", "layers", "versions", "previews"}


def public_media_path(path: str) -> bool:
    if "\\" in path or any(part.startswith(".") for part in path.split("/")):
        return False
    parts = PurePosixPath(path).parts
    suffix = PurePosixPath(path).suffix.lower()
    if len(parts) == 2 and parts[0] == "fonts":
        return suffix in FONT_SUFFIXES
    if len(parts) < 4 or parts[0] != "projects":
        return False
    if len(parts) == 4 and parts[2] == "fonts":
        return suffix in FONT_SUFFIXES
    if len(parts) == 5 and parts[2:4] == ("history", "assets"):
        return suffix in IMAGE_SUFFIXES | FONT_SUFFIXES
    return len(parts) == 6 and parts[2] == "pages" and parts[4] in PAGE_ASSETS and suffix in IMAGE_SUFFIXES


class VersionedMediaFiles(StaticFiles):
    def lookup_path(self, path: str):
        full_path, stat = super().lookup_path(path)
        if not stat:
            return full_path, stat
        try:
            resolved = Path(full_path).resolve().relative_to(Path(self.directory).resolve()).as_posix()
        except ValueError:
            return "", None
        # Also validate symlink targets inside the data root, not just escapes.
        return (full_path, stat) if public_media_path(resolved) else ("", None)

    async def get_response(self, path: str, scope: Scope) -> Response:
        if not public_media_path(path):
            raise HTTPException(404)
        response = await super().get_response(path, scope)
        if response.status_code in {200, 206, 304} and b"v=" in scope.get("query_string", b""):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
