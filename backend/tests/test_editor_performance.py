from io import BytesIO

import app.services.editor_history as history_service
from app.core.database import SessionLocal
from app.models import EditorHistoryEntry, ImagePage, RegionRevision, TextRegion
from app.services.editor_history import project_history_state
from app.storage import get_storage
from PIL import Image, ImageDraw
from sqlalchemy import event, func, select
from test_editor_history import apply
from test_repair_geometry_sync import setup_page


def test_single_box_undo_does_not_rewrite_unrelated_regions_or_revisions(client):
    page, regions = setup_page(client, 35)
    storage = get_storage()
    mask_path = storage.page_dir(page["project_id"], page["id"]) / "masks" / "shared-live.png"
    mask_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("L", (200, 200), 255).save(mask_path)
    with SessionLocal() as db:
        for region in db.scalars(select(TextRegion).where(TextRegion.image_id == page["id"])):
            region.pixel_mask_path = storage.relative(mask_path)
        db.commit()
    target = regions[17]
    response = client.patch(f"/api/regions/{target['id']}", json={"font_size": 40})
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        timestamps = dict(
            db.execute(select(TextRegion.id, TextRegion.updated_at).where(TextRegion.image_id == page["id"])).all()
        )
        counts = dict(
            db.execute(
                select(RegionRevision.region_id, func.count())
                .where(RegionRevision.region_id.in_(timestamps))
                .group_by(RegionRevision.region_id)
            ).all()
        )
    apply(client, page["project_id"])
    with SessionLocal() as db:
        after_times = dict(
            db.execute(select(TextRegion.id, TextRegion.updated_at).where(TextRegion.image_id == page["id"])).all()
        )
        after_counts = dict(
            db.execute(
                select(RegionRevision.region_id, func.count())
                .where(RegionRevision.region_id.in_(timestamps))
                .group_by(RegionRevision.region_id)
            ).all()
        )
        unchanged = db.get(TextRegion, regions[0]["id"])
        assert unchanged.pixel_mask_path == storage.relative(mask_path)
    assert after_counts[target["id"]] == counts[target["id"]] + 1
    for id in timestamps:
        if id != target["id"]:
            assert after_times[id] == timestamps[id]
            assert after_counts[id] == counts[id]


def encoded(image):
    file = BytesIO()
    image.save(file, "PNG")
    file.seek(0)
    return file


