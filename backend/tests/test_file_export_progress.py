from copy import deepcopy
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.services.export_archive import ExportArchive
from app.services.file_export import current_render
from app.services.rendering.pillow_renderer import RENDER_OUTPUT_VERSION, PillowRenderer
from app.services.workflow_state import record
from app.storage import get_storage
from PIL import Image
from test_file_jobs import wait_job


def create_page(client, text="Hello"):
    project_id = client.post("/api/projects", json={"name": "Export progress"}).json()["id"]
    stream = BytesIO()
    Image.new("RGB", (240, 180), "white").save(stream, "PNG")
    page_id = client.post(f"/api/projects/{project_id}/images", files={"files": ("page.png", stream.getvalue())}).json()[0]["id"]
    region_id = client.post(f"/api/images/{page_id}/regions", json={
        "bbox": [20, 20, 180, 100], "polygon": [[20, 20], [200, 20], [200, 120], [20, 120]],
        "orientation": "horizontal", "translated_text": text,
    }).json()["id"]
    return project_id, page_id, region_id


def rendered_state(client):
    project_id, page_id, region_id = create_page(client)
    response = client.post(f"/api/projects/{project_id}/export", json={"formats": ["translated"]})
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        state = {"page": record(db.get(ImagePage, page_id)), "regions": {region_id: record(db.get(TextRegion, region_id))}}
    return project_id, state


@pytest.mark.parametrize("kind", ["translated", "cbz"])
def test_cached_translated_exports_preserve_exact_pixels_without_rendering_or_font_preparation(client, monkeypatch, kind):
    from app.services.layout import FontResolver

    project_id, state = rendered_state(client)
    expected = get_storage().absolute(state["page"]["rendered_path"]).read_bytes()
    assert current_render(state)
    def unexpected(*args, **kwargs):
        raise AssertionError("A current translated PNG must not be rasterized or resolve fonts again")
    monkeypatch.setattr(PillowRenderer, "render", unexpected)
    monkeypatch.setattr(FontResolver, "prepare", unexpected)
    job = client.post(f"/api/projects/{project_id}/exports", json={"kind": kind}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "COMPLETED", done
    with ZipFile(BytesIO(client.get(done["download_url"]).content)) as archive:
        assert archive.read("0001.png") == expected
        assert archive.getinfo("0001.png").compress_type == ZIP_STORED


@pytest.mark.parametrize("change", ["text", "size", "visibility", "version", "removed", "added"])
def test_render_reuse_requires_the_same_visual_inputs_region_set_and_renderer_version(client, change):
    _project_id, state = rendered_state(client)
    assert current_render(state)
    state = deepcopy(state)
    row = next(iter(state["regions"].values()))
    if change == "text":
        row["translated_text"] = "New text"
    elif change == "size":
        row["font_size"] += 1
    elif change == "visibility":
        row["visible"] = False
    elif change == "version":
        row["layout_data"]["render_output_version"] = RENDER_OUTPUT_VERSION - 1
    elif change == "removed":
        state["regions"] = {}
    else:
        state["regions"]["new"] = {**row, "id": "new"}
    assert not current_render(state)


def test_single_page_export_reports_real_preparation_region_and_packaging_progress_before_completion(client, monkeypatch):
    from app.services.file_jobs import FileJobWorker

    project_id, _page_id, _region_id = create_page(client)
    events = []
    original = FileJobWorker.progress
    def observe(self, identifier, value, message="", detail=None):
        events.append((value, detail))
        return original(self, identifier, value, message, detail)
    monkeypatch.setattr(FileJobWorker, "progress", observe)
    job = client.post(f"/api/projects/{project_id}/exports", json={"kind": "translated"}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "COMPLETED", done
    assert {detail["stage"] for _, detail in events if detail} >= {"snapshot", "fonts", "rendering", "packaging"}
    values = [round(value, 9) for value, _ in events]
    assert values == sorted(values)
    assert values[0] > 0
    assert all(value < 1 for value in values)
    assert len({round(value, 2) for value in values}) >= 6
    assert any(detail["stage"] == "rendering" and 0 < value < .8 for value, detail in events if detail)
    assert done["progress"] == 1
    # Export rendering stays detached from the editor's published result.
    with SessionLocal() as db:
        assert db.get(ImagePage, _page_id).rendered_path is None


@pytest.mark.parametrize("kind", ["clean", "project"])
def test_non_render_exports_do_not_prepare_fonts_or_rasterize_text(client, monkeypatch, kind):
    from app.services.layout import FontResolver

    project_id, _page_id, _region_id = create_page(client)
    def unexpected(*args, **kwargs):
        raise AssertionError("This export only packages existing assets")
    monkeypatch.setattr(PillowRenderer, "render", unexpected)
    monkeypatch.setattr(FontResolver, "prepare", unexpected)
    job = client.post(f"/api/projects/{project_id}/exports", json={"kind": kind}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "COMPLETED", done


def test_archive_reports_chunks_and_stores_png_while_compressing_text(tmp_path):
    image, text = tmp_path / "page.png", tmp_path / "fonts.txt"
    image.write_bytes(b"p" * (3 * 1024**2 + 1))
    text.write_text("Noto Sans\n" * 100)
    chunks, checks = [], []
    target = tmp_path / "pages.zip"
    with ExportArchive(target, on_bytes=chunks.append, check=lambda: checks.append(1)) as archive:
        archive.write(image, "0001.png")
        archive.write(text, "fonts.txt")
    assert len(chunks) >= 5
    assert sum(chunks) == image.stat().st_size + text.stat().st_size
    assert len(checks) >= len(chunks)
    with ZipFile(target) as archive:
        assert archive.getinfo("0001.png").compress_type == ZIP_STORED
        assert archive.getinfo("fonts.txt").compress_type == ZIP_DEFLATED
        assert archive.read("0001.png") == image.read_bytes()


def test_archive_cancellation_interrupts_a_single_large_file_between_chunks(tmp_path):
    path = tmp_path / "page.png"
    path.write_bytes(b"p" * (3 * 1024**2))
    chunks = []
    def check():
        if chunks:
            raise RuntimeError("cancelled")
    with pytest.raises(RuntimeError, match="cancelled"), ExportArchive(tmp_path / "cancel.zip", check=check, on_bytes=chunks.append) as archive:
        archive.write(path, "0001.png")
    assert len(chunks) == 1


def test_textless_png_named_jpeg_is_normalized_to_real_png_in_cbz(client):
    project_id = client.post("/api/projects", json={"name": "Image format"}).json()["id"]
    stream = BytesIO()
    Image.new("RGB", (40, 30), "white").save(stream, "JPEG")
    uploaded = client.post(f"/api/projects/{project_id}/images", files={"files": ("renamed.png", stream.getvalue())})
    assert uploaded.status_code == 201, uploaded.text
    job = client.post(f"/api/projects/{project_id}/exports", json={"kind": "cbz"}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "COMPLETED", done
    with ZipFile(BytesIO(client.get(done["download_url"]).content)) as archive:
        with Image.open(BytesIO(archive.read("0001.png"))) as image:
            assert image.format == "PNG"
            assert image.size == (40, 30)
