"""Unified manga layout detection, following Koharu's independent-region design.

Reference: koharu-rs/koharu, crates/koharu-pipeline/src/stages/detection.rs.
RF-DETR supplies text and balloon instances in one pass. Association is a
relationship, NOT a request to merge text boxes or fill the whole balloon.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np
from app.services.base import DetectionResult, ProviderCapabilities, TextDetector
from app.services.detection.classification import MODEL_REPOSITORY, MODEL_REVISION, LayoutTextClassifier
from app.services.detection.text_roles import LayoutPrediction
from app.utils.geometry import bbox_to_polygon, polygon_to_bbox
from PIL import Image

LAYOUT_DEFAULTS = {"text_threshold": .25, "bubble_threshold": .5, "panel_threshold": .5, "containment_threshold": .9}
LAYOUT_VERSION = 2
NATIVE_TEXT_POLICY = {"enabled": True, "filter_sfx": True, "min_sfx_confidence": .2}


def layout_options(config: dict[str, Any]) -> dict[str, float]:
    values = {name: float(config.get(name, default)) for name, default in LAYOUT_DEFAULTS.items()}
    for name, value in values.items():
        minimum = .5 if name == "containment_threshold" else .2
        if not math.isfinite(value) or not minimum <= value <= 1:
            raise ValueError(f"{name} 必须在 {minimum}–1 之间")
    return values


def _box_iou(a: LayoutPrediction, b: LayoutPrediction) -> float:
    ax, ay, aw, ah = a.bbox
    bx, by, bw, bh = b.bbox
    intersection = max(0., min(ax+aw, bx+bw)-max(ax, bx))*max(0., min(ay+ah, by+bh)-max(ay, by))
    return intersection/max(1., aw*ah+bw*bh-intersection)


def _spatial_key(item: LayoutPrediction) -> tuple:
    return item.bbox[1], -item.bbox[0], item.label


def _deduplicate(predictions: list[LayoutPrediction]) -> list[LayoutPrediction]:
    kept: list[LayoutPrediction] = []
    for prediction in sorted(predictions, key=lambda item: (-item.confidence, *_spatial_key(item))):
        if not any(item.label == prediction.label and _box_iou(item, prediction) >= .5 for item in kept):
            kept.append(prediction)
    return kept


def mask_containment(inner: LayoutPrediction, outer: LayoutPrediction) -> float:
    """Koharu's foreground containment, limited to the text detection window."""
    ix, iy = inner.mask_origin
    ox, oy = outer.mask_origin
    ih, iw = inner.mask.shape
    oh, ow = outer.mask.shape
    bx, by, bw, bh = inner.bbox
    left, top = max(ix, math.floor(bx)), max(iy, math.floor(by))
    right, bottom = min(ix+iw, math.ceil(bx+bw)), min(iy+ih, math.ceil(by+bh))
    if left >= right or top >= bottom:
        return 0.
    denominator = np.count_nonzero(inner.mask[top-iy:bottom-iy, left-ix:right-ix])
    x0, y0 = max(left, ox), max(top, oy)
    x1, y1 = min(right, ox+ow), min(bottom, oy+oh)
    if x0 >= x1 or y0 >= y1:
        return 0.
    numerator = np.count_nonzero((inner.mask[y0-iy:y1-iy, x0-ix:x1-ix] > 0) & (outer.mask[y0-oy:y1-oy, x0-ox:x1-ox] > 0))
    return numerator/max(1, denominator)


