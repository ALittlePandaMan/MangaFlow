from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.models import ImagePage, RegionRevision, TextRegion
from app.schemas.domain import RegionCreate, RegionUpdate
from app.utils.geometry import bbox_to_polygon, polygon_to_bbox
from sqlalchemy import func, select
from sqlalchemy.orm import Session

SNAPSHOT_FIELDS = (
    "polygon",
    "bbox",
    "translated_polygon",
    "translated_bbox",
    "source_text",
    "translated_text",
    "confidence",
    "orientation",
    "reading_order",
    "panel_id",
    "bubble_id",
    "region_type",
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
    "locked",
    "visible",
    "needs_review",
    "review_reasons",
    "layout_warning",
    "layout_data",
    "pixel_mask_path",
    "inpainted_path",
)


def region_snapshot(region: TextRegion) -> dict[str, Any]:
    return {field: deepcopy(getattr(region, field)) for field in SNAPSHOT_FIELDS}


def save_revision(db: Session, region: TextRegion, action: str) -> None:
    db.add(RegionRevision(region_id=region.id, action=action, snapshot=region_snapshot(region)))


def _without_automatic_grouping(layout_data: dict[str, Any] | None) -> dict[str, Any]:
    output = deepcopy(layout_data or {})
    detection = output.get("detection")
    if isinstance(detection, dict):
        detection.pop("balloon_assignment", None)
        detection.pop("line_grouping", None)
        detection.pop("instance_split", None)
    return output


def next_region_key(db: Session, image_id: str) -> str:
    count = db.scalar(select(func.count(TextRegion.id)).where(TextRegion.image_id == image_id)) or 0
    used = set(db.scalars(select(TextRegion.region_key).where(TextRegion.image_id == image_id)).all())
    number = count + 1
    while f"R{number:03d}" in used:
        number += 1
    return f"R{number:03d}"


def create_region(db: Session, page: ImagePage, data: RegionCreate) -> TextRegion:
    values = data.model_dump(exclude={"region_key"}, mode="json")
    if not values["bbox"] and values["polygon"]:
        values["bbox"] = polygon_to_bbox(values["polygon"])
    if not values["polygon"] and values["bbox"]:
        values["polygon"] = bbox_to_polygon(values["bbox"])
    if not values["bbox"]:
        raise ValueError("A region needs a bbox or polygon")
    if not values["translated_bbox"]:
        values["translated_bbox"] = deepcopy(values["bbox"])
    if not values["translated_polygon"]:
        values["translated_polygon"] = deepcopy(values["polygon"])
    region = TextRegion(
        image_id=page.id,
        region_key=data.region_key or next_region_key(db, page.id),
        layout_data={"manual": True},
        **values,
    )
    db.add(region)
    db.flush()
    save_revision(db, region, "created")
    return region


def update_region(db: Session, region: TextRegion, data: RegionUpdate, action: str = "updated") -> TextRegion:
    if region.locked and data.locked is not False:
        # Manual editing remains possible, but pipeline callers enforce locks before reaching this service.
        pass
    save_revision(db, region, action)
    changes = data.model_dump(exclude_unset=True, mode="json")
    source_geometry_changed = any(
        field in changes and changes[field] != getattr(region, field)
        for field in ("polygon", "bbox")
    )
    for field, value in changes.items():
        setattr(region, field, value)
    if "region_type" in changes:
        # Mask analysis may suggest a repair strategy, but an explicit choice
        # made in the editor must remain authoritative on later reruns.
        region.layout_data = {**(region.layout_data or {}), "region_type_manual": True}
    if "polygon" in changes and "bbox" not in changes and region.polygon:
        region.bbox = polygon_to_bbox(region.polygon)
    elif "bbox" in changes and "polygon" not in changes and region.bbox:
        region.polygon = bbox_to_polygon(region.bbox)
    if source_geometry_changed:
        # Once source geometry is edited, its original model assignment and
        # per-line polygons are stale and must not drive later mask creation.
        # It is now user-authored geometry and must survive forced detection.
        region.bubble_id = None
        region.layout_data = {**_without_automatic_grouping(region.layout_data), "manual": True}
    if "translated_polygon" in changes and "translated_bbox" not in changes and region.translated_polygon:
        region.translated_bbox = polygon_to_bbox(region.translated_polygon)
    elif "translated_bbox" in changes and "translated_polygon" not in changes and region.translated_bbox:
        region.translated_polygon = bbox_to_polygon(region.translated_bbox)
    db.flush()
    return region


def copy_region(db: Session, region: TextRegion, offset: float = 12) -> TextRegion:
    values = region_snapshot(region)
    values.pop("pixel_mask_path", None)
    values.pop("inpainted_path", None)
    values["bbox"] = [region.bbox[0] + offset, region.bbox[1] + offset, region.bbox[2], region.bbox[3]]
    values["polygon"] = [[point[0] + offset, point[1] + offset] for point in region.polygon]
    translated_bbox = region.translated_bbox or region.bbox
    translated_polygon = region.translated_polygon or region.polygon
    values["translated_bbox"] = [translated_bbox[0] + offset, translated_bbox[1] + offset, translated_bbox[2], translated_bbox[3]]
    values["translated_polygon"] = [[point[0] + offset, point[1] + offset] for point in translated_polygon]
    values["locked"] = False
    values["bubble_id"] = None
    values["layout_data"] = {**_without_automatic_grouping(values.get("layout_data")), "manual": True}
    duplicate = TextRegion(image_id=region.image_id, region_key=next_region_key(db, region.image_id), **values)
    db.add(duplicate)
    db.flush()
    save_revision(db, duplicate, "copied")
    return duplicate


