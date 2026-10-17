from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.pipeline.processor import PipelineProcessor, _exclude_preserved_geometry
from app.services.base import DetectionResult
from app.services.detection.covered_text import _frames_cover
from app.services.detection.mask_geometry import covered_components
from app.services.detection.nested_text import _Mask
from app.services.detection.preserved_text import exclude_preserved_text, frame_is_preserved
from app.services.detection.layout import RFDetrTextDetector
from app.utils.geometry import bbox_to_polygon
from test_covered_text import recorded, build


def frame(box, polygon=None):
    return SimpleNamespace(bbox=box, polygon=polygon if polygon is not None else bbox_to_polygon(box))


def sample(vertical=False):
    pixels = np.zeros((70, 80), np.uint8)
    for y in (5, 30, 55):
        for x in range(5, 70, 15):
            pixels[y:y+10, x:x+9] = 1
    if vertical:
        pixels = pixels.T.copy()
    box = [20., 20., float(pixels.shape[1]), float(pixels.shape[0])]
    return DetectionResult(bbox=box, polygon=bbox_to_polygon(box), confidence=.8,
                           orientation="vertical" if vertical else "horizontal",
                           metadata={"text_angle": 0., "text_box_padding": 3.},
                           text_mask=pixels, text_mask_origin=(20, 20))


@pytest.mark.parametrize("vertical", [False, True])
@pytest.mark.parametrize("line", [0, 1, 2])
@pytest.mark.parametrize("padding", [3., {"top": 0., "bottom": 7., "left": 4., "right": 1.}])
def test_only_covered_whole_lines_are_removed_and_middle_line_creates_two_frames(vertical, line, padding):
    result = sample(vertical)
    result.metadata["text_box_padding"] = padding
    before = deepcopy(result)
    box = [20., 20.+25*line, 80., 20.]
    if vertical:
        box = [box[1], box[0], box[3], box[2]]
    user = frame(box)
    remaining = exclude_preserved_text(result, [user])
    assert remaining is not None and len(remaining) == (2 if line == 1 else 1)
    mask = _Mask(result.text_mask, 20, 20)
    assert _frames_cover(mask, [user.polygon, *[r.polygon for r in remaining]])
    assert all(r.confidence == result.confidence and r.orientation == result.orientation for r in remaining)
    assert result.bbox == before.bbox and result.metadata == before.metadata
    np.testing.assert_array_equal(result.text_mask, before.text_mask)
    if vertical and line == 1:
        assert remaining[0].bbox[0] > remaining[1].bbox[0]


def test_partial_glyph_or_partial_line_is_not_discarded_or_clipped():
    result = sample()
    for box in ([20, 20, 10, 20], [20, 20, 30, 20], [20, 20, 80, 10]):
        assert exclude_preserved_text(result, [frame(box)]) == [result]


def test_two_manual_frames_cannot_jointly_claim_a_bisected_component():
    result = sample()
    # x=29 cuts the first glyph (x=25..34) in half on each line.
    assert exclude_preserved_text(result, [frame([20, 20, 9, 70]), frame([29, 20, 71, 70])]) == [result]


def test_several_manual_frames_can_cover_complete_distinct_lines():
    result = sample()
    assert exclude_preserved_text(result, [frame([20, 20+25*i, 80, 20]) for i in range(3)]) == []


def test_rotated_container_bbox_is_not_evidence_of_complete_coverage():
    result = sample()
    user = frame(result.bbox, [[60, 20], [100, 55], [60, 90], [20, 55]])
    remaining = exclude_preserved_text(result, [user])
    assert remaining and len(remaining) == 2
    assert _frames_cover(_Mask(result.text_mask, 20, 20), [user.polygon, *[r.polygon for r in remaining]])
    assert not frame_is_preserved(result.polygon, [user])


def test_rotated_or_perspective_detection_is_not_axis_aligned_by_filter():
    result = sample()
    user = frame([20, 20, 80, 20])
    result.metadata["text_angle"] = 15.
    assert exclude_preserved_text(result, [user]) == [result]
    result.metadata["text_angle"] = 0.
    result.polygon[0][0] += 5
    assert exclude_preserved_text(result, [user]) == [result]


def test_concave_manual_polygon_notch_keeps_uncovered_text():
    result = sample()
    user = frame(result.bbox, [[20,20],[100,20],[100,90],[20,90],[20,70],[95,70],[95,45],[20,45]])
    remaining = exclude_preserved_text(result, [user])
    assert remaining and len(remaining) == 1 and remaining[0].bbox[1] > 40
    assert not frame_is_preserved(result.polygon, [user])