def _layout_tree(predictions: list[LayoutPrediction]) -> tuple[list[int], dict[int, int | None]]:
    """Koharu's layout_order: panel -> bubble -> text, then spatial siblings.

    Layout ancestry uses 50% bbox containment. Dialogue association separately
    uses 90% foreground containment; those two upstream rules are intentional.
    """
    def container(index: int, candidates: list[int]) -> int | None:
        x, y, w, h = predictions[index].bbox
        matches = []
        for candidate in candidates:
            a, b, c, d = predictions[candidate].bbox
            overlap = max(0., min(x+w, a+c)-max(x, a))*max(0., min(y+h, b+d)-max(y, b))
            if w*h > 0 and overlap/(w*h) >= .5:
                matches.append(candidate)
        return min(matches, key=lambda i: (predictions[i].bbox[2]*predictions[i].bbox[3],
                                          *_spatial_key(predictions[i]), i)) if matches else None

    panels = [i for i, item in enumerate(predictions) if item.label == "panel"]
    bubbles = [i for i, item in enumerate(predictions) if item.label == "bubble"]
    parents = {i: container(i, panels) for i in bubbles}
    text_panels = {}
    for i, item in enumerate(predictions):
        if item.label != "text":
            continue
        bubble = container(i, bubbles)
        panel = parents.get(bubble)
        if panel is None:
            panel = container(i, panels)
        text_panels[i] = panel
        parents[i] = bubble if bubble is not None else panel
    children: dict[int | None, list[int]] = {}
    for i in range(len(predictions)):
        children.setdefault(parents.get(i), []).append(i)
    for siblings in children.values():
        siblings.sort(key=lambda i: (*_spatial_key(predictions[i]), -predictions[i].confidence, i))
    order = []

    def visit(parent: int | None):
        for child in children.get(parent, []):
            order.append(child)
            visit(child)

    visit(None)
    return order, text_panels


def text_geometry(item: LayoutPrediction, width: int, height: int) -> tuple[list[list[float]], str, float]:
    """Projection-profile angle/orientation, inspired by Koharu (not block PCA).

    Bounded samples prevent dense lettering from monopolizing the request CPU.
    Direction is independent of the text-block aspect ratio except on ties.
    """
    rows, columns = np.nonzero(item.mask)
    vertical = item.bbox[3] > item.bbox[2]
    angle = 0.
    if len(rows) >= 4:
        stride = max(1, math.ceil(len(rows)/8192))
        x, y = columns[::stride].astype(float), rows[::stride].astype(float)
        scores = [(0., 0.), (0., 0.)]
        for degrees in np.arange(-45., 45.01, .5):
            radians = math.radians(degrees)
            sin, cos = math.sin(radians), math.cos(radians)
            for axis, projection in enumerate((y*cos-x*sin, x*cos+y*sin)):
                projection -= math.floor(float(projection.min()))
                bins = projection.astype(np.int32)
                fraction = projection-bins
                histogram = np.bincount(bins, weights=1-fraction, minlength=int(bins.max())+2)
                histogram += np.bincount(bins+1, weights=fraction, minlength=len(histogram))
                score = float(histogram@histogram)/len(x)
                if score > scores[axis][0]:
                    scores[axis] = score, float(degrees)
        maximum = max(scores[0][0], scores[1][0])
        if abs(scores[0][0]-scores[1][0]) > maximum*.02:
            vertical = scores[1][0] > scores[0][0]
        angle = scores[int(vertical)][1]
        if abs(angle) < 3:
            angle = 0.
    bx, by, bw, bh = item.bbox
    polygon = np.asarray(bbox_to_polygon([bx, by, bw, bh]), np.float64)
    if angle:
        center = np.asarray([bx+bw/2, by+bh/2])
        rad = math.radians(angle)
        rotation = np.asarray([[math.cos(rad), -math.sin(rad)], [math.sin(rad), math.cos(rad)]])
        polygon = (polygon-center)@rotation.T+center
    polygon[:, 0] = np.clip(polygon[:, 0], 0, width)
    polygon[:, 1] = np.clip(polygon[:, 1], 0, height)
    return polygon.tolist(), "vertical" if vertical else "horizontal", angle


def _instance_id(page_key: str, item: LayoutPrediction) -> str:
    content = f"{page_key}|{item.mask_origin}|{item.mask.shape}".encode()+np.packbits(item.mask > 0).tobytes()
    return "bubble-"+hashlib.sha256(content).hexdigest()[:20]


