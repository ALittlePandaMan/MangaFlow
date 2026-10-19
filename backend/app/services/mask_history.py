"""Sparse mask checkpoints for immediate client-side undo and redo."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from uuid import uuid4

from app.models import EditorHistoryEntry, TextRegion
from app.services.editor_history import project_history_state
from app.storage import get_storage
from PIL import Image, ImageChops
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

_datetime_json = TypeAdapter(datetime)


def _mask(snapshot: dict, region_id: str, size: tuple[int, int]):
    item = next(item for item in snapshot["regions"] if item["id"] == region_id)
    path = item.get("pixel_mask_path")
    if not path:
        return Image.new("L", size, 0)
    with Image.open(get_storage().absolute(path)) as image:
        if image.size != size:
            raise ValueError("Mask history dimensions changed")
        return image.convert("L")


def checkpoint(entry: EditorHistoryEntry, region: TextRegion, bounds=None, before=None, after=None):
    storage = get_storage()
    size = (region.page.width, region.page.height)
    if before is None:
        before = _mask(entry.before, region.id, size)
    if after is None:
        after = _mask(entry.after, region.id, size)
    if bounds is None:
        bounds = ImageChops.difference(before, after).getbbox() or (0, 0, 1, 1)
    left, top, right, bottom = bounds
    images = {
        name: image if image.size == (right - left, bottom - top) else image.crop(bounds)
        for name, image in [("before", before), ("after", after)]
    }
    digest = hashlib.sha256(
        f"{right - left}x{bottom - top}:".encode() + images["before"].tobytes() + images["after"].tobytes()
    ).hexdigest()[:24]
    assets = storage.project_dir(entry.project_id) / "history" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    paths = {}
    # Prepared edits create their sparse files before a DB journal ID exists.
    # The final journal stores these exact immutable paths after publication.
    identity = entry.id if entry.id is not None else uuid4().hex
    for name, image in images.items():
        path = assets / f"mask-step-{identity}-{digest}-{name}.png"
        if not path.is_file():
            temporary = path.with_name(f".{uuid4().hex}.tmp")
            try:
                image.save(temporary, "PNG", compress_level=1)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        paths[name] = storage.relative(path)
    return {"region_id": region.id, "x": left, "y": top, "width": right - left, "height": bottom - top, **paths}


def mask_history_window(db: Session, region: TextRegion):
    state = project_history_state(db, region.page.project_id)
    # Match RegionRead's Pydantic JSON encoding (UTC is Z, not +00:00).
    # The client compares this optimistic-version token as an exact string.
    result = {"region_updated_at": _datetime_json.dump_python(region.updated_at, mode="json"),
              "history": state, "undo": [], "redo": []}
    if state["pending"]:
        return result
    # Never deserialize all page snapshots just to warm local undo. New mask
    # edits have small JSON descriptors; legacy edits are lazily cached below.
    fields = (
        EditorHistoryEntry.id,
        EditorHistoryEntry.image_id,
        EditorHistoryEntry.scope,
        EditorHistoryEntry.task_id,
        EditorHistoryEntry.region_ids,
        EditorHistoryEntry.action,
        EditorHistoryEntry.after["_mask_delta"].label("delta"),
    )
    legacy = 0
    for name, applied, order in [
        ("undo", True, EditorHistoryEntry.id.desc()),
        ("redo", False, EditorHistoryEntry.id.asc()),
    ]:
        entries = db.execute(
            select(*fields)
            .where(
                EditorHistoryEntry.project_id == region.page.project_id,
                EditorHistoryEntry.applied.is_(applied),
                EditorHistoryEntry.pending.is_(False),
            )
            .order_by(order)
            .limit(100)
        ).all()
        budget = 64_000_000
        for item in entries:
            if (
                item.scope != "page"
                or item.image_id != region.image_id
                or item.task_id
                or item.region_ids != [region.id]
                or item.action not in {"编辑蒙版", "调整蒙版"}
            ):
                break
            delta = item.delta
            if delta and not all(get_storage().absolute(delta[key]).is_file() for key in ("before", "after")):
                delta = None
            if not delta:
                if legacy >= 8:
                    break
                legacy += 1
                try:
                    delta = checkpoint(db.get(EditorHistoryEntry, item.id), region)
                except (OSError, ValueError, StopIteration):
                    break
            if delta["region_id"] != region.id:
                break
            amount = delta["width"] * delta["height"] * 2
            if amount > budget:
                break
            budget -= amount
            result[name].append(
                {key: delta[key] for key in ("x", "y", "width", "height")}
                | {
                    "id": item.id,
                    "before_url": get_storage().media_url(delta["before"]),
                    "after_url": get_storage().media_url(delta["after"]),
                }
            )
    return result
