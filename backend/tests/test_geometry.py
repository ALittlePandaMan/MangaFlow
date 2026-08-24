import pytest
from app.utils.geometry import (
    bbox_to_polygon,
    intersection_area,
    order_quadrilateral,
    perspective_coefficients,
    polygon_to_bbox,
    reading_order_japanese,
)


def test_polygon_bbox_roundtrip() -> None:
    bbox = [10.0, 20.0, 80.0, 120.0]
    assert polygon_to_bbox(bbox_to_polygon(bbox)) == bbox


def test_japanese_reading_order_right_to_left_then_top_to_bottom() -> None:
    regions = [
        {"bbox": [100, 100, 30, 50]},  # right lower
        {"bbox": [20, 20, 30, 50]},  # left upper
        {"bbox": [102, 10, 28, 50]},  # right upper
        {"bbox": [21, 120, 32, 50]},  # left lower
    ]
    assert reading_order_japanese(regions) == [2, 0, 1, 3]


def test_intersection_area() -> None:
    assert intersection_area([0, 0, 10, 10], [5, 5, 10, 10]) == 25
    assert intersection_area([0, 0, 2, 2], [4, 4, 2, 2]) == 0


def test_quadrilateral_order_normalizes_unordered_corners() -> None:
    assert order_quadrilateral([[90, 70], [10, 20], [20, 80], [80, 10]]) == [
        [10.0, 20.0],
        [80.0, 10.0],
        [90.0, 70.0],
        [20.0, 80.0],
    ]


def test_quadrilateral_order_rejects_concave_or_degenerate_geometry() -> None:
    assert order_quadrilateral([[0, 0], [100, 0], [30, 20], [0, 100]]) is None
    assert order_quadrilateral([[0, 0], [50, 0], [100, 0], [0, 100]]) is None
    assert order_quadrilateral([[0, 0], [100, 0], [100, 100], [0, 0]]) is None


def test_perspective_coefficients_map_destination_back_to_source() -> None:
    destination = [[10.0, 20.0], [110.0, 5.0], [90.0, 85.0], [20.0, 100.0]]
    source = [[0.0, 0.0], [79.0, 0.0], [79.0, 49.0], [0.0, 49.0]]
    coefficients = perspective_coefficients(destination, source)
    a, b, c, d, e, f, g, h = coefficients
    for (x, y), (expected_x, expected_y) in zip(destination, source, strict=True):
        denominator = g * x + h * y + 1
        assert (a * x + b * y + c) / denominator == pytest.approx(expected_x)
        assert (d * x + e * y + f) / denominator == pytest.approx(expected_y)
