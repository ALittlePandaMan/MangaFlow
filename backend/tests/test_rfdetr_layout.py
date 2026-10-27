from __future__ import annotations

import base64
import zlib
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
from app.services.base import ProviderError
from app.services.detection.classification import LayoutTextClassifier
from app.services.detection.layout import (
    TEXT_BOX_PADDING,
    RFDetrTextDetector,
    build_layout_regions,
    layout_options,
    mask_containment,
    text_geometry,
)
from app.services.detection.layout_protocol import decode_predictions, encode_predictions
from app.services.detection.text_roles import LayoutPrediction, partition_text_roles
from PIL import Image


def prediction(label="text", box=(10, 10, 12, 24), confidence=.95, mask=None):
    x, y, w, h = box
    return LayoutPrediction(label, confidence, list(box), np.ones((h, w), np.uint8) if mask is None else mask, (x, y))


def build(items, config=None):
    return build_layout_regions(items, 200, 200, config or {}, page_key="page-1")


def test_roundtrip_packed_crop_protocol_preserves_exact_geometry_and_binary_masks():
    items = [prediction(), prediction("bubble", (0, 0, 60, 80)), prediction("panel", (0, 0, 150, 150))]
    items[0].mask[2:4, 1:3] = 0
    width, height, decoded = decode_predictions(encode_predictions(items, 200, 200))
    assert (width, height) == (200, 200)
    for source, target in zip(items, decoded, strict=True):
        assert source.label == target.label and source.bbox == target.bbox and source.mask_origin == target.mask_origin
        np.testing.assert_array_equal(source.mask, target.mask)


@pytest.mark.parametrize("empty_label", ["text", "bubble"])
def test_empty_segmentation_preserves_ocr_box_without_inventing_bubble_association(empty_label):
    text, bubble = prediction(), prediction("bubble", (0, 0, 60, 80))
    item = text if empty_label == "text" else bubble
    item.mask, item.mask_origin = np.empty((0, 0), np.uint8), (0, 0)
    _, _, decoded = decode_predictions(encode_predictions([text, bubble], 200, 200))
    results = build(decoded)
    assert len(results) == 1
    assert results[0].bbox == [7., 7., 18., 30.]
    assert results[0].metadata["raw_bbox"] == text.bbox
    assert results[0].orientation == "vertical"
    assert results[0].bubble_id is None and results[0].balloon_mask is None
    kept, _ = partition_text_roles(results)
    assert kept == results


@pytest.mark.parametrize("origin,shape", [([0, 0], [0, 1]), ([0, 0], [1, 0]), ([1, 0], [0, 0]),
                                         ([0, 1], [0, 0]), ([0, 0], [-1, -1])])
def test_empty_crop_support_does_not_accept_malformed_mask_geometry(origin, shape):
    response = encode_predictions([prediction()], 200, 200)
    response["detections"][0].update(origin=origin, size=shape)
    with pytest.raises(ValueError, match="layout_mask_out_of_bounds"):
        decode_predictions(response)


@pytest.mark.parametrize("damage", ["extra_bytes", "truncated", "negative_origin", "huge_size", "nan_box", "too_many"])
def test_rejects_invalid_or_unbounded_worker_payloads(damage):
    response = encode_predictions([prediction()], 200, 200)
    item = response["detections"][0]
    if damage == "extra_bytes":
        item["mask"] = base64.b64encode(zlib.compress(bytes(1000))).decode()
    elif damage == "truncated":
        item["mask"] = base64.b64encode(zlib.compress(bytes(36))[:-1]).decode()
    elif damage == "negative_origin":
        item["origin"] = [-1, 0]
    elif damage == "huge_size":
        item["size"] = [20000, 20000]
    elif damage == "nan_box":
        item["bbox"][0] = float("nan")
    else:
        response["detections"] *= 201
    with pytest.raises((ValueError, TypeError)):
        decode_predictions(response)


