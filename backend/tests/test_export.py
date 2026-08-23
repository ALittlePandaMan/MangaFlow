from __future__ import annotations

import json
import zipfile
from io import BytesIO

from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.services.rendering.pillow_renderer import RENDER_OUTPUT_VERSION
from app.storage import get_storage
from PIL import Image


def _page_image() -> BytesIO:
    output = BytesIO()
    Image.new("RGB", (220, 140), "#eeeeee").save(output, "PNG")
    output.seek(0)
    return output


def _zip_image(payload: bytes, directory: str) -> Image.Image:
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        name = next(item for item in archive.namelist() if item.startswith(f"{directory}/"))
        return Image.open(BytesIO(archive.read(name))).convert("RGBA")


def test_export_rebuilds_outputs_after_perspective_setting_invalidates_cache(client) -> None:
    project = client.post("/api/projects", json={"name": "perspective-export"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", _page_image(), "image/png")},
    ).json()[0]
    region = client.post(
        f"/api/images/{page['id']}/regions",
        json={
            "bbox": [30, 35, 150, 60],
            "polygon": [[30, 35], [180, 35], [180, 95], [30, 95]],
            "translated_bbox": [30, 25, 150, 80],
            "translated_polygon": [[30, 35], [180, 20], [170, 105], [35, 90]],
            "translated_text": "Perspective text",
            "orientation": "horizontal",
            "font_size": 24,
            "perspective_warp": False,
        },
    ).json()

    initial_export = client.post(
        f"/api/projects/{project['id']}/export",
        json={"formats": ["translated", "text_layer", "json"]},
    )
    assert initial_export.status_code == 200, initial_export.text
    _zip_image(initial_export.content, "translated")
    rendered_page = client.get(f"/api/images/{page['id']}").json()
    assert rendered_page["rendered_url"]
    assert rendered_page["text_layer_url"]

    updated = client.patch(f"/api/regions/{region['id']}", json={"perspective_warp": True})
    assert updated.status_code == 200, updated.text
    assert updated.json()["perspective_warp"] is True
    invalidated_page = client.get(f"/api/images/{page['id']}").json()
    assert invalidated_page["rendered_url"] is None
    assert invalidated_page["text_layer_url"] is None

    rebuilt_export = client.post(
        f"/api/projects/{project['id']}/export",
        json={"formats": ["translated", "text_layer", "json"]},
    )
    assert rebuilt_export.status_code == 200, rebuilt_export.text
    with zipfile.ZipFile(BytesIO(rebuilt_export.content)) as archive:
        names = archive.namelist()
        assert any(name.startswith("translated/") for name in names)
        assert any(name.startswith("text_layers/") for name in names)
        manifest = json.loads(archive.read("project.json"))
        assert manifest["pages"][0]["regions"][0]["perspective_warp"] is True
    rebuilt_page = client.get(f"/api/images/{page['id']}").json()
    assert rebuilt_page["rendered_url"]
    assert rebuilt_page["text_layer_url"]
    rebuilt_region = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert rebuilt_region["layout_data"]["perspective_warp_applied"] is True


def test_export_rebuilds_when_cached_output_file_is_missing(client) -> None:
    project = client.post("/api/projects", json={"name": "missing-export-file"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", _page_image(), "image/png")},
    ).json()[0]
    first_export = client.post(
        f"/api/projects/{project['id']}/export",
        json={"formats": ["translated", "text_layer"]},
    )
    assert first_export.status_code == 200, first_export.text

    with SessionLocal() as db:
        persisted_page = db.get(ImagePage, page["id"])
        assert persisted_page is not None and persisted_page.rendered_path
        get_storage().absolute(persisted_page.rendered_path).unlink()

    rebuilt = client.post(
        f"/api/projects/{project['id']}/export",
        json={"formats": ["translated", "text_layer"]},
    )
    assert rebuilt.status_code == 200, rebuilt.text
    with zipfile.ZipFile(BytesIO(rebuilt.content)) as archive:
        assert any(name.startswith("translated/") for name in archive.namelist())
        assert any(name.startswith("text_layers/") for name in archive.namelist())


def test_export_rebuilds_stale_renderer_output(client) -> None:
    project = client.post("/api/projects", json={"name": "stale-renderer-output"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", _page_image(), "image/png")},
    ).json()[0]
    region = client.post(
        f"/api/images/{page['id']}/regions",
        json={
            "bbox": [30, 20, 50, 100],
            "polygon": [[30, 20], [80, 20], [80, 120], [30, 120]],
            "translated_text": "？！",
            "orientation": "vertical",
        },
    ).json()
    first_export = client.post(
        f"/api/projects/{project['id']}/export",
        json={"formats": ["translated"]},
    )
    assert first_export.status_code == 200, first_export.text

    with SessionLocal() as db:
        persisted_region = db.get(TextRegion, region["id"])
        assert persisted_region is not None
        persisted_region.layout_data = {
            **(persisted_region.layout_data or {}),
            "render_output_version": RENDER_OUTPUT_VERSION - 1,
        }
        db.commit()

    rebuilt = client.post(
        f"/api/projects/{project['id']}/export",
        json={"formats": ["translated"]},
    )
    assert rebuilt.status_code == 200, rebuilt.text
    rebuilt_region = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert rebuilt_region["layout_data"]["render_output_version"] == RENDER_OUTPUT_VERSION


def test_clean_export_falls_back_to_original_for_unrepaired_page(client) -> None:
    project = client.post("/api/projects", json={"name": "clean-fallback"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("unrepaired.png", _page_image(), "image/png")},
    ).json()[0]
    assert page["clean_url"] is None

    exported = client.post(f"/api/projects/{project['id']}/export", json={"formats": ["clean"]})
    assert exported.status_code == 200, exported.text
    exported_image = _zip_image(exported.content, "clean")
    assert exported_image.size == (220, 140)
    assert exported_image.getpixel((0, 0))[:3] == (238, 238, 238)


def test_exported_page_names_use_dynamic_zero_padding(client) -> None:
    project = client.post("/api/projects", json={"name": "numbered-export"}).json()
    files = [
        ("files", (f"source-{number}.png", _page_image(), "image/png"))
        for number in range(1, 11)
    ]
    uploaded = client.post(f"/api/projects/{project['id']}/images", files=files)
    assert uploaded.status_code == 201, uploaded.text

    exported = client.post(f"/api/projects/{project['id']}/export", json={"formats": ["clean"]})
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(BytesIO(exported.content)) as archive:
        clean_names = sorted(name for name in archive.namelist() if name.startswith("clean/"))
    assert clean_names == [f"clean/{number:02d}.png" for number in range(1, 11)]


def test_source_project_archive_can_be_imported_for_further_editing(client) -> None:
    project = client.post(
        "/api/projects",
        json={
            "name": "portable-project",
            "description": "round trip",
            "translation_context": {"hero": "阿明"},
        },
    ).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", _page_image(), "image/png")},
    ).json()[0]
    created_region = client.post(
        f"/api/images/{page['id']}/regions",
        json={
            "region_key": "R008",
            "bbox": [30, 35, 150, 60],
            "polygon": [[30, 35], [180, 30], [176, 98], [34, 94]],
            "translated_bbox": [25, 25, 165, 80],
            "translated_polygon": [[25, 35], [190, 22], [182, 105], [30, 95]],
            "source_text": "原文",
            "translated_text": "译文",
            "orientation": "horizontal",
            "font_size": 31,
            "font_family": "Noto Sans CJK SC",
            "font_weight": 700,
            "text_color": "#123456",
            "stroke_color": "#fedcba",
            "stroke_width": 2,
            "alignment": "right",
            "line_spacing": 1.25,
            "character_spacing": 1.5,
            "rotation": 7,
            "perspective_warp": True,
            "opacity": 0.8,
            "locked": True,
            "visible": False,
        },
    ).json()

    exported = client.post(f"/api/projects/{project['id']}/export", json={"formats": ["project"]})
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(BytesIO(exported.content)) as archive:
        manifest = json.loads(archive.read("project.json"))
        assert manifest["schema_version"] == 2
        assert manifest["project"]["description"] == "round trip"
        assert manifest["pages"][0]["assets"]["original"] in archive.namelist()

    imported = client.post(
        "/api/projects/import",
        files={"file": ("portable-project.zip", BytesIO(exported.content), "application/zip")},
    )
    assert imported.status_code == 201, imported.text
    imported_project = imported.json()
    assert imported_project["id"] != project["id"]
    assert imported_project["name"] == project["name"]
    assert imported_project["description"] == "round trip"
    assert imported_project["translation_context"] == {"hero": "阿明"}

    imported_pages = client.get(f"/api/projects/{imported_project['id']}/images").json()
    assert len(imported_pages) == 1
    assert imported_pages[0]["original_url"]
    imported_regions = client.get(f"/api/images/{imported_pages[0]['id']}/regions").json()
    assert len(imported_regions) == 1
    restored = imported_regions[0]
    for field in (
        "region_key", "polygon", "bbox", "translated_polygon", "translated_bbox",
        "source_text", "translated_text", "orientation", "font_size", "font_family",
        "font_weight", "text_color", "stroke_color", "stroke_width", "alignment",
        "line_spacing", "character_spacing", "rotation", "perspective_warp", "opacity",
        "locked", "visible",
    ):
        assert restored[field] == created_region[field]
