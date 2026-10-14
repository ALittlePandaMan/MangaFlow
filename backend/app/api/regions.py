from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.helpers import image_read, region_read, require_page, require_region
from app.application import enqueue_page_task
from app.core.database import get_db
from app.models import ImagePage, RegionRevision, TextRegion
from app.schemas.domain import MaskOperation, ProcessRequest, RegionCreate, RegionRead, RegionUpdate, TaskRead
from app.services.editor_history import capture_page_state, history_state, record_page_edit
from app.services.inpainting import process_mask
from app.services.mask_history import checkpoint as mask_checkpoint
from app.services.mask_history import mask_history_window
from app.services.region_deletion import restore_deleted_pixels
from app.services.regions import (
    STALE_CLEAN_PATH_KEY,
    copy_region,
    create_region,
    merge_regions,
    restore_revision,
    save_revision,
    split_region,
    update_region,
)
from app.storage import get_storage

router = APIRouter(tags=["regions"])

RENDER_AFFECTING_FIELDS = {
    "translated_polygon",
    "translated_bbox",
    "translated_text",
    "orientation",
    "font_size",
    "auto_font_size",
    "font_family",
    "font_weight",
    "text_color",
    "stroke_color",
    "stroke_width",
    "alignment",
    "line_spacing",
    "character_spacing",
    "rotation",
    "perspective_warp",
    "opacity",
    "visible",
}
SOURCE_GEOMETRY_FIELDS = {"polygon", "bbox"}


def _invalidate_rendered_artifacts(page: ImagePage, *, clean: bool = False) -> None:
    metadata = dict(page.metadata_json or {})
    versions = dict(metadata.get("artifact_versions", {}))
    if clean:
        # Do not expose an old clean composite after its source geometry has
        # moved. Keep its private path only so selected-region repair can copy
        # back unaffected repairs without rerunning the full page.
        if page.clean_path:
            metadata[STALE_CLEAN_PATH_KEY] = page.clean_path
        page.clean_path = None
        versions.pop("clean", None)
    page.rendered_path = None
    page.text_layer_path = None
    versions.pop("rendered", None)
    versions.pop("text_layer", None)
    if versions:
        metadata["artifact_versions"] = versions
    else:
        metadata.pop("artifact_versions", None)
    page.metadata_json = metadata


def _mark_manual_mask_edit(region: TextRegion) -> None:
    layout_data = dict(region.layout_data or {})
    mask_generation = dict(layout_data.get("mask_generation", {}))
    mask_generation.update(
        {
            "method": "manual_mask",
            "repair_pending": True,
            "constraint": {
                "version": 1,
                "status": "manual",
                "reason": "manual_mask_edit",
            },
        }
    )
    region.layout_data = {**layout_data, "mask_generation": mask_generation}
    region.inpainted_path = None


@router.get("/images/{image_id}/regions", response_model=list[RegionRead])
def list_regions(image_id: str, db: Session = Depends(get_db)) -> list[RegionRead]:
    require_page(db, image_id)
    regions = list(
        db.scalars(select(TextRegion).where(TextRegion.image_id == image_id).order_by(TextRegion.reading_order)).all()
    )
    return [region_read(region) for region in regions]


@router.post("/images/{image_id}/regions", response_model=RegionRead, status_code=201)
def add_region(image_id: str, payload: RegionCreate, db: Session = Depends(get_db)) -> RegionRead:
    page = require_page(db, image_id)
    before = capture_page_state(db, page, editing=True)
    if payload.id is not None and db.get(TextRegion, str(payload.id)) is not None:
        raise HTTPException(409, "Region ID already exists")
    try:
        region = create_region(db, page, payload)
        _invalidate_rendered_artifacts(page)
        record_page_edit(db, page, before, "创建文字区域", [region.id])
        db.commit()
        db.refresh(region)
        return region_read(region)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _apply_region_patch(db: Session, region: TextRegion, payload: RegionUpdate) -> None:
    rendered_fields = payload.model_fields_set & RENDER_AFFECTING_FIELDS
    source_fields = payload.model_fields_set & SOURCE_GEOMETRY_FIELDS
    rendered_before = {field: deepcopy(getattr(region, field)) for field in RENDER_AFFECTING_FIELDS}
    source_before = {field: deepcopy(getattr(region, field)) for field in SOURCE_GEOMETRY_FIELDS}
    update_region(db, region, payload)
    rendered_output_changed = bool(rendered_fields) and any(getattr(region, field) != value for field, value in rendered_before.items())
    source_geometry_changed = bool(source_fields) and any(getattr(region, field) != value for field, value in source_before.items())
    if source_geometry_changed or rendered_output_changed:
        page = require_page(db, region.image_id)
        _invalidate_rendered_artifacts(page, clean=source_geometry_changed)


