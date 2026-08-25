from __future__ import annotations

import json
import uuid
import zipfile
from collections.abc import Iterable
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

from app.models import ImagePage, Project, TextRegion
from app.schemas.domain import RegionCreate
from app.services.detection.artifacts import BUBBLE_GEOMETRY_KEY, bubble_geometry_items
from app.services.regions import STALE_CLEAN_PATH_KEY
from app.storage import StorageError, StorageManager
from app.storage.files import SAFE_FILENAME
from sqlalchemy.orm import Session

PROJECT_ARCHIVE_SCHEMA_VERSION = 2


def export_project(project: Project, formats: Iterable[str], storage: StorageManager) -> Path:
    requested = set(formats)
    portable_project = "project" in requested
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = storage.root / "exports" / f"{project.id}-{timestamp}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": PROJECT_ARCHIVE_SCHEMA_VERSION,
        "project": {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "source_language": project.source_language,
            "target_language": project.target_language,
            "translation_context": project.translation_context,
            "settings": project.settings,
        },
        "fonts": [],
        "pages": [],
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if portable_project:
            _export_project_fonts(archive, project, manifest, storage)
        ordered_pages = sorted(project.pages, key=lambda item: item.order_index)
        page_number_width = max(1, len(str(len(ordered_pages))))
        for page_number, page in enumerate(ordered_pages, start=1):
            export_stem = f"{page_number:0{page_number_width}d}"
            project_page_root = f"project/pages/{page.order_index:04d}-{page.id}"
            portable_metadata = deepcopy(page.metadata_json or {})
            portable_metadata.pop(STALE_CLEAN_PATH_KEY, None)
            page_data: dict[str, Any] = {
                "id": page.id,
                "filename": page.filename,
                "width": page.width,
                "height": page.height,
                "order_index": page.order_index,
                "status": page.status,
                "current_stage": page.current_stage,
                "error_message": page.error_message,
                "metadata": portable_metadata,
                "assets": {},
                "regions": [],
            }
            if portable_project:
                original_name = f"{project_page_root}/original/{Path(page.filename).name}"
                if _write_file(archive, storage, page.original_path, original_name):
                    page_data["assets"]["original"] = original_name
                for field, relative_path, folder, fallback in (
                    ("clean", page.clean_path, "clean", "clean.png"),
                    ("translated", page.rendered_path, "rendered", "translated.png"),
                    ("text_layer", page.text_layer_path, "layers", "text-layer.png"),
                ):
                    asset_name = f"{project_page_root}/{folder}/{fallback}"
                    if _write_file(archive, storage, relative_path, asset_name):
                        page_data["assets"][field] = asset_name
                bubble_assets: dict[str, str] = {}
                for instance_id, entry in bubble_geometry_items(page.metadata_json).items():
                    member = f"{project_page_root}/bubbles/{_safe_filename(instance_id, 'bubble')}.png"
                    if _write_file(archive, storage, entry.get("path"), member):
                        bubble_assets[instance_id] = member
                if bubble_assets:
                    page_data["assets"]["bubble_constraints"] = bubble_assets
            for region in page.regions:
                region_data: dict[str, Any] = {
                    "region_id": region.region_key,
                    "id": region.id,
                    "bbox": region.bbox,
                    "polygon": region.polygon,
                    "translated_bbox": region.translated_bbox or region.bbox,
                    "translated_polygon": region.translated_polygon or region.polygon,
                    "perspective_warp": region.perspective_warp,
                    "orientation": region.orientation,
                    "reading_order": region.reading_order,
                    "panel_id": region.panel_id,
                    "bubble_id": region.bubble_id,
                    "region_type": region.region_type,
                    "source_text": region.source_text,
                    "translated_text": region.translated_text,
                    "confidence": region.confidence,
                    "font": {
                        "family": region.font_family,
                        "size": region.font_size,
                        "weight": region.font_weight,
                        "color": region.text_color,
                        "stroke_color": region.stroke_color,
                        "stroke_width": region.stroke_width,
                    },
                    "alignment": region.alignment,
                    "line_spacing": region.line_spacing,
                    "character_spacing": region.character_spacing,
                    "rotation": region.rotation,
                    "opacity": region.opacity,
                    "layout": region.layout_data,
                    "locked": region.locked,
                    "visible": region.visible,
                    "needs_review": region.needs_review,
                    "review_reasons": region.review_reasons,
                    "layout_warning": region.layout_warning,
                    "assets": {},
                }
                if portable_project:
                    region_root = f"{project_page_root}/regions/{region.region_key}"
                    mask_name = f"{region_root}/mask.png"
                    if _write_file(archive, storage, region.pixel_mask_path, mask_name):
                        region_data["assets"]["mask"] = mask_name
                    inpainted_name = f"{region_root}/inpainted.png"
                    if _write_file(archive, storage, region.inpainted_path, inpainted_name):
                        region_data["assets"]["inpainted"] = inpainted_name
                page_data["regions"].append(region_data)
                if "masks" in requested and region.pixel_mask_path:
                    _write_file(
                        archive,
                        storage,
                        region.pixel_mask_path,
                        f"masks/{export_stem}-{region.region_key}.png",
                    )
            manifest["pages"].append(page_data)
            prefix = f"{export_stem}.png"
            if "translated" in requested and page.rendered_path:
                _write_file(archive, storage, page.rendered_path, f"translated/{prefix}")
            if "clean" in requested:
                clean_source = page.clean_path if page.clean_path and storage.absolute(page.clean_path).is_file() else page.original_path
                clean_suffix = Path(clean_source).suffix.lower() or ".png"
                clean_name = f"{export_stem}{clean_suffix}"
                _write_file(archive, storage, clean_source, f"clean/{clean_name}")
            if "text_layer" in requested and page.text_layer_path:
                _write_file(archive, storage, page.text_layer_path, f"text_layers/{prefix}")
        if "json" in requested or portable_project:
            archive.writestr("project.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return output


def import_project_archive(
    stream: BinaryIO,
    db: Session,
    storage: StorageManager,
    *,
    maximum_uncompressed_bytes: int,
) -> Project:
    project: Project | None = None
    try:
        with zipfile.ZipFile(stream) as archive:
            _validate_archive(archive, maximum_uncompressed_bytes)
            try:
                manifest = json.loads(archive.read("project.json"))
            except KeyError as exc:
                raise StorageError("项目包缺少 project.json") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StorageError("项目包中的 project.json 无效") from exc
            if not isinstance(manifest, dict) or manifest.get("schema_version") not in {1, PROJECT_ARCHIVE_SCHEMA_VERSION}:
                raise StorageError("不支持的 MangaFlow 项目包版本")
            project_data = manifest.get("project")
            pages_data = manifest.get("pages")
            if not isinstance(project_data, dict) or not isinstance(pages_data, list):
                raise StorageError("项目包清单结构无效")
            project = Project(
                id=str(uuid.uuid4()),
                name=_bounded_text(project_data.get("name"), "导入项目", 200),
                description=_bounded_text(project_data.get("description"), "", 2000),
                source_language=_bounded_text(project_data.get("source_language"), "ja", 32),
                target_language=_bounded_text(project_data.get("target_language"), "zh-CN", 32),
                translation_context=_dictionary(project_data.get("translation_context")),
                settings=_dictionary(project_data.get("settings")),
            )
            db.add(project)
            db.flush()
            _import_project_fonts(archive, manifest, project, storage)
            for fallback_order, page_data in enumerate(pages_data):
                if not isinstance(page_data, dict):
                    raise StorageError("项目包包含无效的页面数据")
                _import_page(archive, project, page_data, fallback_order, db, storage)
            db.commit()
            db.refresh(project)
            return project
    except StorageError:
        db.rollback()
        if project is not None:
            storage.remove_project(project.id)
        raise
    except (zipfile.BadZipFile, OSError, ValueError, TypeError) as exc:
        db.rollback()
        if project is not None:
            storage.remove_project(project.id)
        raise StorageError(f"无法导入项目包：{exc}") from exc
    except Exception:
        db.rollback()
        if project is not None:
            storage.remove_project(project.id)
        raise


def _import_page(
    archive: zipfile.ZipFile,
    project: Project,
    page_data: dict[str, Any],
    fallback_order: int,
    db: Session,
    storage: StorageManager,
) -> None:
    page_id = str(uuid.uuid4())
    filename = _safe_filename(page_data.get("filename"), f"page-{fallback_order + 1}.png")
    assets = _dictionary(page_data.get("assets"))
    original_member = assets.get("original") or f"project/original/{filename}"
    original_payload = _read_member(archive, original_member, "页面原图")
    original_path, width, height = storage.save_upload(project.id, page_id, filename, BytesIO(original_payload))
    metadata = _dictionary(page_data.get("metadata"))
    # This runtime-only pointer is never portable. Trusting an imported value
    # could make incremental repair read another page's clean composite.
    metadata.pop(STALE_CLEAN_PATH_KEY, None)
    _restore_bubble_geometry(
        archive,
        assets.get("bubble_constraints"),
        metadata,
        storage,
        project.id,
        page_id,
        original_path,
        width,
        height,
    )
    page = ImagePage(
        id=page_id,
        project=project,
        filename=filename,
        original_path=original_path,
        width=width,
        height=height,
        order_index=int(page_data.get("order_index", fallback_order)),
        status=_bounded_text(page_data.get("status"), "UPLOADED", 32),
        current_stage=_optional_text(page_data.get("current_stage"), 32),
        error_message=_optional_text(page_data.get("error_message"), 4000),
        metadata_json=metadata,
    )
    page.clean_path = _restore_page_asset(archive, assets.get("clean"), storage, project.id, page_id, "clean", "clean.png")
    page.rendered_path = _restore_page_asset(archive, assets.get("translated"), storage, project.id, page_id, "rendered", "translated.png")
    page.text_layer_path = _restore_page_asset(archive, assets.get("text_layer"), storage, project.id, page_id, "layers", "text-layer.png")
    db.add(page)
    db.flush()
    used_region_keys: set[str] = set()
    regions_data = page_data.get("regions", [])
    if not isinstance(regions_data, list):
        raise StorageError("项目包包含无效的文本区域数据")
    archive_names = set(archive.namelist())
    for fallback_reading_order, region_data in enumerate(regions_data):
        if not isinstance(region_data, dict):
            raise StorageError("项目包包含无效的文本区域")
        region_key = _unique_region_key(region_data.get("region_id"), used_region_keys, fallback_reading_order)
        used_region_keys.add(region_key)
        font = _dictionary(region_data.get("font"))
        validated = RegionCreate.model_validate({
            "region_key": region_key,
            "polygon": region_data.get("polygon") or [],
            "bbox": region_data.get("bbox") or [],
            "translated_polygon": region_data.get("translated_polygon") or region_data.get("polygon") or [],
            "translated_bbox": region_data.get("translated_bbox") or region_data.get("bbox") or [],
            "source_text": str(region_data.get("source_text") or ""),
            "translated_text": str(region_data.get("translated_text") or ""),
            "confidence": region_data.get("confidence", 0),
            "orientation": region_data.get("orientation", "vertical"),
            "reading_order": region_data.get("reading_order", fallback_reading_order),
            "panel_id": region_data.get("panel_id"),
            "bubble_id": region_data.get("bubble_id"),
            "region_type": region_data.get("region_type", "background_complex"),
            "font_size": font.get("size", 28),
            "font_family": font.get("family", "Noto Sans CJK SC"),
            "font_weight": font.get("weight", 400),
            "text_color": font.get("color", "#111111"),
            "stroke_color": font.get("stroke_color", "#ffffff"),
            "stroke_width": font.get("stroke_width", 0),
            "alignment": region_data.get("alignment", "center"),
            "line_spacing": region_data.get("line_spacing", 1.15),
            "character_spacing": region_data.get("character_spacing", 0),
            "rotation": region_data.get("rotation", 0),
            "perspective_warp": bool(region_data.get("perspective_warp", False)),
            "opacity": region_data.get("opacity", 1),
            "locked": bool(region_data.get("locked", False)),
            "visible": bool(region_data.get("visible", True)),
        })
        values = validated.model_dump(mode="json", exclude={"region_key"})
        region = TextRegion(id=str(uuid.uuid4()), image_id=page.id, region_key=region_key, **values)
        region.layout_data = _dictionary(region_data.get("layout"))
        region.needs_review = bool(region_data.get("needs_review", False))
        reasons = region_data.get("review_reasons", [])
        region.review_reasons = [str(item) for item in reasons] if isinstance(reasons, list) else []
        region.layout_warning = bool(region_data.get("layout_warning", False))
        region_assets = _dictionary(region_data.get("assets"))
        legacy_mask = f"masks/{int(page_data.get('order_index', fallback_order)):04d}-{region_key}.png"
        mask_member = region_assets.get("mask") or (legacy_mask if legacy_mask in archive_names else None)
        region.pixel_mask_path = _restore_region_asset(archive, mask_member, storage, project.id, page.id, region.id, "mask.png")
        region.inpainted_path = _restore_region_asset(archive, region_assets.get("inpainted"), storage, project.id, page.id, region.id, "inpainted.png")
        db.add(region)


def _export_project_fonts(
    archive: zipfile.ZipFile,
    project: Project,
    manifest: dict[str, Any],
    storage: StorageManager,
) -> None:
    fonts = (project.settings or {}).get("fonts", [])
    if not isinstance(fonts, list):
        return
    for index, item in enumerate(fonts):
        if not isinstance(item, dict) or not item.get("path"):
            continue
        filename = _safe_filename(item.get("filename"), f"font-{index + 1}.otf")
        member = f"project/fonts/{index:03d}-{filename}"
        if _write_file(archive, storage, str(item["path"]), member):
            manifest["fonts"].append({"name": item.get("name") or Path(filename).stem, "filename": filename, "asset": member})


def _import_project_fonts(
    archive: zipfile.ZipFile,
    manifest: dict[str, Any],
    project: Project,
    storage: StorageManager,
) -> None:
    fonts = manifest.get("fonts", [])
    if not isinstance(fonts, list):
        fonts = []
    restored: list[dict[str, str]] = []
    directory = storage.project_dir(project.id) / "fonts"
    directory.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(fonts):
        if not isinstance(item, dict) or not item.get("asset"):
            continue
        filename = _safe_filename(item.get("filename"), f"font-{index + 1}.otf")
        payload = _read_member(archive, item["asset"], "项目字体")
        destination = directory / filename
        destination.write_bytes(payload)
        restored.append({
            "name": _bounded_text(item.get("name"), Path(filename).stem, 200),
            "filename": filename,
            "path": storage.relative(destination),
        })
    settings = dict(project.settings or {})
    settings["fonts"] = restored
    project.settings = settings


def _restore_page_asset(
    archive: zipfile.ZipFile,
    member: Any,
    storage: StorageManager,
    project_id: str,
    page_id: str,
    folder: str,
    filename: str,
) -> str | None:
    if not isinstance(member, str) or member not in archive.namelist():
        return None
    destination = storage.page_dir(project_id, page_id) / folder / filename
    destination.write_bytes(archive.read(member))
    return storage.relative(destination)


def _restore_bubble_geometry(
    archive: zipfile.ZipFile,
    asset_value: Any,
    metadata: dict[str, Any],
    storage: StorageManager,
    project_id: str,
    page_id: str,
    original_path: str,
    width: int,
    height: int,
) -> None:
    manifest = metadata.get(BUBBLE_GEOMETRY_KEY)
    if not isinstance(manifest, dict):
        return
    items = manifest.get("instances")
    assets = _dictionary(asset_value)
    if not isinstance(items, dict) or not assets:
        metadata.pop(BUBBLE_GEOMETRY_KEY, None)
        return
    restored: dict[str, dict[str, Any]] = {}
    directory = storage.page_dir(project_id, page_id) / "bubbles"
    for instance_id, entry_value in items.items():
        if not isinstance(instance_id, str) or not isinstance(entry_value, dict):
            continue
        member = assets.get(instance_id)
        if not isinstance(member, str) or member not in archive.namelist():
            continue
        destination = directory / f"{_safe_filename(instance_id, 'bubble')}.png"
        destination.write_bytes(archive.read(member))
        entry = dict(entry_value)
        entry["path"] = storage.relative(destination)
        restored[instance_id] = entry
    if not restored:
        metadata.pop(BUBBLE_GEOMETRY_KEY, None)
        return
    metadata[BUBBLE_GEOMETRY_KEY] = {
        **manifest,
        "source": {"path": original_path, "width": width, "height": height},
        "instances": restored,
    }


def _restore_region_asset(
    archive: zipfile.ZipFile,
    member: Any,
    storage: StorageManager,
    project_id: str,
    page_id: str,
    region_id: str,
    filename: str,
) -> str | None:
    if not isinstance(member, str) or member not in archive.namelist():
        return None
    destination = storage.page_dir(project_id, page_id) / "masks" / f"{region_id}-{filename}"
    destination.write_bytes(archive.read(member))
    return storage.relative(destination)


def _validate_archive(archive: zipfile.ZipFile, maximum_uncompressed_bytes: int) -> None:
    files = [item for item in archive.infolist() if not item.is_dir()]
    if not files or len(files) > 20_000:
        raise StorageError("项目包为空或文件数量过多")
    total = sum(item.file_size for item in files)
    if total > maximum_uncompressed_bytes:
        raise StorageError("项目包解压后的体积超过限制")
    for item in files:
        path = Path(item.filename)
        if path.is_absolute() or ".." in path.parts:
            raise StorageError("项目包包含不安全的文件路径")


def _read_member(archive: zipfile.ZipFile, member: Any, label: str) -> bytes:
    if not isinstance(member, str):
        raise StorageError(f"项目包缺少{label}")
    try:
        return archive.read(member)
    except KeyError as exc:
        raise StorageError(f"项目包缺少{label}：{member}") from exc


def _write_file(
    archive: zipfile.ZipFile,
    storage: StorageManager,
    relative: str | None,
    name: str,
) -> bool:
    if not relative:
        return False
    path = storage.absolute(relative)
    if not path.is_file():
        return False
    archive.write(path, name)
    return True


def _safe_filename(value: Any, fallback: str) -> str:
    name = SAFE_FILENAME.sub("_", Path(str(value or fallback)).name).strip("._")
    return name or fallback


def _bounded_text(value: Any, fallback: str, maximum: int) -> str:
    text = str(value if value is not None else fallback).strip()
    return (text or fallback)[:maximum]


def _optional_text(value: Any, maximum: int) -> str | None:
    if value is None:
        return None
    return str(value)[:maximum]


def _dictionary(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _unique_region_key(value: Any, used: set[str], index: int) -> str:
    base = _bounded_text(value, f"R{index + 1:03d}", 24)
    if base not in used:
        return base
    suffix = 2
    while True:
        candidate = f"{base[:20]}-{suffix}"
        if candidate not in used:
            return candidate
        suffix += 1
