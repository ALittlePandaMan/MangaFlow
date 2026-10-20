import hashlib
import shutil
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import pytest
from app.services.detection.artifacts import load_balloon_mask, persist_balloon_mask
from app.storage.files import StorageManager


def test_checkpoint_balloon_artifact_is_accepted_only_within_its_page_and_checksum(tmp_path):
    storage = StorageManager(tmp_path/"data")
    page = storage.page_dir("project","page")
    output = page/"workflows"/str(uuid4())/str(uuid4())
    entry = persist_balloon_mask(storage,output,instance_id="bubble",mask=np.ones((10,12),np.uint8),
        origin=(2,3),image_shape=(40,50),confidence=.99)
    loaded = load_balloon_mask(storage,page,entry,image_shape=(40,50))
    assert loaded.shape==(40,50) and cv2.countNonZero(loaded)==120
    with pytest.raises(ValueError,match="does not belong"):
        load_balloon_mask(storage,storage.page_dir("project","another-page"),entry,image_shape=(40,50))
    with pytest.raises(ValueError,match="does not belong"):
        load_balloon_mask(storage,page,{**entry,"sha256":None},image_shape=(40,50))
    path = storage.absolute(entry["path"])
    path.write_bytes(b"changed after checkpoint")
    with pytest.raises(ValueError,match="checksum"):
        load_balloon_mask(storage,page,entry,image_shape=(40,50))


def test_balloon_mask_png_round_trip_is_pixel_exact(tmp_path: Path) -> None:
    storage = StorageManager(tmp_path / "data")
    page_directory = storage.page_dir("project", "page")
    compact = np.zeros((37, 53), dtype=np.uint8)
    cv2.ellipse(compact, (26, 18), (23, 15), 17, 0, 360, 255, -1)
    cv2.circle(compact, (19, 15), 5, 0, -1)
    cv2.rectangle(compact, (26, 5), (31, 11), 73, -1)

    entry = persist_balloon_mask(
        storage,
        page_directory,
        instance_id="bubble:ellipse/1",
        mask=compact,
        origin=(29, 41),
        image_shape=(120, 140),
        confidence=0.93726,
    )
    restored = load_balloon_mask(
        storage,
        page_directory,
        entry,
        image_shape=(120, 140),
    )

    expected = np.zeros((120, 140), dtype=np.uint8)
    expected[41:78, 29:82] = np.where(compact > 0, 255, 0).astype(np.uint8)
    assert np.array_equal(restored, expected)
    assert entry["origin"] == [29, 41]
    assert entry["size"] == [53, 37]
    assert entry["confidence"] == 0.9373
    assert entry["kind"] == "instance" and entry["parent_bubble_id"] is None
    # Retired split metadata in existing projects remains readable, even though
    # the unified detector only creates independent balloon instances now.
    legacy = {**entry, "kind": "split_child", "parent_bubble_id": "retired-parent"}
    assert np.array_equal(load_balloon_mask(storage, page_directory, legacy, image_shape=(120, 140)), expected)
    assert storage.absolute(entry["path"]).name.startswith("bubble_ellipse_1-")


def test_balloon_mask_rejects_corrupted_png(tmp_path: Path) -> None:
    storage = StorageManager(tmp_path / "data")
    page_directory = storage.page_dir("project", "page")
    entry = persist_balloon_mask(
        storage,
        page_directory,
        instance_id="bubble-1",
        mask=np.full((8, 12), 255, dtype=np.uint8),
        origin=(4, 6),
        image_shape=(40, 50),
        confidence=0.9,
    )
    storage.absolute(entry["path"]).write_bytes(b"not-a-png")

    with pytest.raises(ValueError, match="checksum mismatch"):
        load_balloon_mask(storage, page_directory, entry, image_shape=(40, 50))


def test_balloon_mask_rejects_checksum_valid_empty_png(tmp_path: Path) -> None:
    storage = StorageManager(tmp_path / "data")
    page_directory = storage.page_dir("project", "page")
    entry = persist_balloon_mask(
        storage,
        page_directory,
        instance_id="bubble-empty",
        mask=np.full((8, 12), 255, dtype=np.uint8),
        origin=(4, 6),
        image_shape=(40, 50),
        confidence=0.9,
    )
    path = storage.absolute(entry["path"])
    assert cv2.imwrite(str(path), np.zeros((8, 12), dtype=np.uint8))
    entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="file is empty"):
        load_balloon_mask(storage, page_directory, entry, image_shape=(40, 50))