def test_same_bubble_does_not_merge_two_independent_text_instances():
    results = build([prediction(box=(10, 10, 12, 24)), prediction(box=(35, 15, 12, 24)), prediction("bubble", (0, 0, 65, 70))])
    assert len(results) == 2
    assert results[0].bubble_id == results[1].bubble_id is not None
    assert results[0].bbox != results[1].bbox
    assert all(result.metadata["independent_text_instance"] for result in results)
    assert all("line_grouping" not in result.metadata for result in results)
    assert results[0].balloon_mask is results[1].balloon_mask


def test_adjacent_bubbles_keep_distinct_ids_even_when_bounding_boxes_overlap():
    left = prediction("bubble", (0, 0, 80, 80))
    right = prediction("bubble", (40, 0, 80, 80))
    left.mask[:, 55:] = 0
    right.mask[:, :20] = 0
    results = build([prediction(box=(10, 20, 12, 24)), prediction(box=(80, 20, 12, 24)), left, right])
    assert len(results) == 2
    assert results[0].bubble_id and results[1].bubble_id and results[0].bubble_id != results[1].bubble_id


def test_association_uses_foreground_pixels_not_the_text_rectangle():
    text = prediction(box=(10, 10, 80, 24))
    text.mask[:, 10:] = 0
    bubble = prediction("bubble", (0, 0, 25, 45))
    assert mask_containment(text, bubble) == 1
    result = build([text, bubble])[0]
    assert result.bubble_id
    assert result.metadata["balloon_assignment"]["coverage"] == 1


def test_smallest_containing_bubble_wins_and_less_than_90_percent_does_not_attach():
    text = prediction()
    small, large = prediction("bubble", (5, 5, 30, 40)), prediction("bubble", (0, 0, 100, 100))
    result = build([text, small, large])[0]
    np.testing.assert_array_equal(result.balloon_mask, small.mask)
    small.mask[:, 5:10] = 0
    assert build([text, small])[0].bubble_id is None


def test_duplicate_text_is_removed_without_combining_distinct_text_or_panel_instances():
    results = build([prediction(), prediction(confidence=.7), prediction(box=(50, 10, 12, 24)), prediction("panel", (0, 0, 180, 180))])
    assert len(results) == 2
    assert all(result.metadata["panel"]["method"] == "rfdetr" for result in results)


def test_native_sfx_never_becomes_ocr_text_even_inside_a_bubble_or_with_legacy_overrides():
    outside = prediction("onomatopoeia", (100, 10, 12, 24))
    inside = prediction("onomatopoeia")
    results = build([outside, inside, prediction("bubble", (0, 0, 50, 60))])
    assert all(item.metadata["text_classification"]["excluded"] for item in results)
    for config in ({}, {"text_classification": False}, {"text_classification": {"filter_sfx": False}}):
        low_confidence = prediction("onomatopoeia", confidence=.35)
        kept, report = partition_text_roles(build([outside, low_confidence], config))
        assert kept == [] and report["excluded_count"] == 2


def test_cross_class_duplicate_keeps_exactly_one_translatable_text_and_mask_region():
    items = [prediction(), prediction("onomatopoeia", confidence=.8), prediction("bubble", (0, 0, 50, 60))]
    kept, report = partition_text_roles(build(items))
    assert len(kept) == 1 and report["excluded_count"] == 1
    assert kept[0].metadata["text_classification"]["label"] == "text"
    assert kept[0].bubble_id


def test_balloon_containment_ignores_stray_foreground_outside_detection_bounds():
    mask = np.zeros((50, 50), np.uint8)
    mask[10:20, 10:20] = 1
    mask[40:42, 40:50] = 1
    text = LayoutPrediction("text", .95, [10, 10, 10, 10], mask, (0, 0))
    balloon = prediction("bubble", (5, 5, 30, 30))
    assert mask_containment(text, balloon) == 1
    assert build([text, balloon])[0].bubble_id
    text.bbox = [30, 0, 5, 5]  # no foreground inside the box
    assert mask_containment(text, balloon) == 0


