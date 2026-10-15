from __future__ import annotations

import pytest
from test_editor_history import apply, history, rows
from test_repair_geometry_sync import setup_page


@pytest.mark.parametrize("removed", [[0], [1], [3], [0, 2], [0, 1, 2, 3]])
def test_deletion_compacts_names_in_the_same_reversible_edit(client, removed):
    page, initial = setup_page(client, 4)
    other_page, other_regions = setup_page(client, 1)
    before = history(client, page["project_id"])
    ids = [initial[index]["id"] for index in removed]
    if len(ids) == 1:
        response = client.delete(f"/api/regions/{ids[0]}")
    else:
        response = client.post("/api/regions/bulk-delete", json=ids)
    assert response.status_code == 200, response.text
    assert response.json()["rebuild_task"] is None
    expected = [item for index, item in enumerate(initial) if index not in removed]
    remaining = rows(client, page)
    assert [item["id"] for item in remaining] == [item["id"] for item in expected]
    assert [item["region_key"] for item in remaining] == [f"R{index:03d}" for index in range(1, len(expected) + 1)]
    for current, original in zip(remaining, expected, strict=True):
        for field in ("id", "bbox", "polygon", "translated_bbox", "translated_polygon", "reading_order",
                      "source_text", "translated_text", "font_size", "locked", "mask_url"):
            assert current[field] == original[field]
        if current["region_key"] != original["region_key"]:
            assert response.json()["region_keys"][current["id"]] == current["region_key"]
    assert history(client, page["project_id"])["undo_count"] == before["undo_count"] + 1
    restored = apply(client, page["project_id"])["regions"]
    assert {item["id"]: item["region_key"] for item in restored} == {item["id"]: item["region_key"] for item in initial}
    redone = apply(client, page["project_id"], "redo")["regions"]
    assert {item["id"]: item["region_key"] for item in redone} == {item["id"]: item["region_key"] for item in remaining}
    assert rows(client, other_page)[0]["region_key"] == other_regions[0]["region_key"]
    created = client.post(f"/api/images/{page['id']}/regions", json={"bbox": [1, 1, 10, 10]}).json()
    assert created["region_key"] == f"R{len(remaining) + 1:03d}"


def test_legacy_gaps_and_four_digit_numbers_are_compacted_numerically(client):
    page, _ = setup_page(client, 0)
    keys = ["R1000", "R001", "R999", "R010"]
    initial = [client.post(f"/api/images/{page['id']}/regions", json={
        "region_key": key, "bbox": [index * 20, 10, 10, 10], "reading_order": index,
    }).json() for index, key in enumerate(keys)]
    response = client.delete(f"/api/regions/{initial[1]['id']}")
    assert response.status_code == 200, response.text
    by_id = {item["id"]: item["region_key"] for item in rows(client, page)}
    assert by_id == {initial[3]["id"]: "R001", initial[2]["id"]: "R002", initial[0]["id"]: "R003"}


def test_merge_also_compacts_names_without_renaming_region_identities(client):
    page, initial = setup_page(client, 4)
    response = client.post("/api/regions/merge", json=[initial[0]["id"], initial[2]["id"]])
    assert response.status_code == 200, response.text
    remaining = rows(client, page)
    assert {item["id"]: item["region_key"] for item in remaining} == {
        initial[0]["id"]: "R001", initial[1]["id"]: "R002", initial[3]["id"]: "R003",
    }
    assert response.json()["region_keys"] == {initial[3]["id"]: "R003"}
    assert len(apply(client, page["project_id"])["regions"]) == 4
