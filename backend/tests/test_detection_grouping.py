from pathlib import Path

import cv2
import numpy as np
from app.services.base import DetectionResult
from app.services.detection import group_text_lines


def result(bbox: list[float], orientation: str = "vertical") -> DetectionResult:
    x, y, width, height = bbox
    return DetectionResult(
        polygon=[[x, y], [x + width, y], [x + width, y + height], [x, y + height]],
        bbox=bbox,
        confidence=0.8,
        orientation=orientation,
    )


def test_groups_vertical_columns_in_one_bubble() -> None:
    grouped = group_text_lines(
        [
            result([542, 107, 19, 93]),
            result([522, 107, 19, 117]),
            result([488, 164, 23, 99]),
            result([438, 360, 23, 107]),
            result([461, 362, 20, 63]),
        ]
    )

    assert len(grouped) == 2
    assert grouped[0].bbox == [488, 107, 73, 156]
    assert grouped[0].metadata["line_grouping"]["source_count"] == 3
    assert grouped[1].bbox == [438, 360, 43, 107]


def test_groups_horizontal_rows_but_not_different_size_subtitle() -> None:
    grouped = group_text_lines(
        [
            result([230, 514, 115, 61], "horizontal"),
            result([259, 551, 298, 76], "horizontal"),
            result([379, 633, 168, 25], "horizontal"),
        ]
    )

    assert len(grouped) == 2
    assert grouped[0].bbox == [230, 514, 327, 113]
    assert grouped[1].bbox == [379, 633, 168, 25]


def test_does_not_group_neighboring_bubbles_without_axis_overlap() -> None:
    grouped = group_text_lines(
        [
            result([500, 100, 22, 100]),
            result([470, 260, 22, 100]),
        ]
    )

    assert len(grouped) == 2


def test_does_not_group_stylized_lines_on_textured_background(tmp_path: Path) -> None:
    image_path = tmp_path / "textured.png"
    rng = np.random.default_rng(17)
    image = rng.integers(0, 255, size=(180, 360, 3), dtype=np.uint8)
    cv2.imwrite(str(image_path), image)

    grouped = group_text_lines(
        [
            result([20, 40, 140, 55], "horizontal"),
            result([50, 78, 280, 70], "horizontal"),
        ],
        image_path,
    )

    assert len(grouped) == 2
