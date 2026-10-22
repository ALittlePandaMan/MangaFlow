from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.helpers import image_read, region_read, require_page, require_region
from app.application import enqueue_page_task
from app.core.database import get_db
from app.models import EditorHistoryEntry, ImagePage, RegionRevision, TextRegion
from app.schemas.domain import MaskOperation, ProcessRequest, RegionCreate, RegionRead, RegionUpdate, TaskRead
from app.services.editor_history import history_state
from app.services.inpainting import process_mask
from app.services.mask_history import checkpoint as mask_checkpoint
from app.services.mask_history import mask_history_window
from app.services.region_deletion import restore_deleted_pixels
from app.services.region_edits import apply_page_edit, apply_region_edits
from app.services.regions import (
    STALE_CLEAN_PATH_KEY,
    copy_region,
    create_region,
    merge_regions,
    renumber_region_keys,
    restore_revision,
    save_revision,
    split_region,
    update_region,
)
from app.services.workflow_state import decode, record
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
    # Automatic audits describe an earlier bitmap. A deliberate brush/eraser
    # edit must not inherit its stale completeness/disagreement diagnostics.
    mask_generation.pop("mask_integrity", None)
    mask_generation.pop("segmentation_disagreement", None)
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
        db.scalars(select(TextRegion).where(TextRegion.image_id == image_id).order_by(
            TextRegion.reading_order, TextRegion.created_at, TextRegion.id,
        )).all()
    )
    return [region_read(region) for region in regions]


@router.post("/images/{image_id}/regions", response_model=RegionRead, status_code=201)
def add_region(image_id: str, payload: RegionCreate, db: Session = Depends(get_db)) -> RegionRead:
    require_page(db, image_id)
    if payload.id is not None and db.get(TextRegion, str(payload.id)) is not None:
        raise HTTPException(409, "Region ID already exists")
    try:
        def create(private,page):
            region = create_region(private,page,payload)
            _invalidate_rendered_artifacts(page)
            return {"region_ids":[region.id]}
        metadata,_ = apply_page_edit(db,image_id,create,"创建文字区域")
        return region_read(require_region(db,metadata["region_ids"][0]))
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
    try:
        rows = apply_region_edits(db,region.image_id,[(region_id,payload)],
            "显示/隐藏文字区域" if "visible" in payload.model_fields_set else "编辑文字区域",_apply_region_patch)
        return region_read(rows[0])
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
    try:
        rows = apply_region_edits(db,regions[ids[0]].image_id,[(change.id,change.patch) for change in changes],
                                 "批量编辑文字区域",_apply_region_patch)
        return [region_read(row) for row in rows]
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


@router.delete("/regions/{region_id}")
def delete_region(region_id: str, db: Session = Depends(get_db)):
    region = require_region(db, region_id)
    image_id = region.image_id
    metadata,entry_id = _prepare_delete(db,image_id,[region_id],"删除文字区域")
    page = require_page(db,image_id)
    if metadata["fast"] or not metadata["needs_rebuild"]:
        return {"region_id": region_id, "region_keys": metadata["region_keys"], "rebuild_task": None, "page":image_read(page,include_history=True), "editor_history": history_state(db, page.id)}
    # The clean image is a page-level composite. Rebuild it from the immutable
    # original with only the remaining masks, then recreate the translated layer.
    task = enqueue_page_task(
        db,
        page,
        "rendering",
        ProcessRequest(force=True, options={"_editor_history_entry_id": entry_id}),
        start_stage="inpainting",
    )
    return {"region_id": region_id, "region_keys": metadata["region_keys"], "rebuild_task": TaskRead.model_validate(task), "editor_history": history_state(db, page.id)}


