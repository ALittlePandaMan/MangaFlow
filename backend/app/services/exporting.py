from __future__ import annotations

import json
import zipfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from app.models import Project
from app.storage import StorageManager


def export_project(project: Project, formats: Iterable[str], storage: StorageManager) -> Path:
    requested = set(formats)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = storage.root / "exports" / f"{project.id}-{timestamp}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "project": {
            "id": project.id,
            "name": project.name,
            "source_language": project.source_language,
            "target_language": project.target_language,
            "translation_context": project.translation_context,
            "settings": project.settings,
        },
        "pages": [],
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for page in sorted(project.pages, key=lambda item: item.order_index):
            page_data = {
                "id": page.id,
                "filename": page.filename,
                "width": page.width,
                "height": page.height,
                "order_index": page.order_index,
                "status": page.status,
                "regions": [],
            }
            for region in page.regions:
                region_data = {
                    "region_id": region.region_key,
                    "id": region.id,
                    "bbox": region.bbox,
                    "polygon": region.polygon,
                    "translated_bbox": region.translated_bbox or region.bbox,
                    "translated_polygon": region.translated_polygon or region.polygon,
                    "orientation": region.orientation,
                    "reading_order": region.reading_order,
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
                    "layout": region.layout_data,
                    "mask_path": region.pixel_mask_path or "",
                    "locked": region.locked,
                    "visible": region.visible,
                }
                page_data["regions"].append(region_data)
                if "masks" in requested and region.pixel_mask_path:
                    _write_file(
                        archive,
                        storage,
                        region.pixel_mask_path,
                        f"masks/{page.order_index:04d}-{region.region_key}.png",
                    )
            manifest["pages"].append(page_data)
            prefix = f"{page.order_index:04d}-{Path(page.filename).stem}.png"
            if "translated" in requested and page.rendered_path:
                _write_file(archive, storage, page.rendered_path, f"translated/{prefix}")
            if "clean" in requested and page.clean_path:
                _write_file(archive, storage, page.clean_path, f"clean/{prefix}")
            if "text_layer" in requested and page.text_layer_path:
                _write_file(archive, storage, page.text_layer_path, f"text_layers/{prefix}")
            if "project" in requested:
                _write_file(archive, storage, page.original_path, f"project/original/{Path(page.filename).name}")
        if "json" in requested or "project" in requested:
            archive.writestr("project.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return output


def _write_file(archive: zipfile.ZipFile, storage: StorageManager, relative: str, name: str) -> None:
    path = storage.absolute(relative)
    if path.exists():
        archive.write(path, name)