def test_reading_order_keeps_bubble_children_together_despite_interleaved_columns():
    items = [prediction(box=(100, 10, 10, 20)), prediction(box=(40, 60, 10, 20)),
             prediction(box=(70, 120, 10, 20)), prediction("bubble", (10, 0, 110, 100)),
             prediction("bubble", (65, 110, 30, 50))]
    results = build(list(reversed(items)))
    assert [r.metadata["raw_bbox"][:2] for r in results] == [[100., 10.], [40., 60.], [70., 120.]]
    assert results[0].bubble_id == results[1].bubble_id != results[2].bubble_id


def test_layout_order_uses_panel_then_bubble_ancestry_and_geometry_not_scores():
    items = [prediction(box=(100, 80, 10, 20), confidence=.7), prediction(box=(10, 10, 10, 20)),
             prediction("bubble", (90, 50, 40, 60)), prediction("bubble", (0, 5, 40, 40)),
             prediction("panel", (85, 0, 100, 160)), prediction("panel", (0, 0, 80, 160))]
    results = build(items)
    assert [r.metadata["raw_bbox"][0] for r in results] == [100, 10]
    assert results[0].metadata["panel"]["bbox"] == [85, 0, 100, 160]
    assert results[1].metadata["panel"]["bbox"] == [0, 0, 80, 160]


def test_orientation_uses_line_projection_instead_of_the_whole_block_aspect_ratio():
    mask = np.zeros((100, 60), np.uint8)
    for y in (2, 22, 42, 62, 82):
        mask[y:y+4, 3:57] = 1
    polygon, orientation, angle = text_geometry(prediction(box=(10, 10, 60, 100), mask=mask), 200, 200)
    assert orientation == "horizontal" and angle == 0 and len(polygon) == 4
    _, orientation, angle = text_geometry(prediction(box=(10, 10, 100, 60), mask=mask.T), 200, 200)
    assert orientation == "vertical" and angle == 0


@pytest.mark.parametrize("origin", [(0, 0), (70, 40)])
def test_typography_ignores_disconnected_foreground_outside_text_window(origin):
    clean = np.zeros((160, 240), np.uint8)
    for x in (12, 28, 44):
        clean[12:80, x:x+3] = 1
    noisy = clean.copy()
    for y in (100, 116, 132):
        noisy[y:y+3, 5:235] = 1  # much more horizontal ink, but outside this text
    x, y = origin
    bbox = [x+8, y+8, 45, 76]
    expected = text_geometry(LayoutPrediction("text", .95, bbox, clean, origin), 400, 300)
    actual = text_geometry(LayoutPrediction("text", .95, bbox, noisy, origin), 400, 300)
    assert actual == expected
    assert actual[1:] == ("vertical", 0.)


def test_typography_falls_back_to_box_direction_when_its_window_has_no_foreground():
    mask = np.ones((20, 100), np.uint8)
    item = LayoutPrediction("text", .95, [10, 10, 20, 70], mask, (60, 100))
    polygon, orientation, angle = text_geometry(item, 200, 200)
    assert orientation == "vertical" and angle == 0
    assert polygon == [[10., 10.], [30., 10.], [30., 80.], [10., 80.]]


def test_detection_pads_each_edge_once_without_mutating_model_predictions():
    text = prediction(box=(10, 20, 20, 50))
    original_mask = text.mask.copy()
    first, second = build([text])[0], build([text])[0]
    assert first.bbox == second.bbox == [7., 17., 26., 56.]
    assert first.polygon == [[7., 17.], [33., 17.], [33., 73.], [7., 73.]]
    assert first.metadata["text_box_padding"] == dict.fromkeys(("top", "bottom", "left", "right"), TEXT_BOX_PADDING)
    assert first.metadata["raw_bbox"] == text.bbox == [10, 20, 20, 50]
    np.testing.assert_array_equal(text.mask, original_mask)


