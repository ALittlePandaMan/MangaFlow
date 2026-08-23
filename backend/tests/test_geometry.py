from app.utils.geometry import bbox_to_polygon, intersection_area, polygon_to_bbox, reading_order_japanese


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
