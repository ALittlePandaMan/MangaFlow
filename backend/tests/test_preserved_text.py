from __future__ import annotations

from io import BytesIO
from unittest.mock import Mock

import numpy as np
import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services.detection.covered_text import _frames_cover
from app.services.detection.layout import RFDetrTextDetector
from app.services.detection.mask_evidence import _Mask
from app.services.detection.mask_geometry import covered_components
from app.utils.geometry import bbox_to_polygon
from detection_assertions import assert_rebuilt_regions
from PIL import Image
from test_covered_text import build, recorded


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
def test_real_partial_title_redetection_rebuilds_both_languages_and_replaces_manual_data(client, monkeypatch, protection, line):
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
        stored = db.get(ImagePage, page["id"])
        snapshots = []
        for _ in range(2):
            previous_ids = {r.id for r in stored.regions}
            PipelineProcessor(db).detect(stored, None, force=True)
            db.commit()
            db.refresh(stored)
            assert_rebuilt_regions(stored.regions, expected, previous_ids)
            snapshots.append([r.bbox for r in stored.regions])
        assert snapshots[0] == snapshots[1]
    response = client.get(f"/api/images/{page['id']}/regions")
    assert response.status_code == 200 and '"text_mask"' not in response.text
    detector.unload()