@pytest.mark.parametrize("padding", [0, 1, 8, 50])
def test_configured_padding_only_changes_final_frame(padding):
    text = prediction(box=(70, 70, 20, 50))
    bubble = prediction("bubble", (10, 10, 180, 180))
    result, = build([text, bubble], {"text_box_padding": padding})
    assert result.bbox == [70-padding, 70-padding, 20+2*padding, 50+2*padding]
    assert result.metadata["text_box_padding"] == dict.fromkeys(("top", "bottom", "left", "right"), padding)
    assert result.metadata["raw_bbox"] == text.bbox
    assert result.metadata["balloon_assignment"]["coverage"] == 1
    np.testing.assert_array_equal(result.text_mask, text.mask)
    assert result.balloon_mask is bubble.mask


@pytest.mark.parametrize("padding", [0, 8, 50])
def test_configured_padding_preserves_duplicate_and_sfx_decisions(padding):
    first, second = prediction(box=(10, 10, 2, 8)), prediction(box=(12, 10, 2, 8))
    bubble = prediction("bubble", (10, 10, 4, 8))
    sfx = prediction("onomatopoeia", (80, 80, 12, 24))
    results = build([first, second, bubble, sfx], {"text_box_padding": padding})
    text = [r for r in results if not r.metadata["text_classification"]["excluded"]]
    assert len(text) == 2
    assert text[0].bubble_id == text[1].bubble_id is not None
    excluded, = [r for r in results if r.metadata["text_classification"]["excluded"]]
    assert excluded.bbox == sfx.bbox


@pytest.mark.parametrize("padding", [-1, 51, float("nan"), float("inf")])
def test_invalid_padding_is_rejected_before_model_loading(padding):
    with pytest.raises(ValueError, match="0–50"):
        layout_options({"text_box_padding": padding})


@pytest.mark.parametrize("side", ["top", "bottom", "left", "right"])
def test_each_padding_side_moves_only_its_own_edge(side):
    margins = dict.fromkeys(("top", "bottom", "left", "right"), 0)
    margins[side] = 7
    result, = build([prediction(box=(70, 70, 20, 50))], {"text_box_padding": margins})
    assert result.bbox == [70-margins["left"], 70-margins["top"],
                           20+margins["left"]+margins["right"], 50+margins["top"]+margins["bottom"]]
    assert result.metadata["text_box_padding"] == margins


@pytest.mark.parametrize("angle", [-12, 12])
def test_asymmetric_padding_keeps_slanted_edges_parallel_in_local_axes(angle):
    import cv2

    mask = np.zeros((140, 240), np.uint8)
    for y in (45, 65, 85):
        mask[y:y+3, 40:200] = 1
    mask = cv2.warpAffine(mask, cv2.getRotationMatrix2D((120, 70), -angle, 1), (240, 140), flags=cv2.INTER_NEAREST)
    item = LayoutPrediction("text", .95, [120, 130, 160, 80], mask, (80, 100))
    polygon, orientation, detected_angle = text_geometry(item, 500, 500, padding={"top": 0, "bottom": 9, "left": 7, "right": 2})
    original, original_orientation, original_angle = text_geometry(item, 500, 500)
    assert (orientation, detected_angle) == (original_orientation, original_angle)
    rad = np.radians(detected_angle)
    rotation = np.array([[np.cos(rad), -np.sin(rad)], [np.sin(rad), np.cos(rad)]])
    local = (np.asarray(polygon)-[200, 170])@rotation + [200, 170]
    np.testing.assert_allclose(local, [[113, 130], [282, 130], [282, 219], [113, 219]])
    old_edges = np.roll(original, -1, axis=0)-np.asarray(original)
    new_edges = np.roll(polygon, -1, axis=0)-np.asarray(polygon)
    np.testing.assert_allclose(old_edges/np.linalg.norm(old_edges, axis=1)[:, None],
                               new_edges/np.linalg.norm(new_edges, axis=1)[:, None])


@pytest.mark.parametrize("side", ["top", "bottom", "left", "right"])
@pytest.mark.parametrize("invalid", [-1, 51, None, float("nan")])
def test_each_invalid_side_is_rejected(side, invalid):
    with pytest.raises(ValueError):
        layout_options({"text_box_padding": {side: invalid}})