def test_changed_balloon_mask_keeps_previous_content_addressed_artifact(tmp_path: Path) -> None:
    storage = StorageManager(tmp_path / "data")
    page_directory = storage.page_dir("project", "page")
    first_mask = np.zeros((20, 24), dtype=np.uint8)
    second_mask = np.zeros_like(first_mask)
    cv2.circle(first_mask, (8, 10), 6, 255, -1)
    cv2.circle(second_mask, (15, 10), 6, 255, -1)

    first = persist_balloon_mask(
        storage,
        page_directory,
        instance_id="bubble-stable",
        mask=first_mask,
        origin=(4, 6),
        image_shape=(40, 50),
        confidence=0.9,
    )
    second = persist_balloon_mask(
        storage,
        page_directory,
        instance_id="bubble-stable",
        mask=second_mask,
        origin=(4, 6),
        image_shape=(40, 50),
        confidence=0.9,
    )

    assert first["path"] != second["path"]
    assert storage.absolute(first["path"]).is_file()
    assert storage.absolute(second["path"]).is_file()
    assert not np.array_equal(
        load_balloon_mask(storage, page_directory, first, image_shape=(40, 50)),
        load_balloon_mask(storage, page_directory, second, image_shape=(40, 50)),
    )


def test_balloon_mask_rejects_geometry_outside_page(tmp_path: Path) -> None:
    storage = StorageManager(tmp_path / "data")
    page_directory = storage.page_dir("project", "page")
    compact = np.full((8, 12), 255, dtype=np.uint8)

    with pytest.raises(ValueError, match="escapes the source image"):
        persist_balloon_mask(
            storage,
            page_directory,
            instance_id="bubble-1",
            mask=compact,
            origin=(39, 33),
            image_shape=(40, 50),
            confidence=0.9,
        )

    entry = persist_balloon_mask(
        storage,
        page_directory,
        instance_id="bubble-1",
        mask=compact,
        origin=(4, 6),
        image_shape=(40, 50),
        confidence=0.9,
    )
    entry["origin"] = [45, 35]
    with pytest.raises(ValueError, match="escapes the source image"):
        load_balloon_mask(storage, page_directory, entry, image_shape=(40, 50))


def archived_balloon(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    page_directory = storage.page_dir("project", "page")
    compact = np.zeros((20, 24), dtype=np.uint8)
    cv2.ellipse(compact, (12, 10), (10, 8), 0, 0, 360, 255, -1)
    entry = persist_balloon_mask(storage, page_directory, instance_id="bubble-history",
        mask=compact, origin=(4, 6), image_shape=(40, 50), confidence=.95)
    live = load_balloon_mask(storage, page_directory, entry, image_shape=(40, 50))
    directory = storage.project_dir("project") / "history" / "assets"
    directory.mkdir(parents=True, exist_ok=True)
    archived = directory / f"{entry['sha256']}.png"
    shutil.copyfile(storage.absolute(entry["path"]), archived)
    entry = {**entry, "path": storage.relative(archived)}
    return storage, page_directory, entry, live


def test_undo_archived_balloon_mask_keeps_the_same_pixel_constraint(tmp_path):
    storage, directory, entry, expected = archived_balloon(tmp_path)
    restored = load_balloon_mask(storage, directory, entry, image_shape=(40, 50))
    assert np.array_equal(restored, expected)


@pytest.mark.parametrize("project", ["other-project", "project"])
def test_history_balloon_rejects_cross_project_and_non_content_addressed_paths(tmp_path, project):
    storage, directory, entry, _ = archived_balloon(tmp_path)
    other = storage.project_dir(project) / "history" / "assets"
    other.mkdir(parents=True, exist_ok=True)
    name = f"{entry['sha256']}.png" if project != "project" else "arbitrary.png"
    path = other / name
    shutil.copyfile(storage.absolute(entry["path"]), path)
    entry["path"] = storage.relative(path)
    with pytest.raises(ValueError, match="does not belong"):
        load_balloon_mask(storage, directory, entry, image_shape=(40, 50))


def test_archived_balloon_requires_and_checks_its_checksum(tmp_path):
    storage, directory, entry, _ = archived_balloon(tmp_path)
    missing_checksum = {key: value for key, value in entry.items() if key != "sha256"}
    with pytest.raises(ValueError, match="does not belong"):
        load_balloon_mask(storage, directory, missing_checksum, image_shape=(40, 50))
    storage.absolute(entry["path"]).write_bytes(b"corrupted archive")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_balloon_mask(storage, directory, entry, image_shape=(40, 50))
