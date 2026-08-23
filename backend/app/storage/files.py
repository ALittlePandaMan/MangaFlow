from __future__ import annotations

import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

from PIL import Image

from app.core.config import get_settings

SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
SUPPORTED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


class StorageError(ValueError):
    pass


class StorageManager:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def project_dir(self, project_id: str) -> Path:
        return self._inside(self.root / "projects" / project_id)

    def page_dir(self, project_id: str, page_id: str) -> Path:
        path = self._inside(self.project_dir(project_id) / "pages" / page_id)
        for child in ("original", "masks", "clean", "rendered", "layers", "versions"):
            (path / child).mkdir(parents=True, exist_ok=True)
        return path

    def save_upload(self, project_id: str, page_id: str, filename: str, stream: BinaryIO) -> tuple[str, int, int]:
        safe_name = SAFE_FILENAME.sub("_", Path(filename).name).strip("._") or "page.png"
        extension = Path(safe_name).suffix.lower()
        if extension not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise StorageError("Only JPG, JPEG, PNG and WebP images are supported")
        destination = self.page_dir(project_id, page_id) / "original" / safe_name
        with destination.open("wb") as output:
            shutil.copyfileobj(stream, output)
        try:
            with Image.open(destination) as image:
                image.verify()
            with Image.open(destination) as image:
                width, height = image.size
                if image.format not in SUPPORTED_IMAGE_FORMATS:
                    raise StorageError("Unsupported or invalid image")
        except Exception as exc:
            destination.unlink(missing_ok=True)
            if isinstance(exc, StorageError):
                raise
            raise StorageError("The uploaded file is not a valid image") from exc
        return self.relative(destination), width, height

    def save_project_cover(self, project_id: str, filename: str, stream: BinaryIO) -> str:
        safe_name = SAFE_FILENAME.sub("_", Path(filename).name).strip("._") or "cover.png"
        extension = Path(safe_name).suffix.lower()
        if extension not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise StorageError("Only JPG, JPEG, PNG and WebP images are supported")
        cover_dir = self.project_dir(project_id) / "cover"
        cover_dir.mkdir(parents=True, exist_ok=True)
        temporary = cover_dir / f".upload{extension}"
        with temporary.open("wb") as output:
            shutil.copyfileobj(stream, output)
        try:
            with Image.open(temporary) as image:
                image.verify()
            with Image.open(temporary) as image:
                if image.format not in SUPPORTED_IMAGE_FORMATS:
                    raise StorageError("Unsupported or invalid image")
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            if isinstance(exc, StorageError):
                raise
            raise StorageError("The uploaded file is not a valid image") from exc
        destination = cover_dir / f"cover{extension}"
        for existing in cover_dir.glob("cover.*"):
            existing.unlink(missing_ok=True)
        temporary.replace(destination)
        return self.relative(destination)

    def absolute(self, relative_path: str) -> Path:
        return self._inside(self.root / relative_path)

    def relative(self, absolute_path: Path) -> str:
        return absolute_path.resolve().relative_to(self.root).as_posix()

    def media_url(self, relative_path: str | None) -> str | None:
        return f"/media/{relative_path}" if relative_path else None

    def remove_project(self, project_id: str) -> None:
        directory = self.project_dir(project_id)
        if directory.exists():
            shutil.rmtree(directory)

    def _inside(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise StorageError("Path escapes the configured data directory")
        return resolved


@lru_cache
def get_storage() -> StorageManager:
    return StorageManager(get_settings().data_dir)