@pytest.mark.parametrize("kind", ["none", "empty", "solid"])
def test_missing_or_untrustworthy_mask_uses_full_polygon_containment_not_partial_overlap(kind):
    result = sample()
    result.text_mask = {"none": None, "empty": np.zeros((0,0), np.uint8), "solid": np.ones((70,80), np.uint8)}[kind]
    user = frame([20, 20, 80, 20])
    assert exclude_preserved_text(result, [user]) is None
    assert _exclude_preserved_geometry(result, [user]) is result
    assert _exclude_preserved_geometry(result, [frame(result.bbox)]) is None


def test_legacy_grouping_does_not_drop_a_member_just_because_manual_frame_is_inside_it():
    result = sample()
    result.metadata["line_grouping"] = {"source_boxes": [[20,20,80,20],[20,50,80,20]]}
    assert _exclude_preserved_geometry(result, [frame([20,20,15,20])]) is result


def test_component_must_fit_one_frame_across_multiple_row_chunks():
    pixels = np.ones((260, 8), np.uint8)
    mask = _Mask(pixels, 10, 20)
    polygons = [bbox_to_polygon([10,20,8,140]), bbox_to_polygon([10,150,8,130])]
    assert _frames_cover(mask, polygons)
    assert not covered_components(mask, polygons).any()
    assert covered_components(mask, [bbox_to_polygon([10,20,8,260])]).all()


def test_pixel_centres_and_invalid_polygons_are_handled_conservatively():
    mask = _Mask(np.ones((1,1), np.uint8), 10, 20)
    assert covered_components(mask, [bbox_to_polygon([10.5,20.5,1,1])]).all()
    assert not covered_components(mask, [bbox_to_polygon([10.51,20.51,1,1]), [[0,0],[1,1]]]).any()


@pytest.mark.parametrize("protection", ["manual", "locked", "legacy"])
@pytest.mark.parametrize("line", ["japanese", "english"])
def test_real_partial_title_redetection_preserves_other_language_and_manual_data(client, monkeypatch, protection, line):
    width, height, raw = recorded()
    expected = build(raw, width, height)
    box = [55., 424., 116., 30.] if line == "japanese" else [55., 454., 116., 22.]
    image = BytesIO()
    Image.new("RGB", (width, height), "white").save(image, "PNG")
    project = client.post("/api/projects", json={"name": "partial-title-protection"}).json()
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("page.png", image.getvalue(), "image/png")}).json()[0]
    detector = RFDetrTextDetector({"device": "cpu"})
    detector._text_classifier.ensure_loaded = Mock()
    detector._text_classifier.detect_layout = Mock(return_value=(width, height, raw))
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda *_: (detector, "rfdetr"))
    with SessionLocal() as db:
        region = TextRegion(image_id=page["id"], region_key="R001", reading_order=1,
            bbox=box, polygon=bbox_to_polygon(box), translated_bbox=box, translated_polygon=bbox_to_polygon(box),
            source_text="user source", translated_text="user translation", rotation=11., locked=protection == "locked",
            layout_data={"manual": True} if protection == "manual" else {"detection": {}} if protection == "locked" else {})
        db.add(region)
        db.commit()
        identifier = region.id
        stored = db.get(ImagePage, page["id"])
        snapshots = []
        for _ in range(2):
            PipelineProcessor(db).detect(stored, None, force=True)
            db.commit()
            db.refresh(stored)
            protected = next(r for r in stored.regions if r.id == identifier)
            assert protected.bbox == box and protected.rotation == 11.
            assert protected.source_text == "user source" and protected.translated_text == "user translation"
            others = [r for r in stored.regions if r.id != identifier]
            assert len(others) == 4
            assert [r.bbox for r in others[1:]] == [r.bbox for r in expected[1:]]
            title = others[0]
            assert "preserved_text_partition" in title.layout_data["detection"]
            if line == "japanese":
                assert title.bbox[1] >= 454 and title.bbox[1]+title.bbox[3] >= 472
            else:
                assert title.bbox[1] <= 428 and title.bbox[1]+title.bbox[3] <= 455
            ink = _Mask(expected[0].text_mask, *expected[0].text_mask_origin)
            assert _frames_cover(ink, [protected.polygon, title.polygon])
            snapshots.append([r.bbox for r in others])
        assert snapshots[0] == snapshots[1]
    response = client.get(f"/api/images/{page['id']}/regions")
    assert response.status_code == 200 and '"text_mask"' not in response.text
    detector.unload()
