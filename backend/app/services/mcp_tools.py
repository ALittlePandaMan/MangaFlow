"""Image evidence and geometry tools shared by every MCP client."""
import base64
import io
import math
from uuid import uuid4

from app.api.helpers import require_page, require_region
from app.core.database import SessionLocal
from app.models import AgentReviewIssue, MCPAsset, TextRegion
from app.services.mcp_context import check_page_version
from app.services.mcp_files import asset_path
from app.services.mcp_preview_images import comparison_preview
from app.services.mcp_review_plan import clean_preview_path
from app.services.workflow_state import current_page_version
from app.storage import get_storage
from app.utils.geometry import bbox_to_polygon, polygon_to_bbox, validate_polygon
from fastapi import HTTPException, UploadFile
from mcp import types
from PIL import Image, ImageChops, ImageDraw
from sqlalchemy import func, select


def _layout_summary(layout):
    summary = {key: layout[key] for key in ("manual", "auto_font_size", "ocr_geometry_stale", "translation_source_stale", "render_skipped") if key in layout}
    for key, fields in {
        "mask_generation": ("method", "repair_pending", "coverage"),
        "repair_quality": ("status", "reason"),
        "mask_integrity": ("status", "reason"),
    }.items():
        if isinstance(layout.get(key), dict):
            summary[key] = {field: layout[key][field] for field in fields if field in layout[key]}
    generation = layout.get("mask_generation")
    if isinstance(generation, dict):
        for key in ("constraint", "mask_integrity"):
            if isinstance(generation.get(key), dict):
                summary.setdefault("mask_generation", {})[key] = {
                    field: generation[key][field] for field in ("status", "reason", "glyph_safe_retention", "glyph_component_min_retention")
                    if field in generation[key]}
    return summary


def state(image_id, *, include_details=False, region_ids=None, offset=0, limit=20, expected_version=None, run_id=None, details_offset=0):
    with SessionLocal() as db:
        page = require_page(db, image_id)
        version = current_page_version(db, image_id)
        if ((offset or details_offset) and expected_version != version) or (expected_version is not None and expected_version != version):
            raise HTTPException(409, "Page changed or pagination version is missing; restart page_state from offset=0")
        query = select(TextRegion).where(TextRegion.image_id == image_id)
        if region_ids is not None:
            from app.services.mcp_page_edit import resolve_regions
            query = query.where(TextRegion.id.in_([row.id for row in resolve_regions(db, image_id, region_ids)]))
        count = db.scalar(select(func.count()).select_from(query.subquery()))
        if region_ids is not None and count != len(region_ids):
            raise HTTPException(404, "One or more regions do not belong to this page")
        rows = db.scalars(query.order_by(TextRegion.reading_order, TextRegion.created_at, TextRegion.id).offset(offset).limit(limit))
        regions = []
        for row in rows:
            value = {key: getattr(row, key) for key in ("id", "region_key", "bbox", "source_text", "translated_text", "orientation", "reading_order", "region_type")}
            if row.polygon and row.polygon != bbox_to_polygon(row.bbox):
                value["polygon"] = row.polygon
            if row.translated_bbox and row.translated_bbox != row.bbox:
                value["translated_bbox"] = row.translated_bbox
            if row.translated_polygon and row.translated_polygon != (row.polygon or bbox_to_polygon(row.bbox)):
                value["translated_polygon"] = row.translated_polygon
            for key, default in (("rotation", 0), ("locked", False), ("visible", True), ("perspective_warp", False)):
                if getattr(row, key) != default:
                    value[key] = getattr(row, key)
            flags = _layout_summary(row.layout_data or {})
            if flags:
                value["state"] = flags
            if include_details:
                from app.services.mcp_catalog import EDIT_FIELDS
                value["style"] = {key: getattr(row, key) for key in EDIT_FIELDS
                    if key not in value and key not in {"polygon", "translated_polygon", "translated_bbox", "auto_font_size"}}
                value["style"]["auto_font_size"] = (row.layout_data or {}).get("auto_font_size", True)
            regions.append(value)
        result = {"page": {key: getattr(page, key) for key in ("id", "project_id", "filename", "width", "height", "order_index", "status")}, "version": version,
            "expected_versions": {image_id: version}, "regions": regions,
            "detail_level": "full" if include_details else "summary", "matched_region_count": count,
            "next_offset": offset + len(regions) if offset + len(regions) < count else None}
        if include_details:
            from app.services.quality import evaluate_page
            hints = evaluate_page(page, persist=False)
            result["quality_hints"] = [{key: getattr(item, key) for key in ("region_key", "code", "message", "severity")}
                for item in hints[details_offset:details_offset+limit]]
            issues = select(AgentReviewIssue).where(AgentReviewIssue.image_id == image_id)
            if run_id:
                from app.services.mcp_workflows import page_row
                page_row(db, run_id, image_id)
                issues = issues.where(AgentReviewIssue.workflow_run_id == run_id)
            issue_count = db.scalar(select(func.count()).select_from(issues.subquery()))
            result["issues"] = [{"issue_id": item.id, "region_id": item.region_id, "category": item.category,
                "message": item.message, "status": item.status, "stale": item.page_version != version}
                for item in db.scalars(issues.order_by(AgentReviewIssue.created_at, AgentReviewIssue.id).offset(details_offset).limit(limit))]
            result.update(quality_hint_count=len(hints), issue_count=issue_count,
                details_next_offset=details_offset+limit if details_offset+limit < max(issue_count, len(hints)) else None)
        if current_page_version(db, image_id) != version:
            raise HTTPException(409, "Page changed while reading its state; request a fresh page_state")
        db.rollback()
        return result