def _remove_live_mask(page: ImagePage, mask_path: str) -> None:
    path = get_storage().absolute(mask_path)
    history_directory = get_storage().project_dir(page.project_id) / "history"
    if not path.is_relative_to(history_directory):
        path.unlink(missing_ok=True)


@router.patch("/regions/{region_id}", response_model=RegionRead)
def patch_region(region_id: str, payload: RegionUpdate, db: Session = Depends(get_db)) -> RegionRead:
    region = require_region(db, region_id)
    page = require_page(db, region.image_id)
    before = capture_page_state(db, page, editing=True, region_ids=[region_id])
    try:
        _apply_region_patch(db, region, payload)
        record_page_edit(db, page, before, "显示/隐藏文字区域" if "visible" in payload.model_fields_set else "编辑文字区域", [region.id])
        db.commit()
        db.refresh(region)
        return region_read(region)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class BulkRegionChange(BaseModel):
    id: str
    patch: RegionUpdate


@router.post("/regions/bulk-update", response_model=list[RegionRead])
def bulk_update_regions(changes: list[BulkRegionChange], db: Session = Depends(get_db)):
    ids = [change.id for change in changes]
    if not ids or len(set(ids)) != len(ids):
        raise HTTPException(422, "需要非重复的区域列表")
    regions = {region.id: region for region in db.scalars(select(TextRegion).where(TextRegion.id.in_(ids))).all()}
    if len(regions) != len(ids):
        raise HTTPException(404, "One or more regions were not found")
    if len({region.image_id for region in regions.values()}) != 1:
        raise HTTPException(422, "All regions must belong to the same page")
    page = require_page(db, regions[ids[0]].image_id)
    before = capture_page_state(db, page, editing=True, region_ids=ids)
    for change in changes:
        _apply_region_patch(db, regions[change.id], change.patch)
    record_page_edit(db, page, before, "批量编辑文字区域", ids)
    db.commit()
    return [region_read(regions[id]) for id in ids]


@router.delete("/regions/{region_id}")
def delete_region(region_id: str, db: Session = Depends(get_db)):
    region = require_region(db, region_id)
    page = require_page(db, region.image_id)
    before = capture_page_state(db, page, editing=True)
    region_id_value = region.id
    mask_path = region.pixel_mask_path
    needs_rebuild = bool(page.clean_path or page.rendered_path or page.text_layer_path)
    db.delete(region)
    db.flush()
    remaining=list(db.scalars(select(TextRegion).where(TextRegion.image_id == page.id)).all())
    fast=restore_deleted_pixels(page,[region],remaining)
    if fast:
        _invalidate_rendered_artifacts(page)
    entry = record_page_edit(db, page, before, "删除文字区域", [region_id_value])
    db.commit()
    if mask_path:
        _remove_live_mask(page, mask_path)
    if fast or not needs_rebuild:
        return {"region_id": region_id_value, "rebuild_task": None, "page":image_read(page,include_history=True), "editor_history": history_state(db, page.id)}
    # The clean image is a page-level composite. Rebuild it from the immutable
    # original with only the remaining masks, then recreate the translated layer.
    task = enqueue_page_task(
        db,
        page,
        "rendering",
        ProcessRequest(force=True, options={"_editor_history_entry_id": entry.id if entry else None}),
        start_stage="inpainting",
    )
    return {"region_id": region_id_value, "rebuild_task": TaskRead.model_validate(task), "editor_history": history_state(db, page.id)}


@router.post("/regions/bulk-delete")
def bulk_delete_regions(region_ids: list[str], db: Session = Depends(get_db)):
    unique_ids = list(dict.fromkeys(region_ids))
    if not unique_ids:
        raise HTTPException(422, "At least one region is required")
    regions = list(db.scalars(select(TextRegion).where(TextRegion.id.in_(unique_ids))).all())
    if len(regions) != len(unique_ids):
        raise HTTPException(404, "One or more regions were not found")
    image_ids = {region.image_id for region in regions}
    if len(image_ids) != 1:
        raise HTTPException(422, "All regions must belong to the same page")

    page = require_page(db, regions[0].image_id)
    before = capture_page_state(db, page, editing=True)
    mask_paths = [region.pixel_mask_path for region in regions if region.pixel_mask_path]
    needs_rebuild = bool(page.clean_path or page.rendered_path or page.text_layer_path)
    for region in regions:
        db.delete(region)
    db.flush()
    remaining=list(db.scalars(select(TextRegion).where(TextRegion.image_id == page.id)).all())
    fast=restore_deleted_pixels(page,regions,remaining)
    if fast:
        _invalidate_rendered_artifacts(page)
    entry = record_page_edit(db, page, before, "批量删除文字区域", unique_ids)
    db.commit()
    for mask_path in mask_paths:
        _remove_live_mask(page, mask_path)

    if fast or not needs_rebuild:
        return {"region_ids": unique_ids, "rebuild_task": None, "page":image_read(page,include_history=True), "editor_history": history_state(db, page.id)}
    task = enqueue_page_task(
        db,
        page,
        "rendering",
        ProcessRequest(force=True, options={"_editor_history_entry_id": entry.id if entry else None}),
        start_stage="inpainting",
    )
    return {"region_ids": unique_ids, "rebuild_task": TaskRead.model_validate(task), "editor_history": history_state(db, page.id)}