def merge_regions(db: Session, regions: list[TextRegion]) -> TextRegion:
    if len(regions) < 2 or len({region.image_id for region in regions}) != 1:
        raise ValueError("At least two regions from the same page are required")
    ordered = sorted(regions, key=lambda region: region.reading_order)
    left = min(region.bbox[0] for region in ordered)
    top = min(region.bbox[1] for region in ordered)
    right = max(region.bbox[0] + region.bbox[2] for region in ordered)
    bottom = max(region.bbox[1] + region.bbox[3] for region in ordered)
    target = ordered[0]
    save_revision(db, target, "before_merge")
    target.bbox = [left, top, right - left, bottom - top]
    target.polygon = bbox_to_polygon(target.bbox)
    translated_boxes = [region.translated_bbox or region.bbox for region in ordered]
    translated_left = min(box[0] for box in translated_boxes)
    translated_top = min(box[1] for box in translated_boxes)
    translated_right = max(box[0] + box[2] for box in translated_boxes)
    translated_bottom = max(box[1] + box[3] for box in translated_boxes)
    target.translated_bbox = [
        translated_left,
        translated_top,
        translated_right - translated_left,
        translated_bottom - translated_top,
    ]
    target.translated_polygon = bbox_to_polygon(target.translated_bbox)
    target.rotation = 0.0
    target.perspective_warp = False
    joiner = "" if target.orientation == "vertical" else " "
    target.source_text = joiner.join(region.source_text for region in ordered if region.source_text)
    target.translated_text = joiner.join(region.translated_text for region in ordered if region.translated_text)
    target.confidence = min((region.confidence for region in ordered), default=0.0)
    target.visible = any(region.visible for region in ordered)
    target.bubble_id = None
    target.pixel_mask_path = None
    target.layout_data = {**_without_automatic_grouping(target.layout_data), "manual": True}
    for region in ordered[1:]:
        db.delete(region)
    db.flush()
    return target


def split_region(db: Session, region: TextRegion, axis: str = "auto") -> list[TextRegion]:
    save_revision(db, region, "before_split")
    x, y, width, height = region.bbox
    resolved_axis = ("vertical" if width >= height else "horizontal") if axis == "auto" else axis
    boxes = _split_bbox([x, y, width, height], resolved_axis)
    translated_boxes = _split_bbox(region.translated_bbox or region.bbox, resolved_axis)
    source_parts = _split_text(region.source_text)
    translated_parts = _split_text(region.translated_text)
    region.bbox = boxes[0]
    region.polygon = bbox_to_polygon(boxes[0])
    region.translated_bbox = translated_boxes[0]
    region.translated_polygon = bbox_to_polygon(translated_boxes[0])
    region.perspective_warp = False
    region.source_text = source_parts[0]
    region.translated_text = translated_parts[0]
    region.bubble_id = None
    region.pixel_mask_path = None
    region.layout_data = {**_without_automatic_grouping(region.layout_data), "manual": True}
    values = region_snapshot(region)
    values["bbox"] = boxes[1]
    values["polygon"] = bbox_to_polygon(boxes[1])
    values["translated_bbox"] = translated_boxes[1]
    values["translated_polygon"] = bbox_to_polygon(translated_boxes[1])
    values["source_text"] = source_parts[1]
    values["translated_text"] = translated_parts[1]
    values["reading_order"] = region.reading_order + 1
    values["locked"] = False
    duplicate = TextRegion(image_id=region.image_id, region_key=next_region_key(db, region.image_id), **values)
    db.add(duplicate)
    db.flush()
    save_revision(db, duplicate, "split_created")
    return [region, duplicate]


def restore_revision(db: Session, region: TextRegion, revision: RegionRevision) -> TextRegion:
    if revision.region_id != region.id:
        raise ValueError("Revision does not belong to this region")
    current = region_snapshot(region)
    for field, value in revision.snapshot.items():
        if field in SNAPSHOT_FIELDS:
            setattr(region, field, deepcopy(value))
    db.add(RegionRevision(region_id=region.id, action="restore_checkpoint", snapshot=current))
    db.flush()
    return region


def _split_text(text: str) -> tuple[str, str]:
    midpoint = len(text) // 2
    for index in range(midpoint, min(len(text), midpoint + 12)):
        if text[index : index + 1] in {" ", "\n", "。", "！", "？", ",", "，"}:
            midpoint = index + 1
            break
    return text[:midpoint].strip(), text[midpoint:].strip()


def _split_bbox(bbox: list[float], axis: str) -> list[list[float]]:
    x, y, width, height = bbox
    if axis == "vertical":
        return [[x, y, width / 2, height], [x + width / 2, y, width / 2, height]]
    if axis == "horizontal":
        return [[x, y, width, height / 2], [x, y + height / 2, width, height / 2]]
    raise ValueError("axis must be auto, vertical or horizontal")