def _prepare_delete(db,image_id,identifiers,action):
    def remove(private,page):
        removed = [private.get(TextRegion,identifier) for identifier in identifiers]
        if any(row is None for row in removed):
            raise HTTPException(409,"区域列表已变化，请刷新后重试")
        paths = [row.pixel_mask_path for row in removed if row.pixel_mask_path]
        needs_rebuild = bool(page.clean_path or page.rendered_path or page.text_layer_path)
        for row in removed:
            private.delete(row)
        private.flush()
        remaining = list(private.scalars(select(TextRegion).where(TextRegion.image_id==page.id)))
        names = renumber_region_keys(private,remaining)
        fast = restore_deleted_pixels(page,removed,remaining)
        if fast:
            _invalidate_rendered_artifacts(page)
        return {"region_ids":identifiers,"mask_paths":paths,"region_keys":names,"fast":fast,"needs_rebuild":needs_rebuild}
    metadata,entry_id = apply_page_edit(db,image_id,remove,action)
    page = require_page(db,image_id)
    # Shared legacy mask paths may still belong to a retained region.
    for path in metadata["mask_paths"]:
        if db.scalar(select(TextRegion.id).where(TextRegion.pixel_mask_path==path).limit(1)) is None:
            _remove_live_mask(page,path)
    return metadata,entry_id


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

    image_id = regions[0].image_id
    metadata,entry_id = _prepare_delete(db,image_id,unique_ids,"批量删除文字区域")
    page = require_page(db,image_id)
    if metadata["fast"] or not metadata["needs_rebuild"]:
        return {"region_ids": unique_ids, "region_keys": metadata["region_keys"], "rebuild_task": None, "page":image_read(page,include_history=True), "editor_history": history_state(db, page.id)}
    task = enqueue_page_task(
        db,
        page,
        "rendering",
        ProcessRequest(force=True, options={"_editor_history_entry_id": entry_id}),
        start_stage="inpainting",
    )
    return {"region_ids": unique_ids, "region_keys": metadata["region_keys"], "rebuild_task": TaskRead.model_validate(task), "editor_history": history_state(db, page.id)}


@router.post("/regions/{region_id}/copy", response_model=RegionRead, status_code=201)
def duplicate_region(region_id: str, db: Session = Depends(get_db)) -> RegionRead:
    region = require_region(db, region_id)
    def duplicate(private,page):
        copy = copy_region(private,private.get(TextRegion,region_id))
        _invalidate_rendered_artifacts(page)
        return {"region_ids":[copy.id]}
    metadata,_ = apply_page_edit(db,region.image_id,duplicate,"复制文字区域")
    return region_read(require_region(db,metadata["region_ids"][0]))


