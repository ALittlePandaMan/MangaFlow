"""Regression coverage for imported geometry, canonical pixels and live file jobs."""
import base64
import json
from datetime import timedelta
from io import BytesIO
from zipfile import ZipFile

import pytest
from app.core.database import SessionLocal
from app.models import FileJob, Project
from app.models.entities import utcnow
from app.services.file_jobs import file_worker
from app.storage import get_storage
from PIL import Image, ImageOps
from sqlalchemy import func, select
from test_file_jobs import wait_job
from test_mcp import rpc


def picture(format="PNG", orientation=None):
    image = Image.new("RGB", (80, 40), "white")
    image.paste("red", (0, 0, 40, 20))
    image.paste("blue", (40, 20, 80, 40))
    data = BytesIO()
    exif = Image.Exif()
    exif[270] = "preserved description"
    if orientation is not None:
        exif[274] = orientation
    image.save(data, format, exif=exif)
    return data.getvalue()


def archive_bytes(regions, *, original=None, assets=None):
    data = BytesIO()
    manifest = {"schema_version": 2, "project": {"name": "Integrity test"}, "pages": [{
        "filename": "page.jpg" if original else "page.png", "width": 80, "height": 40,
        "assets": {"original": "original", **(assets or {})}, "regions": regions}]}
    with ZipFile(data, "w") as archive:
        archive.writestr("project.json", json.dumps(manifest))
        archive.writestr("original", original or picture())
        if assets:
            for name in set(assets.values()):
                archive.writestr(name, picture())
    return data.getvalue()


@pytest.mark.parametrize("invalid", [
    {},
    {"bbox": []},
    {"polygon": [[0, 0], [20, 0], [40, 0]]},
    {"polygon": [[0, 0], [30, 30], [0, 30], [30, 0]]},
    {"bbox": [0, 0, float("inf"), 20]},
    {"bbox": [0, 0, 20, 20], "translated_polygon": [[0, 0], [30, 30], [0, 30], [30, 0]]},
    {"bbox": [0, 0, 20, 20], "translated_polygon": [[0, 0], [30, 0], [10, 10], [0, 30]], "perspective_warp": True},
])
def test_project_import_rejects_bad_geometry_atomically(client, invalid):
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(Project))
    directory = get_storage().root / "projects"
    directories = set(directory.iterdir()) if directory.exists() else set()
    response = client.post("/api/projects/import", files={"file": ("bad.zip", archive_bytes([
        {"bbox": [0, 0, 20, 20]}, invalid]))})
    assert response.status_code == 400, response.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Project)) == before
    assert set(directory.iterdir()) == directories


@pytest.mark.parametrize("frames", [
    {"bbox": [5, 5, 20, 20]},
    {"polygon": [[5, 5], [25, 5], [15, 12], [5, 25]]},
    {"bbox": [5, 5, 20, 20], "translated_polygon": [[30, 5], [60, 5], [60, 30], [30, 30]]},
])
def test_project_import_derives_missing_frames_and_preserves_valid_concave_polygons(client, frames):
    from app.utils.geometry import normalize_region_frames
    response = client.post("/api/projects/import", files={"file": ("valid.zip", archive_bytes([frames]))})
    assert response.status_code == 201, response.text
    page = client.get(f"/api/projects/{response.json()['id']}/images").json()[0]
    region = client.get(f"/api/images/{page['id']}/regions").json()[0]
    for key, value in normalize_region_frames(frames).items():
        assert region[key] == value
    client.headers["Host"] = "localhost"
    assert not rpc(client, "page_preview", {"image_id": page["id"], "region_id": region["id"]}).get("isError")


@pytest.mark.parametrize("orientation", range(1, 9))
@pytest.mark.parametrize("format,suffix", [("JPEG", ".jpg"), ("PNG", ".png"), ("WEBP", ".webp")])
def test_uploaded_exif_pixels_are_canonical_and_lossless_after_decode(tmp_path, orientation, format, suffix):
    from app.storage.files import StorageManager
    payload = picture(format, orientation)
    storage = StorageManager(tmp_path)
    path, width, height = storage.save_upload("project", "page", "rotated" + suffix, BytesIO(payload))
    with Image.open(BytesIO(payload)) as image:
        expected = ImageOps.exif_transpose(image).convert("RGB")
    with Image.open(storage.absolute(path)) as saved:
        assert saved.size == expected.size == (width, height)
        assert saved.getexif().get(274, 1) == 1
        assert saved.getexif()[270] == "preserved description"
        assert saved.convert("RGB").tobytes() == expected.tobytes()
    if orientation == 1:
        assert storage.absolute(path).read_bytes() == payload
    else:
        assert path.endswith(".png")


@pytest.mark.parametrize("mode", ["RGBA", "P", "CMYK"])
def test_oriented_upload_preserves_alpha_and_converts_cmyk_safely(tmp_path, mode):
    from app.storage.files import StorageManager
    image = Image.new("RGBA", (80, 40), (255, 0, 0, 0))
    image.paste((0, 0, 255, 255), (0, 0, 40, 20))
    image = image.convert(mode)
    exif = Image.Exif()
    exif[274] = 6
    payload = BytesIO()
    image.save(payload, "JPEG" if mode == "CMYK" else "PNG", exif=exif)
    storage = StorageManager(tmp_path)
    path, width, height = storage.save_upload("project", "page", "oriented.jpg" if mode == "CMYK" else "oriented.png", BytesIO(payload.getvalue()))
    with Image.open(BytesIO(payload.getvalue())) as source:
        expected = ImageOps.exif_transpose(source).convert("RGBA")
    with Image.open(storage.absolute(path)) as saved:
        assert saved.size == (width, height) == (40, 80)
        assert saved.convert("RGBA").tobytes() == expected.tobytes()


