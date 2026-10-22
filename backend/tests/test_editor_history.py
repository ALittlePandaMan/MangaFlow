from __future__ import annotations

import time

import pytest
from app.core.database import SessionLocal
from app.models import EditorHistoryEntry, ProcessingTask, TextRegion
from app.services.editor_history import begin_task_history, project_history_state
from app.storage import get_storage
from app.tasks.manager import TaskManager
from test_repair_geometry_sync import png, setup_page, wait_task
from test_repair_geometry_sync import repair_provider as repair_provider


def history(client, project_id):
    for _ in range(100):
        state = client.get(f"/api/projects/{project_id}/history").json()
        if not state["pending"]:
            return state
        time.sleep(0.01)
    raise AssertionError("History checkpoint did not finish")


def apply(client, project_id, direction="undo"):
    state = history(client, project_id)
    response = client.post(
        f"/api/projects/{project_id}/history/{direction}", json={"expected_entry_id": state[f"{direction}_id"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def rows(client, page):
    return client.get(f"/api/images/{page['id']}/regions").json()


def test_history_cursor_and_pending_flag_share_one_snapshot(client, repair_provider, monkeypatch):
    page, _ = setup_page(client)
    with SessionLocal() as db:
        entry = EditorHistoryEntry(project_id=page["project_id"], image_id=page["id"], action="finishing",
                                   before={}, after={}, pending=True)
        db.add(entry)
        db.commit()
        identifier = entry.id
    with SessionLocal() as db:
        execute = db.execute
        calls = []

        def finish_after_first_read(statement, *args, **kwargs):
            result = execute(statement, *args, **kwargs)
            if not calls:
                with SessionLocal() as writer:
                    writer.get(EditorHistoryEntry, identifier).pending = False
                    writer.commit()
            calls.append(statement)
            return result

        monkeypatch.setattr(db, "execute", finish_after_first_read)
        state = project_history_state(db, page["project_id"])
        assert state["pending"] or state["undo_id"] == identifier
        assert len(calls) == 1


def test_creation_shape_styles_and_copy_split_merge_can_all_undo_and_redo(client, repair_provider):
    page, initial = setup_page(client, 2)
    project_id = page["project_id"]
    created = client.post(
        f"/api/images/{page['id']}/regions",
        json={"polygon": [[15, 15], [75, 20], [50, 85]], "translated_text": "triangle"},
    ).json()
    assert len(rows(client, page)) == 3
    assert created["id"] not in {item["id"] for item in apply(client, project_id)["regions"]}
    redone = apply(client, project_id, "redo")["regions"]
    restored = next(item for item in redone if item["id"] == created["id"])
    assert restored["polygon"] == created["polygon"] and restored["region_key"] == created["region_key"]
    patch = {
        "translated_polygon": [[20, 20], [90, 40], [70, 120], [10, 100]],
        "rotation": 16,
        "perspective_warp": True,
        "source_text": "original",
        "translated_text": "edited",
        "font_size": 32,
        "text_color": "#123456",
        "locked": True,
        "visible": False,
    }
    changed = client.patch(f"/api/regions/{created['id']}", json=patch).json()
    undone = next(item for item in apply(client, project_id)["regions"] if item["id"] == created["id"])
    for field in patch:
        assert undone[field] == created[field]
    redone = next(item for item in apply(client, project_id, "redo")["regions"] if item["id"] == created["id"])
    for field in patch:
        assert redone[field] == changed[field]
    copy = client.post(f"/api/regions/{initial[0]['id']}/copy").json()
    assert copy["id"] not in {item["id"] for item in apply(client, project_id)["regions"]}
    assert copy["id"] in {item["id"] for item in apply(client, project_id, "redo")["regions"]}
    split = client.post(f"/api/regions/{initial[0]['id']}/split").json()
    assert len(split) == 2
    unsplit = apply(client, project_id)["regions"]
    assert split[1]["id"] not in {item["id"] for item in unsplit}
    assert next(item for item in unsplit if item["id"] == initial[0]["id"])["polygon"] == initial[0]["polygon"]
    assert split[1]["id"] in {item["id"] for item in apply(client, project_id, "redo")["regions"]}
    merged = client.post("/api/regions/merge", json=[item["id"] for item in split]).json()["region"]
    assert len([item for item in rows(client, page) if item["id"] in {region["id"] for region in split}]) == 1
    unmerged = apply(client, project_id)["regions"]
    assert all(item["id"] in {row["id"] for row in unmerged} for item in split)
    assert (
        next(item for item in apply(client, project_id, "redo")["regions"] if item["id"] == merged["id"])["polygon"]
        == merged["polygon"]
    )


def test_batch_updates_are_one_atomic_history_step_and_validate_scope(client, repair_provider):
    page, regions = setup_page(client, 3)
    baseline = history(client, page["project_id"])["undo_count"]
    changes = [
        {"id": region["id"], "patch": {"font_size": 42, "translated_bbox": [20 + index * 50, 20, 40, 80]}}
        for index, region in enumerate(regions)
    ]
    response = client.post("/api/regions/bulk-update", json=changes)
    assert response.status_code == 200, response.text
    assert history(client, page["project_id"])["undo_count"] == baseline + 1
    assert [item["font_size"] for item in apply(client, page["project_id"])["regions"]] == [
        item["font_size"] for item in regions
    ]
    assert all(item["font_size"] == 42 for item in apply(client, page["project_id"], "redo")["regions"])
    other_page, other = setup_page(client)
    invalid = client.post(
        "/api/regions/bulk-update", json=[changes[0], {"id": other[0]["id"], "patch": {"font_size": 55}}]
    )
    assert invalid.status_code == 422
    assert rows(client, other_page)[0]["font_size"] != 55


def test_mask_delete_and_reset_restore_immutable_files_not_only_geometry(client, repair_provider):
    page, regions = setup_page(client, 2)
    project_id = page["project_id"]
    masked = client.put(
        f"/api/regions/{regions[0]['id']}/mask", files={"file": ("mask.png", png(mask=True), "image/png")}
    ).json()
    pixels = client.get(masked["mask_url"]).content
    assert apply(client, project_id)["regions"][0]["mask_url"] is None
    redo_mask = apply(client, project_id, "redo")["regions"][0]
    assert client.get(redo_mask["mask_url"]).content == pixels
    response = client.post("/api/regions/bulk-delete", json=[item["id"] for item in regions])
    assert response.status_code == 200
    assert rows(client, page) == []
    restored = apply(client, project_id)["regions"]
    assert [item["id"] for item in restored] == [item["id"] for item in regions]
    assert client.get(restored[0]["mask_url"]).content == pixels
    apply(client, project_id, "redo")
    assert rows(client, page) == []
    apply(client, project_id)
    assert client.post(f"/api/images/{page['id']}/reset").status_code == 200
    assert rows(client, page) == []
    restored = apply(client, project_id)["regions"]
    assert client.get(restored[0]["mask_url"]).content == pixels
    apply(client, project_id, "redo")
    assert rows(client, page) == []


def test_processing_history_restores_text_frames_and_artifacts_without_rerunning_models(client, repair_provider):
    page, regions = setup_page(client)
    project_id = page["project_id"]
    before = history(client, project_id)["undo_count"]
    task = wait_task(
        client,
        client.post(
            f"/api/images/{page['id']}/process",
            json={
                "start_stage": "ocr",
                "end_stage": "rendering",
                "force": True,
                "options": {"text_segmentation": False},
            },
        ).json(),
    )
    assert task["status"] == "COMPLETED", task
    assert history(client, project_id)["undo_count"] == before + 1
    calls = repair_provider.calls
    processed_page = client.get(f"/api/images/{page['id']}").json()
    result = rows(client, page)[0]
    rendered = client.get(processed_page["rendered_url"]).content
    reverted = apply(client, project_id)
    assert reverted["regions"][0]["source_text"] == regions[0]["source_text"]
    assert reverted["page"]["clean_url"] is None
    restored = apply(client, project_id, "redo")
    assert restored["regions"][0]["source_text"] == result["source_text"]
    assert restored["regions"][0]["translated_text"] == result["translated_text"]
    assert client.get(restored["page"]["rendered_url"]).content == rendered
    assert repair_provider.calls == calls
    # Structural deletion and its automatic rebuild remain one undo command.
    baseline = history(client, project_id)["undo_count"]
    removed = client.delete(f"/api/regions/{regions[0]['id']}").json()
    assert removed["rebuild_task"] is None
    assert repair_provider.calls == calls
    assert history(client, project_id)["undo_count"] == baseline + 1
    restored = apply(client, project_id)
    assert restored["regions"][0]["id"] == regions[0]["id"]
    assert client.get(restored["page"]["rendered_url"]).content == rendered


def test_import_delete_and_order_share_the_same_project_history(client, repair_provider):
    project = client.post("/api/projects", json={"name": "image-history"}).json()
    project_id = project["id"]
    pages = client.post(
        f"/api/projects/{project_id}/images",
        files=[
            ("files", ("a.png", png(), "image/png")),
            ("files", ("b.png", png(), "image/png")),
            ("files", ("c.png", png(), "image/png")),
        ],
    ).json()
    assert history(client, project_id)["undo_count"] == 1
    assert apply(client, project_id)["pages"] == []
    restored = apply(client, project_id, "redo")["pages"]
    assert [item["id"] for item in restored] == [item["id"] for item in pages]
    original = client.get(restored[1]["original_url"]).content
    region = client.post(
        f"/api/images/{pages[1]['id']}/regions",
        json={"bbox": [10, 10, 40, 100], "source_text": "kept", "translated_text": "saved"},
    ).json()
    desired = [pages[2]["id"], pages[0]["id"], pages[1]["id"]]
    baseline = history(client, project_id)["undo_count"]
    assert client.post(f"/api/projects/{project_id}/images/order", json=desired).status_code == 200
    assert history(client, project_id)["undo_count"] == baseline + 1
    assert [item["id"] for item in apply(client, project_id)["pages"]] == [item["id"] for item in pages]
    assert [item["id"] for item in apply(client, project_id, "redo")["pages"]] == desired
    assert client.delete(f"/api/images/{pages[1]['id']}").status_code == 204
    assert client.get(f"/api/images/{pages[1]['id']}").status_code == 404
    recovered = apply(client, project_id)
    assert recovered["page"]["id"] == pages[1]["id"]
    assert recovered["regions"][0]["id"] == region["id"]
    assert recovered["regions"][0]["source_text"] == "kept"
    assert client.get(recovered["page"]["original_url"]).content == original
    apply(client, project_id, "redo")
    assert client.get(f"/api/images/{pages[1]['id']}").status_code == 404
    apply(client, project_id)
    apply(client, project_id)  # Undo order.
    apply(client, project_id)  # Undo region creation after image restoration.
    assert rows(client, pages[1]) == []
    assert apply(client, project_id)["pages"] == []  # Undo the entire import.


def test_new_edits_clear_redo_noops_do_not_and_stale_cursors_cannot_restore(client, repair_provider):
    page, regions = setup_page(client)
    project_id = page["project_id"]
    client.patch(f"/api/regions/{regions[0]['id']}", json={"font_size": 30})
    apply(client, project_id)
    assert history(client, project_id)["redo_count"] == 1
    before = history(client, project_id)
    client.patch(f"/api/regions/{regions[0]['id']}", json={"font_size": regions[0]["font_size"]})
    assert history(client, project_id) == before
    stale = before["undo_id"]
    client.patch(f"/api/regions/{regions[0]['id']}", json={"font_size": 45})
    assert history(client, project_id)["redo_count"] == 0
    conflict = client.post(f"/api/projects/{project_id}/history/undo", json={"expected_entry_id": stale})
    assert conflict.status_code == 409
    assert rows(client, page)[0]["font_size"] == 45


def test_history_cannot_mutate_a_page_during_a_task_and_is_limited_and_deduplicated(client, repair_provider):
    page, regions = setup_page(client)
    project_id = page["project_id"]
    head = history(client, project_id)["undo_id"]
    with SessionLocal() as db:
        task = ProcessingTask(project_id=project_id, image_id=page["id"], task_type="ocr", status="PAUSED", payload={})
        db.add(task)
        db.commit()
        task_id = task.id
    assert client.post(f"/api/projects/{project_id}/history/undo", json={"expected_entry_id": head}).status_code == 409
    assert client.patch(f"/api/regions/{regions[0]['id']}", json={"font_size": 70}).status_code == 409
    with SessionLocal() as db:
        db.get(ProcessingTask, task_id).status = "CANCELLED"
        db.commit()
    for size in range(10, 115):
        assert client.patch(f"/api/regions/{regions[0]['id']}", json={"font_size": size}).status_code == 200
    assert history(client, project_id)["undo_count"] == 100
    assets = get_storage().project_dir(project_id) / "history" / "assets"
    assert len(list(assets.iterdir())) == 1  # One original asset, not 100 copies.


@pytest.mark.parametrize("failed", [False, True])
def test_repair_undo_restores_both_frames_and_manual_mask_even_after_failure(client, repair_provider, failed):
    page, regions = setup_page(client)
    project_id, region_id = page["project_id"], regions[0]["id"]
    client.put(f"/api/regions/{region_id}/mask", files={"file": ("mask.png", png(mask=True), "image/png")})
    before = client.patch(
        f"/api/regions/{region_id}",
        json={
            "translated_polygon": [[30, 30], [70, 40], [60, 95], [25, 80]],
            "rotation": -17,
            "perspective_warp": True,
        },
    ).json()
    mask = client.get(before["mask_url"]).content
    repair_provider.fail = failed
    task = wait_task(
        client,
        client.post(
            f"/api/regions/{region_id}/inpaint",
            json={"force": True, "options": {"repair_geometry": "translated", "text_segmentation": False}},
        ).json(),
    )
    assert task["status"] == ("FAILED" if failed else "COMPLETED"), task
    after = rows(client, page)[0]
    calls = repair_provider.calls
    reverted = apply(client, project_id)["regions"][0]
    for key in (
        "polygon",
        "bbox",
        "translated_polygon",
        "translated_bbox",
        "rotation",
        "perspective_warp",
        "layout_data",
    ):
        assert reverted[key] == before[key]
    assert client.get(reverted["mask_url"]).content == mask
    restored = apply(client, project_id, "redo")["regions"][0]
    for key in ("polygon", "bbox", "translated_polygon", "translated_bbox", "rotation", "perspective_warp"):
        assert restored[key] == after[key]
    assert repair_provider.calls == calls


def test_startup_closes_interrupted_task_checkpoint_so_undo_is_not_permanently_blocked(client, repair_provider):
    page, regions = setup_page(client)
    project_id = page["project_id"]
    with SessionLocal() as db:
        task = ProcessingTask(
            project_id=project_id,
            image_id=page["id"],
            task_type="ocr",
            status="RUNNING",
            current_stage="ocr",
            payload={},
        )
        db.add(task)
        db.commit()
        entry_id = begin_task_history(db, task)
        db.get(TextRegion, regions[0]["id"]).source_text = "partially committed OCR"
        task.page.status = "OCR_RUNNING"
        task.page.current_stage = "ocr"
        db.commit()
    assert client.get(f"/api/projects/{project_id}/history").json()["pending"] is True
    TaskManager().recover_interrupted()
    assert history(client, project_id)["undo_id"] == entry_id
    reverted = apply(client, project_id)
    assert reverted["regions"][0]["source_text"] == regions[0]["source_text"]
    assert reverted["page"]["current_stage"] is None
    assert apply(client, project_id, "redo")["regions"][0]["source_text"] == "partially committed OCR"


def test_order_history_keeps_current_page_and_ocr_exemption_is_reversible(client, repair_provider):
    page, _ = setup_page(client)
    project_id = page["project_id"]
    other = client.post(f"/api/projects/{project_id}/images", files={"files": ("b.png", png(), "image/png")}).json()[0]
    client.post(f"/api/projects/{project_id}/images/order", json=[other["id"], page["id"]])
    state = history(client, project_id)
    response = client.post(
        f"/api/projects/{project_id}/history/undo",
        json={"expected_entry_id": state["undo_id"], "current_image_id": page["id"]},
    )
    assert response.status_code == 200 and response.json()["page"]["id"] == page["id"]
    response = client.patch(f"/api/images/{page['id']}/ocr-exempt?exempt=true")
    assert response.status_code == 200, response.text
    assert response.json()["ocr_exempt"] is True
    assert apply(client, project_id)["page"]["ocr_exempt"] is False
    assert apply(client, project_id, "redo")["page"]["ocr_exempt"] is True
