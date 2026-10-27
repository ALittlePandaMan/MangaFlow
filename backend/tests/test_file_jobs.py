from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile, ZipInfo

import pytest
from app.core.config import get_settings
from app.services.file_import import extract_comic, render_pdf
from app.services.psd_export import text_data, write_psd
from PIL import Image


def png():
    stream = BytesIO()
    Image.new("RGB", (30, 20), "white").save(stream, "PNG")
    return stream.getvalue()


def test_shared_line_break_contract():
    import json

    from app.services.layout.line_breaking import wrap_paragraph
    source = Path(__file__).resolve().parents[2] / "frontend/src/features/editor/lib/lineBreakFixtures.json"
    for fixture in json.loads(source.read_text(encoding="utf-8")):
        assert wrap_paragraph(fixture["text"], lambda items, fixture=fixture: len(items) <= fixture["width"]) == fixture["lines"]


def test_natural_comic_order_and_nested_paths(tmp_path):
    source = tmp_path / "book.cbz"
    with ZipFile(source, "w") as archive:
        for name in ["漫画/10.png", "漫画/2.png", "漫画/1.png", "__MACOSX/._2.png"]:
            archive.writestr(name, png())
        archive.writestr("readme.txt", "ignored")
    pages = extract_comic(source, tmp_path / "pages", get_settings())
    assert [name for _, name in pages] == ["1.png", "2.png", "10.png"]


@pytest.mark.skipif(__import__("shutil").which("unrar") is None, reason="unrar is required for compressed RAR5")
def test_rar5_solid_fixture(tmp_path):
    source = Path(__file__).parent / "fixtures/comics/rar5-solid.cbr"
    pages = extract_comic(source, tmp_path / "pages", get_settings())
    assert [name for _, name in pages] == ["1.png", "2.png", "10.png"]
    with Image.open(pages[0][0]) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)


@pytest.mark.parametrize("kind", ["traversal", "symlink", "corrupt", "project", "duplicate", "limit"])
def test_comic_rejects_unsafe_inputs(tmp_path, kind):
    source = tmp_path / "bad.zip"
    with ZipFile(source, "w") as archive:
        if kind == "traversal":
            archive.writestr("../bad.png", png())
        elif kind == "symlink":
            entry = ZipInfo("link.png")
            entry.external_attr = 0o120777 << 16
            archive.writestr(entry, "target")
        elif kind == "project":
            archive.writestr("project.json", "{}")
        elif kind == "duplicate":
            archive.writestr("A.png", png())
            archive.writestr("a.png", png())
        else:
            archive.writestr("1.png", png() if kind == "limit" else b"not an image")
    settings = get_settings().model_copy(update={"max_import_page_pixels": 1}) if kind == "limit" else get_settings()
    with pytest.raises((ValueError, OSError)):
        extract_comic(source, tmp_path / "pages", settings)


def test_pdf_rotation_and_limits(tmp_path):
    import pypdfium2 as pdfium
    source = tmp_path / "book.pdf"
    with pdfium.PdfDocument.new() as doc:
        page = doc.new_page(72, 144)
        page.set_rotation(90)
        page.close()
        doc.save(source)
    render_pdf(source, tmp_path / "pages", 2, 500000, 1024**2)
    with Image.open(tmp_path / "pages/000000.png") as image:
        assert image.size == (600, 300)
    with pytest.raises(ValueError):
        render_pdf(source, tmp_path / "limited", 2, 100, 1024**2)


def test_psd_has_editable_text_and_exact_composite(tmp_path):
    psd_tools = pytest.importorskip("psd_tools")
    image = tmp_path / "image.png"
    Image.new("RGB", (120, 80), "white").save(image)
    region = SimpleNamespace(translated_text="Hello 漫画", font_size=20, translated_bbox=[10, 10, 100, 60],
        text_color="#111111", stroke_color="#ffffff", stroke_width=0, font_weight=400,
        line_spacing=1.15, character_spacing=0, alignment="center", rotation=15, orientation="vertical")
    target = tmp_path / "test.psd"
    write_psd(target, (120, 80), [
        {"name": "Background", "path": image, "bounds": (0, 0, 120, 80)},
        {"name": "文字", "path": image, "bounds": (0, 0, 120, 80), "text": text_data(region, {}, "NotoSansCJKsc-Regular")},
    ], image)
    doc = psd_tools.PSDImage.open(target)
    assert doc.size == (120, 80)
    text = next(layer for layer in doc if layer.kind == "type")
    assert text.text.rstrip("\r") == "Hello 漫画"
    assert text.engine_dict["StyleRun"]["RunLengthArray"][0].value == len("Hello 漫画\r")
    assert doc.topil().convert("RGB").tobytes() == Image.open(image).tobytes()