@pytest.mark.parametrize("box,expected", [
    ((0, 0, 10, 20), [0., 0., 13., 23.]),
    ((190, 180, 10, 20), [187., 177., 13., 23.]),
    ((0, 180, 10, 20), [0., 177., 13., 23.]),
    ((190, 0, 10, 20), [187., 0., 13., 23.]),
])
def test_detection_padding_is_clipped_to_image_bounds(box, expected):
    result = build([prediction(box=box)])[0]
    assert result.bbox == expected
    assert all(0 <= x <= 200 and 0 <= y <= 200 for x, y in result.polygon)


@pytest.mark.parametrize("angle", [-12, 12])
def test_padding_slanted_text_moves_edges_parallel_without_changing_typography(angle):
    import cv2

    mask = np.zeros((140, 240), np.uint8)
    for y in (45, 65, 85):
        mask[y:y+3, 40:200] = 1
    mask = cv2.warpAffine(mask, cv2.getRotationMatrix2D((120, 70), -angle, 1), (240, 140), flags=cv2.INTER_NEAREST)
    item = LayoutPrediction("text", .95, [120, 130, 160, 80], mask, (80, 100))
    original, direction, detected_angle = text_geometry(item, 400, 400)
    padded, padded_direction, padded_angle = text_geometry(item, 400, 400, padding=TEXT_BOX_PADDING)
    assert detected_angle == pytest.approx(angle, abs=.5)
    assert (padded_direction, padded_angle) == (direction, detected_angle)
    old_points, new_points = np.asarray(original), np.asarray(padded)
    np.testing.assert_allclose(old_points.mean(axis=0), new_points.mean(axis=0))
    old_edges = np.roll(old_points, -1, axis=0) - old_points
    new_edges = np.roll(new_points, -1, axis=0) - new_points
    old_lengths = np.linalg.norm(old_edges, axis=1)
    new_lengths = np.linalg.norm(new_edges, axis=1)
    np.testing.assert_allclose(new_lengths, old_lengths + 2*TEXT_BOX_PADDING)
    np.testing.assert_allclose(new_edges/new_lengths[:, None], old_edges/old_lengths[:, None])


def test_padding_does_not_change_deduplication_or_balloon_association():
    first, second = prediction(box=(10, 10, 2, 8)), prediction(box=(12, 10, 2, 8))
    bubble = prediction("bubble", (10, 10, 4, 8))
    results = build([first, second, bubble])
    # Expanded boxes overlap by >50%, but the actual detections are distinct.
    assert len(results) == 2
    assert results[0].bubble_id == results[1].bubble_id is not None
    assert all(result.metadata["balloon_assignment"]["coverage"] == 1 for result in results)
    assert all(result.balloon_mask is bubble.mask for result in results)


def test_padding_does_not_expand_excluded_sfx_candidates():
    sfx = prediction("onomatopoeia")
    result = build([sfx])[0]
    assert result.bbox == sfx.bbox
    assert result.metadata["text_box_padding"] == dict.fromkeys(("top", "bottom", "left", "right"), 0)


@pytest.mark.parametrize("value", [-1, float("nan"), 1.1, .1])
def test_invalid_thresholds_fail_before_loading_model(value):
    with pytest.raises(ValueError):
        layout_options({"text_threshold": value})


@pytest.mark.parametrize("extension,mode", [("png", "RGBA"), ("jpg", "RGB"), ("webp", "RGB")])
def test_provider_accepts_normal_images_and_calls_only_one_layout_model(tmp_path, extension, mode):
    path = tmp_path/f"page.{extension}"
    Image.new(mode, (200, 200), "white").save(path)
    detector = RFDetrTextDetector({"device": "cpu"})
    detector._text_classifier.ensure_loaded = Mock()
    detector._text_classifier.detect_layout = Mock(return_value=(200, 200, [prediction()]))
    assert len(detector.detect(path)) == 1
    detector._text_classifier.detect_layout.assert_called_once_with(path, thresholds=(.25, .2, .5, .5))
    detector.unload()


