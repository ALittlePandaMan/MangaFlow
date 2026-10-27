from __future__ import annotations

import json
import zipfile
from io import BytesIO

import cv2
import numpy as np
from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.services.detection.artifacts import (
    BUBBLE_GEOMETRY_KEY,
    bubble_geometry_items,
    load_balloon_mask,
    persist_balloon_mask,
)
from app.services.regions import STALE_CLEAN_PATH_KEY
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


def test_exports_in_the_same_second_never_overwrite_an_earlier_download(client, monkeypatch):
    from datetime import datetime, timezone

    from app.api import projects
    from app.services import exporting

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 18, tzinfo=timezone.utc)

    monkeypatch.setattr(exporting, "datetime", FrozenDatetime)
    project = client.post("/api/projects", json={"name": "simultaneous downloads"}).json()
    client.post(f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", _page_image(), "image/png")})
    original, saved = projects.export_project, []

    def record(*args, **kwargs):
        path = original(*args, **kwargs)
        saved.append((path, path.read_bytes()))
        return path

    monkeypatch.setattr(projects, "export_project", record)
    for formats in (["clean"], ["json"]):
        response = client.post(f"/api/projects/{project['id']}/export", json={"formats": formats})
        assert response.status_code == 200, response.text
    assert saved[0][0] != saved[1][0]
    assert saved[0][0].read_bytes() == saved[0][1]


def test_export_rejects_an_earlier_page_invalidated_while_later_pages_render(client, monkeypatch):
    from app.api import projects

    project = client.post("/api/projects", json={"name": "edit during export"}).json()
    pages = client.post(f"/api/projects/{project['id']}/images", files=[
        ("files", (f"{index}.png", _page_image(), "image/png")) for index in range(2)]).json()
    region = client.post(f"/api/images/{pages[0]['id']}/regions", json={
        "bbox": [10, 10, 100, 100], "translated_text": "before"}).json()
    original, rendered = projects.refresh_export_page, []

    def invalidate_earlier(db, identifier):
        original(db, identifier)
        rendered.append(identifier)
        if len(rendered) == 2:
            with SessionLocal() as other:
                page = other.get(ImagePage, rendered[0])
                page.rendered_path = page.text_layer_path = None
                other.get(TextRegion, region["id"]).translated_text = "new user edit"
                other.commit()

    monkeypatch.setattr(projects, "refresh_export_page", invalidate_earlier)
    response = client.post(f"/api/projects/{project['id']}/export", json={"formats": ["translated"]})
    assert len(rendered) == 2
    assert response.status_code == 409, response.text
    assert client.get(f"/api/images/{pages[0]['id']}").json()["rendered_url"] is None


def test_export_reads_page_paths_and_regions_from_one_snapshot(client, monkeypatch):
    from app.api import projects
    from app.core.database import engine
    from sqlalchemy import event

    project = client.post("/api/projects", json={"name": "consistent export"}).json()
    page = client.post(f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", _page_image(), "image/png")}).json()[0]
    region = client.post(f"/api/images/{page['id']}/regions", json={
        "bbox": [10, 10, 100, 100], "translated_text": "snapshot translation"}).json()
    original, armed, writes = projects.refresh_export_page, [], []

    def render(*args, **kwargs):
        original(*args, **kwargs)
        armed.append(True)

    def concurrent_edit(connection, cursor, statement, parameters, context, executemany):
        if armed and not writes and "FROM text_regions" in statement:
            writes.append(True)
            with SessionLocal() as other:
                current = other.get(ImagePage, page["id"])
                current.rendered_path = current.text_layer_path = None
                other.get(TextRegion, region["id"]).translated_text = "later translation"
                other.commit()

    monkeypatch.setattr(projects, "refresh_export_page", render)
    event.listen(engine, "before_cursor_execute", concurrent_edit)
    try:
        response = client.post(f"/api/projects/{project['id']}/export", json={"formats": ["translated", "json"]})
    finally:
        event.remove(engine, "before_cursor_execute", concurrent_edit)
    assert writes == [True]
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert "translated/1.png" in archive.namelist()
        manifest = json.loads(archive.read("project.json"))
    assert manifest["pages"][0]["regions"][0]["translated_text"] == "snapshot translation"
    assert client.get(f"/api/images/{page['id']}/regions").json()[0]["translated_text"] == "later translation"


def test_project_import_preserves_files_when_commit_acknowledgement_is_lost(client, monkeypatch):
    import pytest
    from app.models import Project
    from app.services.exporting import import_project_archive
    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError

    project = client.post("/api/projects", json={"name": "ambiguous import"}).json()
    client.post(f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", _page_image(), "image/png")})
    exported = client.post(f"/api/projects/{project['id']}/export", json={"formats": ["project"]})
    assert exported.status_code == 200
    with SessionLocal() as db:
        commit = db.commit

        def lost_acknowledgement():
            commit()  # The server committed; the client did not receive its acknowledgement.
            raise OperationalError("COMMIT", {}, OSError("connection lost"), connection_invalidated=True)

        monkeypatch.setattr(db, "commit", lost_acknowledgement)
        with pytest.raises(OperationalError):
            import_project_archive(BytesIO(exported.content), db, get_storage(), maximum_uncompressed_bytes=1024**2)
    with SessionLocal() as db:
        imported = db.scalar(select(Project).where(Project.name == project["name"], Project.id != project["id"]))
        assert imported is not None and len(imported.pages) == 1
        assert get_storage().absolute(imported.pages[0].original_path).is_file()


def test_multi_page_export_renders_without_writer_and_outputs_remain_public(client, monkeypatch):
    from app.services import export_render
    from test_region_edit_transactions import _check_writer_available

    project = client.post("/api/projects", json={"name": "unlocked export"}).json()
    pages = client.post(f"/api/projects/{project['id']}/images", files=[
        ("files", (f"{index}.png", _page_image(), "image/png")) for index in range(2)]).json()
    before = client.get(f"/api/projects/{project['id']}/history").json()
    original, calls = export_render._render, []

    def unlocked(*args, **kwargs):
        _check_writer_available(project["id"])
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(export_render, "_render", unlocked)
    response = client.post(f"/api/projects/{project['id']}/export", json={"formats": ["translated", "text_layer"]})
    assert response.status_code == 200, response.text
    assert calls == [1, 1]
    assert client.get(f"/api/projects/{project['id']}/history").json() == before
    for page in pages:
        current = client.get(f"/api/images/{page['id']}").json()
        assert "/export-" in current["rendered_url"]
        assert client.get(current["rendered_url"]).status_code == 200
        assert client.get(current["text_layer_url"]).status_code == 200


def test_export_late_render_does_not_overwrite_a_newer_region_edit(client, monkeypatch):
    from app.services import export_render

    project = client.post("/api/projects", json={"name": "stale export"}).json()
    page = client.post(f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", _page_image(), "image/png")}).json()[0]
    region = client.post(f"/api/images/{page['id']}/regions", json={
        "bbox": [10, 10, 100, 100], "translated_text": "old translation"}).json()
    original = export_render._render

    def concurrent(*args, **kwargs):
        result = original(*args, **kwargs)
        with SessionLocal() as other:
            other.get(TextRegion, region["id"]).translated_text = "keep newer translation"
            other.commit()
        return result

    monkeypatch.setattr(export_render, "_render", concurrent)
    response = client.post(f"/api/projects/{project['id']}/export", json={"formats": ["translated"]})
    assert response.status_code == 409, response.text
    assert client.get(f"/api/images/{page['id']}").json()["rendered_url"] is None
    assert client.get(f"/api/images/{page['id']}/regions").json()[0]["translated_text"] == "keep newer translation"


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


def test_portable_export_does_not_leak_private_stale_clean_path(client) -> None:
    project = client.post("/api/projects", json={"name": "stale-clean-metadata"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", _page_image(), "image/png")},
    ).json()[0]
    with SessionLocal() as db:
        persisted_page = db.get(ImagePage, page["id"])
        assert persisted_page is not None
        persisted_page.metadata_json = {
            **(persisted_page.metadata_json or {}),
            STALE_CLEAN_PATH_KEY: "projects/private/old-clean.png",
        }
        db.commit()

    exported = client.post(f"/api/projects/{project['id']}/export", json={"formats": ["project"]})
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(BytesIO(exported.content)) as archive:
        manifest = json.loads(archive.read("project.json"))

    assert STALE_CLEAN_PATH_KEY not in manifest["pages"][0]["metadata"]


def test_project_import_discards_untrusted_stale_clean_path(client) -> None:
    project = client.post("/api/projects", json={"name": "malicious-stale-clean"}).json()
    client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", _page_image(), "image/png")},
    )
    exported = client.post(f"/api/projects/{project['id']}/export", json={"formats": ["project"]})
    assert exported.status_code == 200, exported.text

    rewritten = BytesIO()
    with zipfile.ZipFile(BytesIO(exported.content)) as source, zipfile.ZipFile(
        rewritten,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as target:
        for member in source.infolist():
            if member.filename == "project.json":
                continue
            target.writestr(member, source.read(member))
        manifest = json.loads(source.read("project.json"))
        manifest["pages"][0]["metadata"][STALE_CLEAN_PATH_KEY] = (
            "projects/another-project/pages/another-page/clean/clean.png"
        )
        target.writestr("project.json", json.dumps(manifest))
    rewritten.seek(0)

    imported = client.post(
        "/api/projects/import",
        files={"file": ("untrusted.zip", rewritten, "application/zip")},
    )
    assert imported.status_code == 201, imported.text
    imported_page = client.get(f"/api/projects/{imported.json()['id']}/images").json()[0]
    with SessionLocal() as db:
        persisted = db.get(ImagePage, imported_page["id"])
        assert persisted is not None
        assert STALE_CLEAN_PATH_KEY not in (persisted.metadata_json or {})


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
    assert restored['id'] != created_region['id']
    for field in (
        "region_key", "polygon", "bbox", "translated_polygon", "translated_bbox",
        "source_text", "translated_text", "orientation", "font_size", "font_family",
        "font_weight", "text_color", "stroke_color", "stroke_width", "alignment",
        "line_spacing", "character_spacing", "rotation", "perspective_warp", "opacity",
        "locked", "visible",
    ):
        assert restored[field] == created_region[field]
    assert restored["layout_data"]["typography_state"] == created_region["layout_data"]["typography_state"]
    assert restored["layout_data"]["text_rotation"] == created_region["layout_data"]["text_rotation"]


def test_project_archive_round_trips_exact_balloon_constraint_assets(client) -> None:
    project = client.post("/api/projects", json={"name": "balloon-artifact-roundtrip"}).json()
    page = client.post(
        f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", _page_image(), "image/png")},
    ).json()[0]
    region = client.post(
        f"/api/images/{page['id']}/regions",
        json={"bbox": [55, 25, 70, 90], "orientation": "vertical"},
    ).json()
    storage = get_storage()
    compact = np.zeros((100, 80), dtype=np.uint8)
    cv2.ellipse(compact, (40, 50), (35, 46), 0, 0, 360, 255, -1)
    with SessionLocal() as db:
        persisted_page = db.get(ImagePage, page["id"])
        persisted_region = db.get(TextRegion, region["id"])
        assert persisted_page is not None and persisted_region is not None
        entry = persist_balloon_mask(
            storage,
            storage.page_dir(project["id"], page["id"]),
            instance_id="bubble-portable",
            mask=compact,
            origin=(50, 20),
            image_shape=(140, 220),
            confidence=0.97,
        )
        persisted_page.metadata_json = {
            BUBBLE_GEOMETRY_KEY: {
                "schema_version": 1,
                "source": {
                    "path": persisted_page.original_path,
                    "width": persisted_page.width,
                    "height": persisted_page.height,
                },
                "instances": {"bubble-portable": entry},
            }
        }
        persisted_region.bubble_id = "bubble-portable"
        persisted_region.layout_data = {
            "detection": {
                "balloon_assignment": {
                    "status": "assigned",
                    "bubble_id": "bubble-portable",
                    "balloon_confidence": 0.97,
                    "core_coverage": 1.0,
                },
                "balloon_constraint": {
                    "schema_version": 1,
                    "status": "available",
                    "instance_id": "bubble-portable",
                },
            }
        }
        db.commit()

    exported = client.post(f"/api/projects/{project['id']}/export", json={"formats": ["project"]})
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(BytesIO(exported.content)) as archive:
        manifest = json.loads(archive.read("project.json"))
        member = manifest["pages"][0]["assets"]["bubble_constraints"]["bubble-portable"]
        assert member in archive.namelist()

    imported = client.post(
        "/api/projects/import",
        files={"file": ("balloons.zip", BytesIO(exported.content), "application/zip")},
    )
    assert imported.status_code == 201, imported.text
    imported_project = imported.json()
    imported_page = client.get(f"/api/projects/{imported_project['id']}/images").json()[0]
    with SessionLocal() as db:
        restored_page = db.get(ImagePage, imported_page["id"])
        assert restored_page is not None
        restored_items = bubble_geometry_items(restored_page.metadata_json)
        restored_mask = load_balloon_mask(
            storage,
            storage.page_dir(imported_project["id"], imported_page["id"]),
            restored_items["bubble-portable"],
            image_shape=(140, 220),
        )
    expected = np.zeros((140, 220), dtype=np.uint8)
    expected[20:120, 50:130] = compact
    assert np.array_equal(restored_mask, expected)