@router.post("/regions/{region_id}/copy", response_model=RegionRead, status_code=201)
def duplicate_region(region_id: str, db: Session = Depends(get_db)) -> RegionRead:
    region = require_region(db, region_id)
    page = require_page(db, region.image_id)
    before = capture_page_state(db, page, editing=True)
    duplicate = copy_region(db, region)
    _invalidate_rendered_artifacts(page)
    record_page_edit(db, page, before, "复制文字区域", [duplicate.id])
    db.commit()
    db.refresh(duplicate)
    return region_read(duplicate)


@router.post("/regions/merge")
def merge_region_list(region_ids: list[str], db: Session = Depends(get_db)):
    unique_ids = list(dict.fromkeys(region_ids))
    regions = list(db.scalars(select(TextRegion).where(TextRegion.id.in_(unique_ids))).all())
    if len(regions) != len(unique_ids):
        raise HTTPException(404, "One or more regions were not found")
    try:
        if not regions:
            raise ValueError("At least two regions from the same page are required")
        page = require_page(db, regions[0].image_id)
        before = capture_page_state(db, page, editing=True)
        mask_paths = [region.pixel_mask_path for region in regions if region.pixel_mask_path]
        needs_rebuild = bool(page.clean_path or page.rendered_path or page.text_layer_path)
        merged = merge_regions(db, regions)
        entry = record_page_edit(db, page, before, "合并文字区域", unique_ids)
        db.commit()
        db.refresh(merged)
        for mask_path in mask_paths:
            _remove_live_mask(page, mask_path)
        if not needs_rebuild:
            return {"region": region_read(merged), "rebuild_task": None}
        # Merging removes the source masks, so this structural operation must
        # rebuild the complete clean page rather than use selected-region
        # incremental repair semantics for the new merged region.
        task = enqueue_page_task(
            db,
            page,
            "rendering",
            ProcessRequest(force=True, options={"rebuild_clean": True, "_editor_history_entry_id": entry.id if entry else None}),
            region_id=merged.id,
            start_stage="mask",
        )
        return {"region": region_read(merged), "rebuild_task": TaskRead.model_validate(task)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/regions/{region_id}/split", response_model=list[RegionRead])
def split_region_endpoint(region_id: str, axis: str = "auto", db: Session = Depends(get_db)) -> list[RegionRead]:
    try:
        region = require_region(db, region_id)
        page = require_page(db, region.image_id)
        before = capture_page_state(db, page, editing=True)
        regions = split_region(db, region, axis)
        _invalidate_rendered_artifacts(page, clean=True)
        record_page_edit(db, page, before, "拆分文字区域", [item.id for item in regions])
        db.commit()
        return [region_read(region) for region in regions]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/regions/{region_id}/mask", response_model=RegionRead)
def upload_mask(
    region_id: str,
    file: UploadFile = File(...),
    expected_updated_at: str | None = Form(None),
    x: int | None = Form(None),
    y: int | None = Form(None),
    db: Session = Depends(get_db),
) -> RegionRead:
    region = require_region(db, region_id)
    page = require_page(db, region.image_id)
    before = capture_page_state(db, page, editing=True, region_ids=[region_id])
    if region.locked:
        raise HTTPException(409, "请先解锁区域再编辑蒙版")
    if expected_updated_at is not None:
        try:
            expected = datetime.fromisoformat(expected_updated_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(422, "Invalid region version") from exc
        if expected.replace(tzinfo=None) != region.updated_at.replace(tzinfo=None):
            raise HTTPException(409, "区域已发生变化，请关闭并重新打开蒙版编辑器")
    try:
        image = Image.open(file.file)
        partial = x is not None or y is not None
        if partial and (x is None or y is None or expected_updated_at is None):
            raise HTTPException(422, "Mask patches require x, y and a region version")
        if partial and (x < 0 or y < 0 or image.width < 1 or image.height < 1 or x+image.width > page.width or y+image.height > page.height):
            raise HTTPException(422, "Mask patch is outside the page")
        if not partial and image.size != (page.width, page.height):
            raise HTTPException(422, f"Mask must be {page.width}x{page.height} pixels")
        if image.mode == "RGBA":
            mask = image.getchannel("A")
        else:
            mask = image.convert("L")
        if partial:
            if region.pixel_mask_path:
                with Image.open(get_storage().absolute(region.pixel_mask_path)) as current:
                    combined=current.convert("L")
                if combined.size != (page.width,page.height):
                    raise HTTPException(409, "Stored mask dimensions do not match the page")
            else:
                combined=Image.new("L",(page.width,page.height),0)
            box=(x,y,x+mask.width,y+mask.height)
            before_patch=combined.crop(box)
            combined.paste(mask,(x,y))
            mask=combined
        # Immutable assets keep revision snapshots valid and avoid stale image
        # caches when the same region's mask is edited repeatedly.
        output = get_storage().page_dir(page.project_id, page.id) / "masks" / f"{region.id}-{uuid4().hex}.png"
        save_revision(db, region, "mask_edited")
        mask.save(output, "PNG", compress_level=1)
        region.pixel_mask_path = get_storage().relative(output)
        _mark_manual_mask_edit(region)
        # Keep the last successful clean composite until the edited regions
        # have been repaired. Replacing a mask does not undo other repairs.
        _invalidate_rendered_artifacts(page)
        entry=record_page_edit(db, page, before, "编辑蒙版", [region.id])
        if entry:
            delta=mask_checkpoint(entry,region,box if partial else None,before_patch if partial else None,mask)
            entry.after={**entry.after,'_mask_delta':delta}
        db.commit()
        db.refresh(region)
        return region_read(region)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, "Invalid mask image") from exc


@router.get('/regions/{region_id}/mask-history')
def get_mask_history(region_id:str,db:Session=Depends(get_db)):
    return mask_history_window(db,require_region(db,region_id))


@router.post("/regions/{region_id}/mask/operation", response_model=RegionRead)
def mask_operation(region_id: str, payload: MaskOperation, db: Session = Depends(get_db)) -> RegionRead:
    region = require_region(db, region_id)
    page = require_page(db, region.image_id)
    before = capture_page_state(db, page, editing=True)
    if region.locked:
        raise HTTPException(409, "请先解锁区域再编辑蒙版")
    if not region.pixel_mask_path:
        raise HTTPException(409, "Region does not have a mask")
    path = get_storage().absolute(region.pixel_mask_path)
    output = path.with_name(f"{region.id}-{uuid4().hex}.png")
    save_revision(db, region, f"mask_{payload.operation}")
    process_mask(path, output, payload.operation, payload.amount)
    region.pixel_mask_path = get_storage().relative(output)
    _mark_manual_mask_edit(region)
    _invalidate_rendered_artifacts(page)
    record_page_edit(db, page, before, "调整蒙版", [region.id])
    db.commit()
    db.refresh(region)
    return region_read(region)


def _region_stage(stage: str, start_stage: str | None = None):
    def endpoint(region_id: str, payload: ProcessRequest, db: Session = Depends(get_db)):
        region = require_region(db, region_id)
        page = require_page(db, region.image_id)
        return enqueue_page_task(db, page, stage, payload, region_id=region.id, start_stage=start_stage)

    endpoint.__name__ = f"region_{stage}"
    return endpoint


for _public, _internal, _start in (
    ("ocr", "ocr", None),
    ("translate", "translation", None),
    # A region may have moved or been resized since its last mask. Rebuild the
    # selected mask before rerunning background repair.
    ("inpaint", "inpainting", "mask"),
    ("render", "rendering", None),
):
    router.add_api_route(
        f"/regions/{{region_id}}/{_public}",
        _region_stage(_internal, _start),
        methods=["POST"],
        response_model=TaskRead,
        status_code=202,
    )


@router.get("/regions/{region_id}/revisions")
def list_revisions(region_id: str, db: Session = Depends(get_db)) -> list[dict]:
    require_region(db, region_id)
    revisions = list(
        db.scalars(
            select(RegionRevision)
            .where(RegionRevision.region_id == region_id)
            .order_by(RegionRevision.created_at.desc())
        ).all()
    )
    return [
        {"id": item.id, "action": item.action, "created_at": item.created_at, "snapshot": item.snapshot}
        for item in revisions
    ]


@router.post("/regions/{region_id}/revisions/{revision_id}/restore", response_model=RegionRead)
def restore(region_id: str, revision_id: str, db: Session = Depends(get_db)) -> RegionRead:
    region = require_region(db, region_id)
    revision = db.get(RegionRevision, revision_id)
    if revision is None:
        raise HTTPException(404, "Revision not found")
    try:
        page = require_page(db, region.image_id)
        before = capture_page_state(db, page, editing=True)
        restore_revision(db, region, revision)
        record_page_edit(db, page, before, "恢复区域版本", [region.id])
        db.commit()
        db.refresh(region)
        return region_read(region)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