@pytest.mark.parametrize('mode,extension', [('RGB', 'jpg'), ('RGB', 'webp'), ('RGBA', 'png')])
def test_layout_worker_preserves_image_colors_and_composites_transparency_on_white(tmp_path, mode, extension):
    from app.services.detection.classification_worker import load_layout_image

    path = tmp_path / f'page.{extension}'
    color = (10, 20, 30, 0) if mode == 'RGBA' else (10, 20, 30)
    Image.new(mode, (80, 100), color).save(path)
    original = path.read_bytes()
    loaded = load_layout_image(str(path))
    assert loaded.mode == 'RGB' and loaded.size == (80, 100)
    if mode == 'RGBA':
        assert loaded.getpixel((0, 0)) == (255, 255, 255)
    else:
        assert max(loaded.getpixel((0, 0))) < 50
    assert path.read_bytes() == original


def test_authoritative_detection_errors_are_not_silently_treated_as_empty_pages():
    worker = LayoutTextClassifier({})
    worker._start = Mock(side_effect=RuntimeError("runtime_not_installed"))
    with pytest.raises(ProviderError, match="原有区域保持不变"):
        worker.detect_layout(Path("missing.png"))


def test_actual_empty_detection_is_valid_and_has_no_synthetic_bubble_text_boxes():
    assert decode_predictions(encode_predictions([], 200, 200)) == (200, 200, [])
    assert build([prediction("bubble", (0, 0, 100, 100))]) == []


def test_threshold_changes_share_one_worker_but_keep_independent_postprocessing():
    from app.services.registry import ProviderRegistry
    registry = ProviderRegistry()
    first = registry.create("detection", "rfdetr", {"device": "cpu", "text_threshold": .25})
    second = registry.create("detection", "rfdetr", {"device": "cpu", "text_threshold": .7})
    assert first is not second
    assert first._text_classifier is second._text_classifier
    assert first.config["text_threshold"] == .25 and second.config["text_threshold"] == .7
    first.unload()
    second.unload()


def test_pipeline_persists_independent_texts_and_shared_exact_balloon_without_running_legacy_segmentation(client, monkeypatch):
    from io import BytesIO

    from app.core.database import SessionLocal
    from app.models import ImagePage
    from app.pipeline.processor import PipelineProcessor
    from app.services.detection.artifacts import BUBBLE_GEOMETRY_KEY, load_balloon_mask

    output = BytesIO()
    Image.new("RGB", (200, 200), "white").save(output, "PNG")
    project = client.post("/api/projects", json={"name": "unified-layout-integration"}).json()
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("page.png", output.getvalue(), "image/png")}).json()[0]
    predictions = [prediction(), prediction("onomatopoeia", confidence=.8),
                   prediction(box=(45, 10, 12, 24)), prediction("bubble", (0, 0, 80, 80))]
    detector = RFDetrTextDetector({"device": "cpu"})
    detector._text_classifier.ensure_loaded = Mock()
    detector._text_classifier.detect_layout = Mock(return_value=(200, 200, predictions))
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda *_: (detector, "rfdetr"))
    monkeypatch.setattr("app.pipeline.processor.detect_panel_frames", Mock(side_effect=AssertionError("must use RF-DETR panels")))
    monkeypatch.setattr("app.pipeline.processor.panel_reading_order", Mock(side_effect=AssertionError("must keep RF-DETR traversal")))
    with SessionLocal() as db:
        stored = db.get(ImagePage, page["id"])
        processor = PipelineProcessor(db)
        processor.detect(stored, None, force=True)
        db.commit()
        db.refresh(stored)
        assert len(stored.regions) == 2
        ordered = sorted(stored.regions, key=lambda item: item.reading_order)
        assert [item.bbox[0] for item in ordered] == [42, 7]
        assert [item.region_key for item in ordered] == ["R001", "R002"]
        first, second = stored.regions
        assert first.bubble_id == second.bubble_id and first.bbox != second.bbox
        entry = stored.metadata_json[BUBBLE_GEOMETRY_KEY]["instances"][first.bubble_id]
        exact = load_balloon_mask(processor.storage, processor.storage.page_dir(project["id"], stored.id), entry, image_shape=(200, 200))
        np.testing.assert_array_equal(exact[:80, :80] > 0, predictions[-1].mask > 0)
        assert np.count_nonzero(exact) == 6400
        first.locked = True
        first.source_text, first.translated_text = "locked source", "locked translation"
        first.layout_data = {"detection": {"balloon_grouping_version": 2}}
        db.commit()
        locked_id, replaced_id = first.id, second.id
        processor.detect(stored, None, force=True)
        db.commit()
        db.refresh(stored)
        assert len(stored.regions) == 2
        assert locked_id not in {region.id for region in stored.regions}
        assert replaced_id not in {region.id for region in stored.regions}
        assert all(not region.locked and region.source_text == region.translated_text == '' for region in stored.regions)
        previous_ids = [region.id for region in stored.regions]
        detector._text_classifier.detect_layout.side_effect = ProviderError("model_failed")
        with pytest.raises(ProviderError):
            processor.detect(stored, None, force=True)
        assert [region.id for region in stored.regions] == previous_ids
    detector.unload()