def wait_job(client, identifier):
    import time
    for _ in range(100):
        job = client.get(f"/api/file-jobs/{identifier}").json()
        if job["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return job
        time.sleep(.1)
    raise AssertionError("file job timed out")


def test_async_import_export_and_undo(client):
    project = client.post("/api/projects", json={"name": "Comic"}).json()
    source = BytesIO()
    with ZipFile(source, "w") as archive:
        archive.writestr("10.png", png())
        archive.writestr("2.png", png())
    response = client.post(f"/api/projects/{project['id']}/imports", files={"file": ("comic.cbz", source.getvalue())})
    assert response.status_code == 202, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "COMPLETED", job
    pages = client.get(f"/api/projects/{project['id']}/images").json()
    assert [page["filename"] for page in pages] == ["2.png", "10.png"]
    for kind in ["cbz", "psd", "project", "clean", "translated"]:
        response = client.post(f"/api/projects/{project['id']}/exports", json={"kind": kind})
        assert response.status_code == 202
        job = wait_job(client, response.json()["id"])
        assert job["status"] == "COMPLETED", job
        response = client.get(job["download_url"])
        assert response.status_code == 200
        with ZipFile(BytesIO(response.content)) as archive:
            if kind == "cbz":
                assert archive.namelist() == ["0001.png", "0002.png"]
    assert len(client.get(f"/api/projects/{project['id']}/images").json()) == 2
    history = client.get(f"/api/projects/{project['id']}/history").json()
    response = client.post(f"/api/projects/{project['id']}/history/undo", json={"expected_entry_id": history["undo_id"]})
    assert response.status_code == 200, response.text
    assert client.get(f"/api/projects/{project['id']}/images").json() == []
    history = client.get(f"/api/projects/{project['id']}/history").json()
    assert client.post(f"/api/projects/{project['id']}/history/redo", json={"expected_entry_id": history["redo_id"]}).status_code == 200
    assert len(client.get(f"/api/projects/{project['id']}/images").json()) == 2


def test_export_does_not_lock_edits_and_can_cancel_queued_and_running(client, monkeypatch):
    import threading

    from app.services import file_export
    entered, release = threading.Event(), threading.Event()
    original = file_export.export_snapshot
    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(file_export, "export_snapshot", blocked)
    project = client.post("/api/projects", json={"name": "Nonblocking"}).json()["id"]
    response = client.post(f"/api/projects/{project}/images", files=[("files", ("page.png", png()))])
    page_id = response.json()[0]["id"]
    first = client.post(f"/api/projects/{project}/exports", json={"kind": "cbz"}).json()["id"]
    try:
        assert entered.wait(10)
        region = client.post(f"/api/images/{page_id}/regions", json={"bbox": [1, 1, 20, 15],
            "polygon": [[1, 1], [21, 1], [21, 16], [1, 16]]})
        assert region.status_code == 201, region.text
        updated = client.patch(f"/api/regions/{region.json()['id']}", json={"source_text": "edit during export"})
        assert updated.status_code == 200, updated.text
        second = client.post(f"/api/projects/{project}/exports", json={"kind": "cbz"}).json()["id"]
        assert client.post(f"/api/file-jobs/{second}/cancel").json()["status"] == "CANCELLED"
        assert client.post(f"/api/file-jobs/{first}/cancel").json()["status"] == "CANCELLING"
    finally:
        release.set()
    assert wait_job(client, first)["status"] == "CANCELLED"
    assert client.get(f"/api/file-jobs/{first}/download").status_code == 409
    assert client.get(f"/api/images/{page_id}/regions").json()[0]["source_text"] == "edit during export"


def test_failed_import_publishes_no_pages(client):
    project = client.post("/api/projects", json={"name": "Atomic"}).json()["id"]
    source = BytesIO()
    with ZipFile(source, "w") as archive:
        archive.writestr("1.png", png())
        archive.writestr("2.png", b"corrupt")
    job = client.post(f"/api/projects/{project}/imports", files={"file": ("bad.zip", source.getvalue())}).json()
    assert wait_job(client, job["id"])["status"] == "FAILED"
    assert client.get(f"/api/projects/{project}/images").json() == []


def test_recovery_marks_running_failed_but_preserves_queued(model_engine, monkeypatch):
    from app.models import FileJob, Project
    from app.services import file_jobs
    from sqlalchemy.orm import sessionmaker
    sessions = sessionmaker(bind=model_engine, expire_on_commit=False)
    monkeypatch.setattr(file_jobs, "SessionLocal", sessions)
    with sessions() as db:
        project = Project(name="Recovery")
        db.add(project)
        db.flush()
        running = FileJob(project_id=project.id, kind="cbz", status="RUNNING")
        queued = FileJob(project_id=project.id, kind="cbz", status="QUEUED")
        db.add_all([running, queued])
        db.commit()
    worker = file_jobs.FileJobWorker()
    monkeypatch.setattr(worker, "run", lambda: None)
    worker.start()
    worker.shutdown()
    with sessions() as db:
        assert db.get(FileJob, running.id).status == "FAILED"
        assert db.get(FileJob, queued.id).status == "QUEUED"


def test_psd_region_group_and_perspective_backup(client):
    psd_tools = pytest.importorskip("psd_tools")
    project = client.post("/api/projects", json={"name": "PSD"}).json()["id"]
    page_id = client.post(f"/api/projects/{project}/images", files=[("files", ("page.png", png()))]).json()[0]["id"]
    region = client.post(f"/api/images/{page_id}/regions", json={"bbox": [1, 1, 20, 15],
        "polygon": [[1, 1], [21, 1], [21, 16], [1, 16]], "translated_text": "Hi", "perspective_warp": True}).json()
    job = client.post(f"/api/projects/{project}/exports", json={"kind": "psd"}).json()
    done = wait_job(client, job["id"])
    assert done["status"] == "COMPLETED", done
    doc = psd_tools.PSDImage.open(BytesIO(client.get(done["download_url"]).content))
    group = next(layer for layer in doc if layer.name == region["region_key"])
    text = next(layer for layer in group if layer.kind == "type")
    assert text.text.rstrip("\r").replace("\r", "") == "Hi"
    assert not text.visible
    assert any(layer.name == "Rendered text" and layer.visible for layer in group)


def test_export_snapshot_keeps_original_text_while_live_text_changes(client, monkeypatch):
    import threading

    from app.services import file_export
    psd_tools = pytest.importorskip("psd_tools")
    entered, release = threading.Event(), threading.Event()
    original = file_export.export_snapshot
    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(file_export, "export_snapshot", blocked)
    project = client.post("/api/projects", json={"name": "Snapshot"}).json()["id"]
    page_id = client.post(f"/api/projects/{project}/images", files=[("files", ("page.png", png()))]).json()[0]["id"]
    region = client.post(f"/api/images/{page_id}/regions", json={"bbox": [1, 1, 20, 15],
        "polygon": [[1, 1], [21, 1], [21, 16], [1, 16]], "translated_text": "Before", "orientation": "horizontal"}).json()
    job = client.post(f"/api/projects/{project}/exports", json={"kind": "psd"}).json()
    try:
        assert entered.wait(10)
        assert client.patch(f"/api/regions/{region['id']}", json={"translated_text": "After"}).status_code == 200
    finally:
        release.set()
    done = wait_job(client, job["id"])
    assert done["status"] == "COMPLETED", done
    doc = psd_tools.PSDImage.open(BytesIO(client.get(done["download_url"]).content))
    texts = [layer.text.rstrip("\r").replace("\r", "") for layer in doc.descendants() if layer.kind == "type"]
    assert texts == ["Before"]
    assert client.get(f"/api/images/{page_id}/regions").json()[0]["translated_text"] == "After"