@router.post("/regions/merge")
def merge_region_list(region_ids: list[str], db: Session = Depends(get_db)):
    unique_ids = list(dict.fromkeys(region_ids))
    regions = list(db.scalars(select(TextRegion).where(TextRegion.id.in_(unique_ids))).all())
    if len(regions) != len(unique_ids):
        raise HTTPException(404, "One or more regions were not found")
    try:
        if len(regions)<2 or len({region.image_id for region in regions})!=1:
            raise ValueError("At least two regions from the same page are required")
        image_id = regions[0].image_id
        def merge(private,page):
            rows = [private.get(TextRegion,identifier) for identifier in unique_ids]
            if any(row is None for row in rows):
                raise HTTPException(409,"区域列表已变化，请刷新后重试")
            paths = [row.pixel_mask_path for row in rows if row.pixel_mask_path]
            needs_rebuild = bool(page.clean_path or page.rendered_path or page.text_layer_path)
            merged = merge_regions(private,rows)
            remaining = list(private.scalars(select(TextRegion).where(TextRegion.image_id==page.id)))
            return {"region_ids":unique_ids,"merged_id":merged.id,"mask_paths":paths,
                    "region_keys":renumber_region_keys(private,remaining),"needs_rebuild":needs_rebuild}
        metadata,entry_id = apply_page_edit(db,image_id,merge,"合并文字区域")
        page = require_page(db,image_id)
        merged = require_region(db,metadata["merged_id"])
        for path in metadata["mask_paths"]:
            if db.scalar(select(TextRegion.id).where(TextRegion.pixel_mask_path==path).limit(1)) is None:
                _remove_live_mask(page,path)
        if not metadata["needs_rebuild"]:
            return {"region": region_read(merged), "region_keys": metadata["region_keys"], "rebuild_task": None}
        # Merging removes the source masks, so this structural operation must
        # rebuild the complete clean page rather than use selected-region
        # incremental repair semantics for the new merged region.
        task = enqueue_page_task(
            db,
            page,
            "rendering",
            ProcessRequest(force=True, options={"rebuild_clean": True, "_editor_history_entry_id": entry_id}),
            region_id=merged.id,
            start_stage="mask",
        )
        return {"region": region_read(merged), "region_keys": metadata["region_keys"], "rebuild_task": TaskRead.model_validate(task)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/regions/{region_id}/split", response_model=list[RegionRead])
def split_region_endpoint(region_id: str, axis: str = "auto", db: Session = Depends(get_db)) -> list[RegionRead]:
    try:
        region = require_region(db, region_id)
        def split(private,page):
            regions = split_region(private,private.get(TextRegion,region_id),axis)
            _invalidate_rendered_artifacts(page,clean=True)
            return {"region_ids":[item.id for item in regions]}
        metadata,_ = apply_page_edit(db,region.image_id,split,"拆分文字区域")
        return [region_read(require_region(db,identifier)) for identifier in metadata["region_ids"]]
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
    image_id = region.image_id
    details = {}

    def prepare(private,page):
        region = private.get(TextRegion,region_id)
        if region.locked:
            raise HTTPException(409, "请先解锁区域再编辑蒙版")
        if expected_updated_at is not None:
            try:
                expected = datetime.fromisoformat(expected_updated_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(422, "Invalid region version") from exc
            def utc_naive(value: datetime) -> datetime:
                return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
            if utc_naive(expected) != utc_naive(region.updated_at):
                raise HTTPException(409, "区域已发生变化，请关闭并重新打开蒙版编辑器")
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
        save_revision(private, region, "mask_edited")
        mask.save(output, "PNG", compress_level=1)
        region.pixel_mask_path = get_storage().relative(output)
        _mark_manual_mask_edit(region)
        # Keep the last successful clean composite until the edited regions
        # have been repaired. Replacing a mask does not undo other repairs.
        _invalidate_rendered_artifacts(page)
        details.update(bounds=box if partial else None,before=before_patch if partial else None,after=mask)
        return {"region_ids":[region_id]}

    def sparse_history(private,page,before,after,metadata):
        entry = EditorHistoryEntry(project_id=page.project_id,image_id=page.id,before=before,after=after)
        delta = mask_checkpoint(entry,private.get(TextRegion,region_id),details["bounds"],details["before"],details["after"])
        return {"_mask_delta":delta}

    try:
        apply_page_edit(db,image_id,prepare,"编辑蒙版",scope=[region_id],prepare_extra=sparse_history)
        return region_read(require_region(db,region_id))
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
    def prepare(private,page):
        region = private.get(TextRegion,region_id)
        if region.locked:
            raise HTTPException(409, "请先解锁区域再编辑蒙版")
        if not region.pixel_mask_path:
            raise HTTPException(409, "Region does not have a mask")
        path = get_storage().absolute(region.pixel_mask_path)
        output = get_storage().page_dir(page.project_id,page.id)/"masks"/f"{region.id}-{uuid4().hex}.png"
        save_revision(private, region, f"mask_{payload.operation}")
        process_mask(path, output, payload.operation, payload.amount)
        region.pixel_mask_path = get_storage().relative(output)
        _mark_manual_mask_edit(region)
        _invalidate_rendered_artifacts(page)
        return {"region_ids":[region_id]}

    def sparse_history(private,page,before,after,metadata):
        entry = EditorHistoryEntry(project_id=page.project_id,image_id=page.id,before=before,after=after)
        return {"_mask_delta":mask_checkpoint(entry,private.get(TextRegion,region_id))}

    apply_page_edit(db,region.image_id,prepare,"调整蒙版",scope=[region_id],prepare_extra=sparse_history)
    return region_read(require_region(db,region_id))


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
    if revision.region_id!=region_id:
        raise HTTPException(422,"Revision does not belong to this region")
    frozen = record(revision)
    try:
        def restore(private,page):
            current = private.get(TextRegion,region_id)
            original = (deepcopy(current.bbox),deepcopy(current.polygon))
            restore_revision(private,current,RegionRevision(**decode(RegionRevision,frozen)))
            source_changed = original!=(current.bbox,current.polygon)
            _invalidate_rendered_artifacts(page,clean=source_changed)
            return {"region_ids":[region_id]}
        apply_page_edit(db,region.image_id,restore,"恢复区域版本",scope=[region_id])
        return region_read(require_region(db,region_id))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