@pytest.mark.parametrize("in_balloon", [False, True])
@pytest.mark.parametrize("angle", [-12, 12])
@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_slanted_detection_initializes_reliable_angle_inside_and_outside_balloons(client, monkeypatch, in_balloon, angle, orientation):
    from io import BytesIO

    import cv2
    from app.core.database import SessionLocal
    from app.models import ImagePage
    from app.pipeline.processor import PipelineProcessor

    mask = np.zeros((140, 240), np.uint8)
    for y in (45, 65, 85):
        mask[y:y+3, 40:200] = 1
    if orientation == "vertical":
        mask = mask.T
    height, width = mask.shape
    mask = cv2.warpAffine(mask, cv2.getRotationMatrix2D((width/2, height/2), -angle, 1), (width, height), flags=cv2.INTER_NEAREST)
    ys, xs = np.nonzero(mask)
    box = [int(xs.min()), int(ys.min()), int(xs.max()-xs.min()+1), int(ys.max()-ys.min()+1)]
    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(output, "PNG")
    project = client.post("/api/projects", json={"name": "independent-text-angle"}).json()
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("page.png", output.getvalue(), "image/png")}).json()[0]
    detector = RFDetrTextDetector({"device": "cpu"})
    detector._text_classifier.ensure_loaded = Mock()
    predictions = [LayoutPrediction("text", .95, box, mask, (0, 0))]
    if in_balloon:
        predictions.append(LayoutPrediction("bubble", .95, [0, 0, width, height], np.ones_like(mask), (0, 0)))
    detector._text_classifier.detect_layout = Mock(return_value=(width, height, predictions))
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda *_: (detector, "rfdetr"))
    with SessionLocal() as db:
        stored = db.get(ImagePage, page["id"])
        PipelineProcessor(db).detect(stored, None, force=True)
        db.commit()
        db.refresh(stored)
        region = stored.regions[0]
        assert region.layout_data["detection"]["text_angle"] == pytest.approx(angle, abs=.5)
        assert region.orientation == orientation
        assert (region.polygon[1][1] - region.polygon[0][1]) * angle > 0
        assert region.translated_polygon == region.polygon
        assert region.rotation == region.layout_data["detection"]["text_angle"]
        assert bool(region.bubble_id) is in_balloon
        assert not region.perspective_warp
        # Explicit redetection rebuilds locked and manually adjusted typography.
        region.rotation, region.locked = 7., True
        db.commit()
        region_id = region.id
        PipelineProcessor(db).detect(stored, None, force=True)
        db.commit()
        db.refresh(stored)
        assert len(stored.regions) == 1
        rebuilt = stored.regions[0]
        assert rebuilt.id != region_id and not rebuilt.locked
        assert rebuilt.rotation == rebuilt.layout_data['detection']['text_angle']
    detector.unload()
