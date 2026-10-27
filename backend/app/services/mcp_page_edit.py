"""Narrow, atomic text-region corrections using the editor's publication path."""
from app.api.helpers import require_page
from app.core.database import SessionLocal
from app.models import TextRegion
from app.schemas.domain import ProcessRequest, RegionCreate, RegionUpdate
from app.services.mcp_context import check_page_version, expected_versions
from app.services.workflow_state import current_page_version
from app.utils.geometry import validate_polygon
from fastapi import HTTPException
from sqlalchemy import select


def resolve_regions(db, image_id, references):
    rows = list(db.scalars(select(TextRegion).where(TextRegion.image_id == image_id)))
    lookup = {key: row for row in rows for key in (row.id, row.region_key)}
    result = []
    for ref in references:
        if ref not in lookup:
            raise HTTPException(422, f"Region {ref} does not belong to this page/version")
        result.append(lookup[ref])
    if len({row.id for row in result}) != len(result):
        raise HTTPException(422, "Duplicate region references")
    return result


def validate_patch(value, page, *, create=False):
    from app.services.mcp_catalog import CREATE_FIELDS, EDIT_FIELDS
    if not value or set(value) - (CREATE_FIELDS if create else EDIT_FIELDS).keys():
        raise HTTPException(422, "Supply nonempty supported text/geometry/style fields")
    if create and not (value.get("bbox") or value.get("polygon")):
        raise HTTPException(422, "New text regions need source bbox or polygon")
    import math
    for field in ("bbox", "translated_bbox"):
        if field in value:
            box = value[field]
            if (not isinstance(box, list) or len(box) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box)
                    or min(box[:2]) < 0 or min(box[2:]) <= 0 or box[0]+box[2] > page.width or box[1]+box[3] > page.height):
                raise HTTPException(422, f"{field} must be within original image bounds")
    for field in ("polygon", "translated_polygon"):
        if field in value:
            validate_polygon(value[field])
            if any(x < 0 or y < 0 or x > page.width or y > page.height for x, y in value[field]):
                raise HTTPException(422, f"{field} must be within original image bounds")


def edit(args):
    from app.api.regions import _apply_region_patch, _invalidate_rendered_artifacts, _remove_live_mask
    from app.application import enqueue_page_task
    from app.services.mcp_context import workflow_run_id
    from app.services.mcp_workflows import guard_page
    from app.services.region_deletion import restore_deleted_pixels
    from app.services.region_edits import apply_page_edit
    from app.services.regions import create_region, renumber_region_keys

    with SessionLocal() as db:
        page = require_page(db, args["image_id"])
        check_page_version(db, page.id)
        updates = args.get("update", [])
        deletes = resolve_regions(db, page.id, args.get("delete", []))
        changed = resolve_regions(db, page.id, [item["region_id"] for item in updates])
        if not (updates or deletes or args.get("add")):
            raise HTTPException(422, "Supply add, update or delete edits")
        if {row.id for row in deletes} & {row.id for row in changed}:
            raise HTTPException(422, "A region cannot be updated and deleted together")
        run_id = workflow_run_id.get()
        run = guard_page(db, run_id, page.id)[0] if run_id else None
        if run:
            from app.services.mcp_batch_workflow import enabled
            from app.services.mcp_workflows import page_row
            receipt = page_row(db, run.id, page.id)
            if enabled(run) and run.options["translation_mode"] == "agent" and not receipt.preflight and not receipt.final_review:
                if args.get("add") or deletes or any(set(item["patch"]) != {"translated_text"} for item in updates):
                    raise HTTPException(409, "During agent text preparation only translated_text updates are allowed; record other issues and correct after the batch")
        for row in [*deletes, *changed]:
            if row.locked:
                raise HTTPException(409, "Locked regions cannot be edited by MCP")
            if run and run.options.get("region_ids") and row.id not in run.options["region_ids"] and (row.layout_data or {}).get("workflow_created_by") != run.id:
                raise HTTPException(422, "Region is outside frozen local repair scope")
        patches = []
        for row, item in zip(changed, updates, strict=True):
            validate_patch(item["patch"], page)
            patches.append((row.id, RegionUpdate(**item["patch"])))
        additions = []
        for value in args.get("add", []):
            validate_patch(value, page, create=True)
            additions.append(RegionCreate(**value))
        deleted_ids = [row.id for row in deletes]

        def operation(private, target):
            removed = [private.get(TextRegion, identifier) for identifier in deleted_ids]
            paths = [row.pixel_mask_path for row in removed if row.pixel_mask_path]
            needs_rebuild = bool(target.clean_path or target.rendered_path or target.text_layer_path)
            for row in removed:
                private.delete(row)
            private.flush()
            remaining = list(private.scalars(select(TextRegion).where(TextRegion.image_id == target.id)))
            fast = restore_deleted_pixels(target, removed, remaining) if removed else True
            if removed:
                renumber_region_keys(private, remaining)
            for identifier, payload in patches:
                _apply_region_patch(private, private.get(TextRegion, identifier), payload)
            created = [create_region(private, target, payload).id for payload in additions]
            if removed or created:
                _invalidate_rendered_artifacts(target)
            return {"region_ids": [*deleted_ids, *[identifier for identifier, _ in patches], *created],
                "created_ids": created, "mask_paths": paths, "rebuild": bool(removed and needs_rebuild and not fast)}

        metadata, entry_id = apply_page_edit(db, page.id, operation, "漫画翻译纠错", capture_version=True)
        page = require_page(db, args["image_id"])
        for path in metadata["mask_paths"]:
            if db.scalar(select(TextRegion.id).where(TextRegion.pixel_mask_path == path).limit(1)) is None:
                _remove_live_mask(page, path)
        task = None
        if metadata["rebuild"]:
            token = expected_versions.set({page.id: metadata["published_version"]})
            try:
                task = enqueue_page_task(db, page, "rendering", ProcessRequest(force=True,
                    options={"_editor_history_entry_id": entry_id}), start_stage="inpainting")
            finally:
                expected_versions.reset(token)
        return {"image_id": page.id, "version": current_page_version(db, page.id),
            "created": [{"id": row.id, "region_key": row.region_key} for row in resolve_regions(db, page.id, metadata["created_ids"])],
            "updated_count": len(patches), "deleted_count": len(deleted_ids),
            "rebuild_task": {"id": task.id, "status": task.status, "kind": "processing"} if task else None,
            "next_tool": "task_wait" if task else "page_state"}