def preview_region_bounds(region, kind):
    if kind == "comparison":
        source = region.bbox
        target = preview_region_bounds(region, "rendered")
        x, y = min(source[0], target[0]), min(source[1], target[1])
        return [x, y, max(source[0]+source[2], target[0]+target[2])-x,
                max(source[1]+source[3], target[1]+target[3])-y]
    if kind != "rendered":
        return region.bbox
    from app.services.layout.text_frame import aligned_text_frame
    from app.services.layout.text_rotation import effective_text_rotation
    bounds = region.translated_bbox or region.bbox
    polygon = region.translated_polygon or bbox_to_polygon(bounds)
    angle = effective_text_rotation(region)
    if aligned_text_frame(polygon, angle) is not None:
        return polygon_to_bbox(polygon)
    if angle:
        x, y, w, h = bounds
        cx, cy = x+w/2, y+h/2
        cosine, sine = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        polygon = [[cx+(px-cx)*cosine-(py-cy)*sine, cy+(px-cx)*sine+(py-cy)*cosine]
                   for px, py in bbox_to_polygon(bounds)]
    return polygon_to_bbox(polygon)


def preview(args):
    with SessionLocal() as db:
        page = require_page(db, args["image_id"])
        version = current_page_version(db, page.id)
        if args.get("expected_version") is not None and args["expected_version"] != version:
            raise HTTPException(409, "Page changed; refresh page_state and preview")
        kind = args.get("kind", "original")
        if args.get("region_id"):
            from app.services.mcp_page_edit import resolve_regions
            region = resolve_regions(db, page.id, [args["region_id"]])[0]
        else:
            region = None
        if region and region.image_id != page.id:
            raise HTTPException(422, "Region does not belong to page")
        crop = args.get("crop") or (preview_region_bounds(region, kind) if region else [0, 0, page.width, page.height])
        x, y, w, h = crop
        if not all(math.isfinite(v) for v in crop) or w <= 0 or h <= 0:
            raise HTTPException(422, "Crop must be finite [x,y,width,height]")
        box = (max(0, math.floor(x)), max(0, math.floor(y)), min(page.width, math.ceil(x+w)), min(page.height, math.ceil(y+h)))
        if box[2] <= box[0] or box[3] <= box[1]:
            raise HTTPException(422, "Crop is outside image")
        path = {"original": page.original_path, "annotated": page.original_path,
                "clean": clean_preview_path(page) if kind == "clean" else None, "rendered": page.rendered_path}.get(kind)
        if kind == "mask":
            picture = Image.new("RGB", (page.width, page.height), "black")
            from PIL import ImageChops
            for row in ([region] if region else page.regions):
                if row.pixel_mask_path:
                    with Image.open(get_storage().absolute(row.pixel_mask_path)) as mask:
                        picture = ImageChops.lighter(picture, mask.convert("RGB"))
        elif kind != "comparison":
            if not path:
                raise HTTPException(409, f"{kind} is unavailable/stale; run its processing stage first")
            with Image.open(get_storage().absolute(path)) as image:
                picture = image.convert("RGB")
        if kind == "annotated":
            draw = ImageDraw.Draw(picture)
            for row in page.regions:
                if args.get("frame", "source") == "translation":
                    points = row.translated_polygon or bbox_to_polygon(row.translated_bbox or row.bbox)
                    if row.rotation and not row.perspective_warp:
                        from app.services.layout.text_frame import aligned_text_frame
                        if aligned_text_frame(points, row.rotation) is None:
                            bounds = row.translated_bbox or row.bbox
                            cx, cy = bounds[0]+bounds[2]/2, bounds[1]+bounds[3]/2
                            angle = math.radians(row.rotation)
                            points = [[cx+(x-cx)*math.cos(angle)-(y-cy)*math.sin(angle),
                                cy+(x-cx)*math.sin(angle)+(y-cy)*math.cos(angle)] for x, y in bbox_to_polygon(bounds)]
                else:
                    points = row.polygon or bbox_to_polygon(row.bbox)
                draw.line([tuple(p) for p in points + points[:1]], fill="#2563eb", width=max(2, page.width//500))
                draw.text(tuple(points[0]), row.region_key, fill="#ff0000", stroke_width=1, stroke_fill="white")
        if kind == "comparison":
            data, display_metadata = comparison_preview(page, box, args.get("max_size", 2048))
        else:
            picture = picture.crop(box)
            picture.thumbnail((args.get("max_size", 1600),)*2)
            output = io.BytesIO()
            picture.save(output, "PNG", compress_level=3)
            data = output.getvalue()
            while len(data) > 2*1024**2:
                picture = picture.resize((max(1, picture.width*3//4), max(1, picture.height*3//4)))
                output = io.BytesIO()
                picture.save(output, "PNG", compress_level=3)
                data = output.getvalue()
            display_metadata = {"display_size": list(picture.size),
                "original_pixels_per_display_pixel": [(box[2]-box[0])/picture.width, (box[3]-box[1])/picture.height]}
        if current_page_version(db, page.id) != version:
            raise HTTPException(409, "Page changed while preparing the preview; request a fresh preview")
        identifier = str(uuid4())
        asset_path(identifier).parent.mkdir(parents=True, exist_ok=True)
        asset_path(identifier).write_bytes(data)
        metadata = {"image_id": page.id, "version": version, "kind": kind, "frame": args.get("frame", "source"), "original_size": [page.width, page.height],
            "clean_uses_original": kind == "clean" and path == page.original_path,
            "crop": [box[0], box[1], box[2]-box[0], box[3]-box[1]], **display_metadata,
            "download_path": f"/mangaflow/previews/{identifier}", "visual_review_performed": False}
        db.add(MCPAsset(id=identifier, filename="preview.png", size=len(data), evidence=metadata))
        db.commit()
        metadata["preview_id"] = identifier
        return metadata, types.ImageContent(type="image", data=base64.b64encode(data).decode(), mimeType="image/png")


def paint(args):
    from app.api.regions import upload_mask
    with SessionLocal() as db:
        region = require_region(db, args["region_id"])
        page = require_page(db, region.image_id)
        check_page_version(db, page.id)
        points = args["points"]
        if not all(math.isfinite(v) for p in points for v in p):
            raise HTTPException(422, "Mask coordinates must be finite")
        if any(x < 0 or y < 0 or x > page.width or y > page.height for x, y in points):
            raise HTTPException(422, "Mask coordinates must be within original image bounds")
        if args["shape"] == "polygon":
            validate_polygon(points)
        if region.pixel_mask_path:
            with Image.open(get_storage().absolute(region.pixel_mask_path)) as image:
                mask = image.convert("L")
        else:
            mask = Image.new("L", (page.width, page.height), 0)
        if mask.size != (page.width, page.height):
            raise HTTPException(409, "Stored mask dimensions do not match the page")
        previous = mask.copy()
        draw = ImageDraw.Draw(mask)
        value = 255 if args["mode"] == "paint" else 0
        if args["shape"] == "polygon":
            draw.polygon([tuple(p) for p in points], fill=value)
        else:
            draw.line([tuple(p) for p in points], fill=value, width=args.get("width", 10), joint="curve")
        changed_bounds = ImageChops.difference(previous, mask).getbbox()
        if changed_bounds is None:
            check_page_version(db, page.id)
            return {"changed": False, "changed_bounds": None}
        data = io.BytesIO()
        mask.save(data, "PNG")
        data.seek(0)
        upload_mask(region.id, UploadFile(data, filename="mask.png"), None, None, None, db)
        x, y, right, bottom = changed_bounds
        return {"changed": True, "changed_bounds": [x, y, right-x, bottom-y]}