def build_layout_regions(predictions: list[LayoutPrediction], width: int, height: int, config: dict[str, Any], *, page_key: str) -> list[DetectionResult]:
    options, policy = layout_options(config), dict(NATIVE_TEXT_POLICY)
    thresholds = {"text": options["text_threshold"], "bubble": options["bubble_threshold"], "panel": options["panel_threshold"], "onomatopoeia": .2}
    predictions = _deduplicate([item for item in predictions if item.confidence > thresholds[item.label]])
    bubbles = [item for item in predictions if item.label == "bubble"]
    order, text_panels = _layout_tree(predictions)
    results = []
    for index in order:
        text = predictions[index]
        if text.label not in {"text", "onomatopoeia"}:
            continue
        candidates = [(bubble, mask_containment(text, bubble)) for bubble in bubbles] if text.label == "text" else []
        candidates = [pair for pair in candidates if pair[1] >= options["containment_threshold"]]
        # As in Koharu, prefer the smallest containing instance, without
        # collapsing multiple text instances inside that balloon into one box.
        candidate = min(candidates, key=lambda pair: np.count_nonzero(pair[0].mask)) if candidates else None
        bubble, coverage = candidate if candidate else (None, 0.)
        bubble_id = _instance_id(page_key, bubble) if bubble is not None else None
        # Keep SFX only as excluded diagnostic candidates. They never become
        # OCR/translation/repair regions, even when located inside a balloon.
        excluded = text.label != "text"
        polygon, orientation, angle = (text_geometry(text, width, height) if not excluded
                                       else (bbox_to_polygon(text.bbox), "horizontal", 0.))
        classification = {"version": 1, "status": "classified", "label": text.label, "excluded": excluded,
                          "confidence": text.confidence, "model": MODEL_REPOSITORY, "model_revision": MODEL_REVISION, "policy": policy}
        assignment = {"status": "assigned" if bubble is not None else "unassigned", "bubble_id": bubble_id,
                      "method": "text_mask_containment", "coverage": round(coverage, 4), "core_coverage": round(coverage, 4),
                      "balloon_confidence": bubble.confidence if bubble is not None else 0.}
        metadata = {"detector": "rfdetr", "layout_version": LAYOUT_VERSION, "layout_settings": options,
                    "model": MODEL_REPOSITORY, "model_revision": MODEL_REVISION,
                    "text_classification": classification, "balloon_assignment": assignment,
                    "text_angle": angle, "independent_text_instance": True}
        panel_index = text_panels.get(index)
        if panel_index is not None:
            panel = predictions[panel_index]
            metadata["panel"] = {"id": "panel-"+hashlib.sha256(str(panel.bbox).encode()).hexdigest()[:12],
                                 "bbox": panel.bbox, "method": "rfdetr"}
        results.append(DetectionResult(
            polygon=polygon, bbox=polygon_to_bbox(polygon), confidence=text.confidence, orientation=orientation,
            metadata=metadata, bubble_id=bubble_id,
            balloon_mask=bubble.mask if bubble is not None else None,
            balloon_mask_origin=bubble.mask_origin if bubble is not None else None,
            balloon_mask_id=bubble_id, balloon_mask_confidence=bubble.confidence if bubble is not None else None,
        ))
    return results


class RFDetrTextDetector(TextDetector):
    capabilities = ProviderCapabilities(
        name="rfdetr", provider_type="detection", devices=["cpu", "cuda"],
        description="Koharu RF-DETR：一次检测文字、气泡、拟声词和分镜，按像素关联，不强制合框",
        extra={"polygon": True, "pixel_mask": True, "layout_instances": True, "text_role_classification": ["text", "onomatopoeia"]},
    )

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._text_classifier = LayoutTextClassifier(self.config)

    def load(self) -> None:
        layout_options(self.config)
        self._text_classifier.ensure_loaded()

    def unload(self) -> None:
        self._text_classifier.close()
        super().unload()

    def detect(self, image_path: Path) -> list[DetectionResult]:
        self.ensure_loaded()
        options = layout_options(self.config)
        thresholds = (options["text_threshold"], .2, options["bubble_threshold"], options["panel_threshold"])
        width, height, predictions = self._text_classifier.detect_layout(image_path, thresholds=thresholds)
        with Image.open(image_path) as source:
            if source.size != (width, height):
                raise ValueError("RF-DETR 返回的页面尺寸不匹配")
        return build_layout_regions(predictions, width, height, self.config, page_key=str(image_path.resolve()))
