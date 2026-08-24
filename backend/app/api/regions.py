from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.helpers import region_read, require_page, require_region
from app.application import enqueue_page_task
from app.core.database import get_db
from app.models import RegionRevision, TextRegion
from app.schemas.domain import MaskOperation, ProcessRequest, RegionCreate, RegionRead, RegionUpdate, TaskRead
from app.services.inpainting import process_mask
from app.services.regions import (
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
    try:
        region = create_region(db, page, payload)
        db.commit()
        db.refresh(region)
        return region_read(region)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/regions/{region_id}", response_model=RegionRead)
def patch_region(region_id: str, payload: RegionUpdate, db: Session = Depends(get_db)) -> RegionRead:
    region = require_region(db, region_id)
    try:
        rendered_output_changed = bool(payload.model_fields_set & RENDER_AFFECTING_FIELDS)
        update_region(db, region, payload)
        if rendered_output_changed:
            # Never allow export to reuse a cached translated image whose
            # geometry, content or style no longer matches the region.
            page = require_page(db, region.image_id)
            page.rendered_path = None
            page.text_layer_path = None
        db.commit()
        db.refresh(region)
        return region_read(region)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/regions/{region_id}")
def delete_region(region_id: str, db: Session = Depends(get_db)):
    region = require_region(db, region_id)
    page = require_page(db, region.image_id)
    region_id_value = region.id
    mask_path = region.pixel_mask_path
    needs_rebuild = bool(page.clean_path or page.rendered_path or page.text_layer_path)
    db.delete(region)
    db.commit()
    if mask_path:
        get_storage().absolute(mask_path).unlink(missing_ok=True)
    if not needs_rebuild:
        return {"region_id": region_id_value, "rebuild_task": None}
    # The clean image is a page-level composite. Rebuild it from the immutable
    # original with only the remaining masks, then recreate the translated layer.
    task = enqueue_page_task(
        db,
        page,
        "rendering",
        ProcessRequest(force=True),
        start_stage="inpainting",
    )
    return {"region_id": region_id_value, "rebuild_task": TaskRead.model_validate(task)}


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
    mask_paths = [region.pixel_mask_path for region in regions if region.pixel_mask_path]
    needs_rebuild = bool(page.clean_path or page.rendered_path or page.text_layer_path)
    for region in regions:
        db.delete(region)
    db.commit()
    for mask_path in mask_paths:
        get_storage().absolute(mask_path).unlink(missing_ok=True)

    if not needs_rebuild:
        return {"region_ids": unique_ids, "rebuild_task": None}
    task = enqueue_page_task(
        db,
        page,
        "rendering",
        ProcessRequest(force=True),
        start_stage="inpainting",
    )
    return {"region_ids": unique_ids, "rebuild_task": TaskRead.model_validate(task)}


@router.post("/regions/{region_id}/copy", response_model=RegionRead, status_code=201)
def duplicate_region(region_id: str, db: Session = Depends(get_db)) -> RegionRead:
    duplicate = copy_region(db, require_region(db, region_id))
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
        mask_paths = [region.pixel_mask_path for region in regions if region.pixel_mask_path]
        needs_rebuild = bool(page.clean_path or page.rendered_path or page.text_layer_path)
        merged = merge_regions(db, regions)
        db.commit()
        db.refresh(merged)
        for mask_path in mask_paths:
            get_storage().absolute(mask_path).unlink(missing_ok=True)
        if not needs_rebuild:
            return {"region": region_read(merged), "rebuild_task": None}
        # Merging removes the source masks, so this structural operation must
        # rebuild the complete clean page rather than use selected-region
        # incremental repair semantics for the new merged region.
        task = enqueue_page_task(
            db,
            page,
            "rendering",
            ProcessRequest(force=True, options={"rebuild_clean": True}),
            region_id=merged.id,
            start_stage="mask",
        )
        return {"region": region_read(merged), "rebuild_task": TaskRead.model_validate(task)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/regions/{region_id}/split", response_model=list[RegionRead])
def split_region_endpoint(region_id: str, axis: str = "auto", db: Session = Depends(get_db)) -> list[RegionRead]:
    try:
        regions = split_region(db, require_region(db, region_id), axis)
        db.commit()
        return [region_read(region) for region in regions]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/regions/{region_id}/mask", response_model=RegionRead)
def upload_mask(region_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)) -> RegionRead:
    region = require_region(db, region_id)
    page = require_page(db, region.image_id)
    try:
        image = Image.open(file.file)
        if image.size != (page.width, page.height):
            raise HTTPException(422, f"Mask must be {page.width}x{page.height} pixels")
        if image.mode == "RGBA":
            mask = image.getchannel("A")
        else:
            mask = image.convert("L")
        output = get_storage().page_dir(page.project_id, page.id) / "masks" / f"{region.id}.png"
        save_revision(db, region, "mask_edited")
        mask.save(output, "PNG")
        region.pixel_mask_path = get_storage().relative(output)
        db.commit()
        db.refresh(region)
        return region_read(region)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, "Invalid mask image") from exc


@router.post("/regions/{region_id}/mask/operation", response_model=RegionRead)
def mask_operation(region_id: str, payload: MaskOperation, db: Session = Depends(get_db)) -> RegionRead:
    region = require_region(db, region_id)
    if not region.pixel_mask_path:
        raise HTTPException(409, "Region does not have a mask")
    path = get_storage().absolute(region.pixel_mask_path)
    save_revision(db, region, f"mask_{payload.operation}")
    process_mask(path, path, payload.operation, payload.amount)
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
        restore_revision(db, region, revision)
        db.commit()
        db.refresh(region)
        return region_read(region)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
