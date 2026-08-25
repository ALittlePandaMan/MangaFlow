import hashlib
from pathlib import Path

import cv2
import numpy as np
import pytest
from app.services.detection.artifacts import load_balloon_mask, persist_balloon_mask
from app.storage.files import StorageManager


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
        parent_instance_id=None,
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
        parent_instance_id=None,
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
        parent_instance_id=None,
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
        parent_instance_id=None,
    )
    second = persist_balloon_mask(
        storage,
        page_directory,
        instance_id="bubble-stable",
        mask=second_mask,
        origin=(4, 6),
        image_shape=(40, 50),
        confidence=0.9,
        parent_instance_id=None,
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
            parent_instance_id=None,
        )

    entry = persist_balloon_mask(
        storage,
        page_directory,
        instance_id="bubble-1",
        mask=compact,
        origin=(4, 6),
        image_shape=(40, 50),
        confidence=0.9,
        parent_instance_id=None,
    )
    entry["origin"] = [45, 35]
    with pytest.raises(ValueError, match="escapes the source image"):
        load_balloon_mask(storage, page_directory, entry, image_shape=(40, 50))