def test_snapshot_resolves_a_shared_asset_once_and_refreshes_it_after_overwrite(client, monkeypatch):
    page, _ = setup_page(client)
    storage = get_storage()
    directory = storage.project_dir(page["project_id"])
    source = storage.page_dir(page["project_id"], page["id"]) / "versions" / "shared.png"
    Image.new("RGB", (200, 200), "white").save(source)
    relative = storage.relative(source)
    original = history_service._archive_file
    calls = []

    def archive(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(history_service, "_archive_file", archive)
    value = {
        "regions": [{"pixel_mask_path": relative, "inpainted_path": relative} for _ in range(100)],
        "source_text": relative,
    }
    result = history_service._archive_paths(value, directory, directory / "history" / "assets")
    assert len(calls) == 1
    assert result["source_text"] == relative
    old = result["regions"][0]["pixel_mask_path"]
    assert all(region["inpainted_path"] == old for region in result["regions"])
    Image.new("RGB", (200, 200), "black").save(source)
    refreshed = history_service._archive_paths(value, directory, directory / "history" / "assets")
    assert len(calls) == 2
    assert refreshed["regions"][0]["pixel_mask_path"] != old
    assert Image.open(storage.absolute(old)).getpixel((0, 0)) == (255, 255, 255)


def patch(client, region, image, x, y):
    return client.put(
        f"/api/regions/{region['id']}/mask",
        files={"file": ("patch.png", encoded(image), "image/png")},
        data={"x": x, "y": y, "expected_updated_at": region["updated_at"]},
    )


def test_small_mask_patches_preserve_other_pixels_version_checks_and_per_stroke_history(client):
    page, regions = setup_page(client)
    region = regions[0]
    first = patch(client, region, Image.new("L", (10, 12), 255), 20, 50)
    assert first.status_code == 200, first.text
    first = first.json()
    second = patch(client, first, Image.new("L", (2, 3), 0), 23, 54)
    assert second.status_code == 200, second.text
    assert patch(client, region, Image.new("L", (2, 2), 255), 100, 100).status_code == 409
    pixels = Image.open(BytesIO(client.get(second.json()["mask_url"]).content))
    assert pixels.size == (200, 200)
    assert pixels.getpixel((20, 50)) == 255 and pixels.getpixel((23, 54)) == 0
    assert pixels.getpixel((100, 100)) == 0
    restored = apply(client, page["project_id"])["regions"][0]
    assert Image.open(BytesIO(client.get(restored["mask_url"]).content)).getpixel((23, 54)) == 255
    assert apply(client, page["project_id"])["regions"][0]["mask_url"] is None
    apply(client, page["project_id"], "redo")
    redone = apply(client, page["project_id"], "redo")["regions"][0]
    assert Image.open(BytesIO(client.get(redone["mask_url"]).content)).getpixel((23, 54)) == 0


def test_mask_patch_rejects_out_of_page_coordinates_missing_version_and_locked_regions(client):
    _, regions = setup_page(client)
    region = regions[0]
    for x, y in [(-1, 0), (199, 199)]:
        assert patch(client, region, Image.new("L", (3, 3), 255), x, y).status_code == 422
    response = client.put(
        f"/api/regions/{region['id']}/mask",
        files={"file": ("p.png", encoded(Image.new("L", (2, 2))), "image/png")},
        data={"x": 0, "y": 0},
    )
    assert response.status_code == 422
    locked = client.patch(f"/api/regions/{region['id']}", json={"locked": True}).json()
    assert patch(client, locked, Image.new("L", (2, 2), 255), 0, 0).status_code == 409


def test_delete_restores_only_removed_repairs_preserves_overlaps_and_undo_without_models(client, monkeypatch):
    page, regions = setup_page(client, 2)
    for i, region in enumerate(regions):
        mask = Image.new("L", (200, 200), 0)
        ImageDraw.Draw(mask).rectangle((20 + i * 10, 20, 40 + i * 10, 60), fill=255)
        response = client.put(
            f"/api/regions/{region['id']}/mask", files={"file": ("m.png", encoded(mask), "image/png")}
        )
        assert response.status_code == 200
    with SessionLocal() as db:
        model = db.get(ImagePage, page["id"])
        directory = get_storage().page_dir(model.project_id, model.id)
        clean = Image.new("RGB", (200, 200), "white")
        ImageDraw.Draw(clean).rectangle((20, 20, 50, 60), fill="blue")
        clean.save(directory / "clean" / "clean.png")
        model.clean_path = get_storage().relative(directory / "clean" / "clean.png")
        model.rendered_path = model.clean_path
        model.text_layer_path = model.clean_path
        db.commit()
    monkeypatch.setattr(
        "app.api.regions.enqueue_page_task",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Deletion must not rerun inference")),
    )
    response = client.delete(f"/api/regions/{regions[0]['id']}")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["rebuild_task"] is None
    assert result["page"]["rendered_url"] is None and result["page"]["text_layer_url"] is None
    clean = Image.open(BytesIO(client.get(result["page"]["clean_url"]).content))
    assert clean.getpixel((25, 30)) == (0, 0, 0)  # Removed repair returns to original text.
    assert clean.getpixel((35, 30)) == (0, 0, 255)  # Overlapping remaining mask stays repaired.
    restored = apply(client, page["project_id"])
    assert len(restored["regions"]) == 2
    assert Image.open(BytesIO(client.get(restored["page"]["clean_url"]).content)).getpixel((25, 30)) == (0, 0, 255)
    redone = apply(client, page["project_id"], "redo")
    assert len(redone["regions"]) == 1
    response = client.post("/api/regions/bulk-delete", json=[regions[1]["id"]])
    assert response.status_code == 200 and response.json()["rebuild_task"] is None
    assert Image.open(BytesIO(client.get(response.json()["page"]["clean_url"]).content)).getpixel((35, 30)) == (
        255,
        255,
        255,
    )


def test_history_cursor_reads_do_not_select_or_deserialize_image_snapshots(client):
    page, _ = setup_page(client)
    with SessionLocal() as db:
        db.add(
            EditorHistoryEntry(
                project_id=page["project_id"],
                action="big snapshot",
                before={"payload": "x" * 1_000_000},
                after={"payload": "y" * 1_000_000},
                region_ids=[],
            )
        )
        db.commit()
        statements = []

        def observe(_connection, _cursor, statement, *_):
            statements.append(statement)

        connection = db.connection()
        event.listen(connection, "before_cursor_execute", observe)
        try:
            state = project_history_state(db, page["project_id"])
        finally:
            event.remove(connection, "before_cursor_execute", observe)
        assert state["undo_label"] == "big snapshot"
        assert all(
            "editor_history.before" not in statement and "editor_history.after" not in statement
            for statement in statements
        )


def test_sparse_history_warmup_survives_refresh_and_preserves_real_redo_branch(client):
    page, regions = setup_page(client)
    region = regions[0]
    first = patch(client, region, Image.new("L", (10, 12), 255), 20, 50).json()
    second = patch(client, first, Image.new("L", (2, 3), 0), 23, 54).json()
    window = client.get(f"/api/regions/{region['id']}/mask-history").json()
    assert window["region_updated_at"] == second["updated_at"]
    assert len(window["undo"]) == 2 and window["redo"] == []
    step = window["undo"][0]
    assert (step["x"], step["y"], step["width"], step["height"]) == (23, 54, 2, 3)
    assert Image.open(BytesIO(client.get(step["before_url"]).content)).getpixel((0, 0)) == 255
    assert Image.open(BytesIO(client.get(step["after_url"]).content)).getpixel((0, 0)) == 0
    apply(client, page["project_id"])
    window = client.get(f"/api/regions/{region['id']}/mask-history").json()
    assert len(window["undo"]) == 1 and len(window["redo"]) == 1
    assert window["redo"][0]["id"] == step["id"]
    current = client.get(f"/api/images/{page['id']}/regions").json()[0]
    patch(client, current, Image.new("L", (1, 1), 255), 90, 100)
    assert client.get(f"/api/regions/{region['id']}/mask-history").json()["redo"] == []


def test_background_history_retry_is_idempotent_and_cannot_repeat_an_old_command_after_other_edits(client):
    page, regions = setup_page(client)
    region = regions[0]
    first = patch(client, region, Image.new("L", (2, 2), 255), 20, 50).json()
    head = first["editor_history"]["undo_id"]
    body = {"expected_entry_id": head, "operation_id": "mask-undo-retry-1", "current_image_id": page["id"]}
    url = f"/api/projects/{page['project_id']}/history/undo"
    first_undo = client.post(url, json=body)
    assert first_undo.status_code == 200, first_undo.text
    retried = client.post(url, json=body)
    assert retried.status_code == 200, retried.text
    assert retried.json()["history"] == first_undo.json()["history"]
    assert retried.json()["regions"][0]["mask_url"] is None
    apply(client, page["project_id"], "redo")
    assert client.post(url, json=body).status_code == 409


def test_mask_warmup_does_not_skip_other_region_or_non_mask_commands_and_caches_legacy_assets(client):
    page, regions = setup_page(client, 2)
    a, b = regions
    first = patch(client, a, Image.new("L", (2, 2), 255), 10, 10).json()
    patch(client, b, Image.new("L", (2, 2), 255), 30, 30)
    assert client.get(f"/api/regions/{a['id']}/mask-history").json()["undo"] == []
    apply(client, page["project_id"])
    # Emulate a persisted mask step from an older version of the application.
    with SessionLocal() as db:
        entry = db.get(EditorHistoryEntry, first["editor_history"]["undo_id"])
        entry.after = {key: value for key, value in entry.after.items() if key != "_mask_delta"}
        db.commit()
    window = client.get(f"/api/regions/{a['id']}/mask-history").json()
    assert len(window["undo"]) == 1
    assert Image.open(BytesIO(client.get(window["undo"][0]["after_url"]).content)).getpixel((0, 0)) == 255
    assert window["redo"] == []  # The next project command edits another mask.
    client.patch(f"/api/regions/{a['id']}", json={"font_size": 36})
    assert client.get(f"/api/regions/{a['id']}/mask-history").json()["undo"] == []


def test_missing_sparse_preview_does_not_block_authoritative_undo_redo_and_is_recreated(client):
    page, regions = setup_page(client)
    region = regions[0]
    first = patch(client, region, Image.new("L", (3, 4), 255), 20, 50).json()
    with SessionLocal() as db:
        entry = db.get(EditorHistoryEntry, first["editor_history"]["undo_id"])
        delta = entry.after["_mask_delta"]
        get_storage().absolute(delta["before"]).unlink()
        get_storage().absolute(delta["after"]).unlink()
    apply(client, page["project_id"])
    redone = apply(client, page["project_id"], "redo")["regions"][0]
    assert Image.open(BytesIO(client.get(redone["mask_url"]).content)).getpixel((20, 50)) == 255
    window = client.get(f"/api/regions/{region['id']}/mask-history").json()
    assert len(window["undo"]) == 1
    assert client.get(window["undo"][0]["before_url"]).status_code == 200
    assert client.get(window["undo"][0]["after_url"]).status_code == 200
