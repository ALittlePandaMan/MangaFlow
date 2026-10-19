"""Small, disposable UI previews; full-resolution editing artifacts stay intact."""
from __future__ import annotations

import hashlib
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4

from app.core.config import get_settings
from app.models import ImagePage
from app.storage import get_storage
from PIL import Image, ImageOps

PreviewKind = Literal["cover", "page", "page-2x"]
PREVIEW_VERSION = 2
PREVIEW_SIZES = {"cover": (768, 1536), "page": (200, 400), "page-2x": (400, 800)}
# Bounded locks coalesce concurrent requests without retaining every page ID.
_LOCKS = tuple(Lock() for _ in range(16))


def preview_source(page: ImagePage, kind: PreviewKind, *, metadata: dict | None = None) -> tuple[str, str]:
    rendered = kind != "cover" and bool(page.rendered_path)
    path = page.rendered_path if rendered else page.original_path
    metadata = (page.metadata_json or {}) if metadata is None else metadata
    versions = metadata.get("artifact_versions") or {}
    version = (
        versions.get("rendered") or int(page.updated_at.timestamp() * 1000)
        if rendered else int(page.created_at.timestamp() * 1000)
    )
    # History restoration can change the source path without changing its time.
    token = hashlib.sha256(f"{PREVIEW_VERSION}:{path}:{version}".encode()).hexdigest()[:20]
    return path, token


def preview_url(page: ImagePage, kind: PreviewKind, *, metadata: dict | None = None) -> str:
    _, token = preview_source(page, kind, metadata=metadata)
    return f"{get_settings().api_prefix}/images/{page.id}/preview/{kind}?v={token}"


def image_preview(page: ImagePage, kind: PreviewKind) -> Path:
    storage = get_storage()
    relative, token = preview_source(page, kind)
    source = storage.absolute(relative)
    stat = source.stat()
    signature = hashlib.sha256(f"{token}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()[:24]
    directory = storage.project_dir(page.project_id) / "pages" / page.id / "previews"
    target = directory / f"{kind}-{signature}.webp"
    if target.is_file():
        return target
    with _LOCKS[int(signature[:2], 16) % len(_LOCKS)]:
        if target.is_file():
            return target
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f".{uuid4().hex}.tmp"
        try:
            with Image.open(source) as original:
                # JPEG decoders can downsample before expanding the full image.
                original.draft("RGB", PREVIEW_SIZES[kind])
                preview = ImageOps.exif_transpose(original)
                if kind == "cover" and preview.height > preview.width / 1.5:
                    # Cards already use a centered, wide object-cover crop.
                    # Drop the hidden top/bottom while keeping extra vertical
                    # margin for the varying card widths on phones/desktops.
                    height = round(preview.width / 1.5)
                    top = (preview.height - height) // 2
                    preview = preview.crop((0, top, preview.width, top + height))
                preview.thumbnail(PREVIEW_SIZES[kind], Image.Resampling.LANCZOS)
                alpha = "A" in preview.getbands() or "transparency" in preview.info
                preview = preview.convert("RGBA" if alpha else "RGB")
                profile = original.info.get("icc_profile") if original.mode in {"RGB", "RGBA"} else None
                preview.save(temporary, "WEBP", quality=82, method=4, **({"icc_profile": profile} if profile else {}))
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target