def assert_preview_space(client, page):
    client.headers["Host"] = "localhost"
    native = rpc(client, "page_preview", {"image_id": page["id"]})
    with Image.open(BytesIO(base64.b64decode(native["content"][1]["data"]))) as preview:
        assert preview.size == (page["width"], page["height"])
    with Image.open(BytesIO(client.get(page["thumbnail_url"]).content)) as thumbnail:
        assert thumbnail.size == (page["width"], page["height"])


@pytest.mark.parametrize("container", [False, True])
def test_upload_and_comic_import_share_exif_coordinates_with_mcp_and_export(client, container):
    project = client.post("/api/projects", json={"name": "Canonical orientation"}).json()
    payload = picture("JPEG", 6)
    if container:
        data = BytesIO()
        with ZipFile(data, "w") as archive:
            archive.writestr("photo.jpg", payload)
        queued = client.post(f"/api/projects/{project['id']}/imports", files={"file": ("comic.cbz", data.getvalue())}).json()
        assert wait_job(client, queued["id"])["status"] == "COMPLETED"
        page = client.get(f"/api/projects/{project['id']}/images").json()[0]
    else:
        page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("photo.jpg", payload)}).json()[0]
    assert (page["width"], page["height"]) == (40, 80)
    assert_preview_space(client, page)
    queued = client.post(f"/api/projects/{project['id']}/exports", json={"kind": "cbz"}).json()
    done = wait_job(client, queued["id"])
    assert done["status"] == "COMPLETED", done
    with ZipFile(BytesIO(client.get(done["download_url"]).content)) as archive:
        with Image.open(BytesIO(archive.read("0001.png"))) as exported:
            with Image.open(BytesIO(payload)) as original:
                expected = ImageOps.exif_transpose(original).convert("RGB")
            assert exported.size == (40, 80)
            assert exported.convert("RGB").tobytes() == expected.tobytes()


def test_legacy_project_exif_hint_cannot_rotate_saved_geometry_or_mask(client):
    frame = {"bbox": [5, 5, 20, 10], "assets": {"mask": "mask.png"}}
    payload = archive_bytes([frame], original=picture("JPEG", 6), assets={"clean": "clean.png", "mask": "mask.png"})
    imported = client.post("/api/projects/import", files={"file": ("legacy.zip", payload)})
    assert imported.status_code == 201, imported.text
    page = client.get(f"/api/projects/{imported.json()['id']}/images").json()[0]
    assert (page["width"], page["height"]) == (80, 40)
    assert_preview_space(client, page)
    region = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert region["bbox"] == frame["bbox"]
    with Image.open(BytesIO(client.get(region["mask_url"]).content)) as mask:
        assert mask.size == (80, 40)
    with Image.open(BytesIO(client.get(page["clean_url"]).content)) as clean:
        assert clean.size == (80, 40)


def test_file_job_history_pagination_never_truncates_live_work(client):
    file_worker.shutdown()  # Seed deterministic queue states only in the disposable test database.
    project = client.post("/api/projects", json={"name": "Live queue"}).json()
    other = client.post("/api/projects", json={"name": "Other project"}).json()
    with SessionLocal() as db:
        running = FileJob(project_id=project["id"], kind="cbz", status="RUNNING", created_at=utcnow()-timedelta(hours=2))
        db.add(running)
        db.flush()
        running_id = running.id
        db.add_all(FileJob(project_id=project["id"], kind="cbz", status="QUEUED") for _ in range(120))
        db.add_all(FileJob(project_id=project["id"], kind="cbz", status="COMPLETED") for _ in range(130))
        db.add(FileJob(project_id=other["id"], kind="cbz", status="CANCELLING"))
        db.commit()
    url = f"/api/file-jobs?project_id={project['id']}"
    result = client.get(url).json()
    assert len(result) == 221 and len({job["id"] for job in result}) == 221
    assert len([job for job in result if job["status"] in {"RUNNING", "QUEUED"}]) == 121
    assert running_id in {job["id"] for job in result}
    first, second = (client.get(url + f"&limit=7&offset={offset}").json() for offset in (0, 7))
    first_done = {job["id"] for job in first if job["status"] == "COMPLETED"}
    second_done = {job["id"] for job in second if job["status"] == "COMPLETED"}
    assert len(first_done) == len(second_done) == 7 and first_done.isdisjoint(second_done)
    assert len(first) == len(second) == 128
    assert client.post(f"/api/file-jobs/{running_id}/cancel").json()["status"] == "CANCELLING"
    assert client.get(url + "&limit=0").status_code == 422
    assert client.get(url + "&offset=-1").status_code == 422


def test_file_job_status_batch_recovers_old_completions_and_omits_deleted_jobs(client):
    project = client.post("/api/projects", json={"name": "Old completion"}).json()
    with SessionLocal() as db:
        job = FileJob(project_id=project["id"], kind="cbz", status="COMPLETED")
        db.add(job)
        db.commit()
        identifier = job.id
    response = client.post("/api/file-jobs/status", json=[identifier, "deleted", identifier])
    assert response.status_code == 200
    assert [job["id"] for job in response.json()] == [identifier]
    assert client.post("/api/file-jobs/status", json=[]).status_code == 422
    assert client.post("/api/file-jobs/status", json=[identifier]*401).status_code == 422
    client.headers["Host"] = "localhost"
    result = rpc(client, "task_wait", {"task_id": identifier, "kind": "file", "seconds": 0})
    assert not result.get("isError"), result
    assert result["structuredContent"]["id"] == identifier
