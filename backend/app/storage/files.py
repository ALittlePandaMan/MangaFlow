from __future__ import annotations

import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from PIL import Image, ImageOps

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
        for child in ("original", "masks", "bubbles", "clean", "rendered", "layers", "versions"):
            (path / child).mkdir(parents=True, exist_ok=True)
        return path

    def asset_page_dir(self, project_id: str, page_id: str) -> Path:
        """Published asset boundary; staged workers override only output paths."""
        return self.page_dir(project_id, page_id)

    def save_upload(self, project_id: str, page_id: str, filename: str, stream: BinaryIO, *,
                    apply_exif_orientation: bool = True) -> tuple[str, int, int]:
        original_name = Path(filename.replace("\\", "/")).name
        extension = Path(original_name).suffix.lower()
        safe_name = (SAFE_FILENAME.sub("_", Path(original_name).stem).strip("._") or "page") + extension
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
            destination, width, height = self._normalize_orientation(destination, apply_exif_orientation)
        except Exception as exc:
            destination.unlink(missing_ok=True)
            if isinstance(exc, StorageError):
                raise
            raise StorageError("The uploaded file is not a valid image") from exc
        return self.relative(destination), width, height

    @staticmethod
    def _normalize_orientation(destination: Path, apply_orientation: bool) -> tuple[Path, int, int]:
        """Publish canonical pixels once, without a lossy JPEG recompression.

        Ordinary uploads adopt their display orientation. Portable projects
        already have pixel-coordinate geometry/masks: retain those pixels and
        remove only the legacy display hint instead of rotating their frames.
        Images without a transforming EXIF tag remain byte-for-byte unchanged.
        """
        temporary = destination.with_name(f".{uuid4().hex}.png")
        try:
            with Image.open(destination) as source:
                if source.getexif().get(274) not in range(2, 9):
                    return destination, *source.size
                image = ImageOps.exif_transpose(source) if apply_orientation else source.copy()
                try:
                    exif = image.getexif()
                    exif.pop(274, None)
                    metadata = {key: source.info[key] for key in ("dpi", "icc_profile") if source.info.get(key) is not None}
                    if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"}:
                        converted = image.convert("RGB")
                        image.close()
                        image = converted
                        # A CMYK profile is not a valid profile for RGB pixels.
                        metadata.pop("icc_profile", None)
                    width, height = image.size
                    if exif:
                        metadata["exif"] = exif.tobytes()
                    image.save(temporary, "PNG", compress_level=3, **metadata)
                finally:
                    image.close()
            target = destination.with_suffix(".png")
            temporary.replace(target)
            if destination != target:
                destination.unlink()
            return target, width, height
        finally:
            temporary.unlink(missing_ok=True)

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
