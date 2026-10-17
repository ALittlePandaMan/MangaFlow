"""Unified manga layout detection, following Koharu's independent-region design.

Reference: koharu-rs/koharu, crates/koharu-pipeline/src/stages/detection.rs.
RF-DETR supplies text and balloon instances in one pass. Association is a
relationship, NOT a request to merge text boxes or fill the whole balloon.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
from app.services.base import DetectionResult, ProviderCapabilities, TextDetector
from app.services.detection.classification import MODEL_REPOSITORY, MODEL_REVISION, LayoutTextClassifier
from app.services.detection.covered_text import covered_text_parents
from app.services.detection.long_text import long_text_partitions
from app.services.detection.nested_text import _window, nested_text_duplicates
from app.services.detection.text_angle import projection_score, supported_text_angle
from app.services.detection.text_roles import LayoutPrediction
from app.utils.geometry import bbox_to_polygon, polygon_to_bbox
from PIL import Image

TEXT_BOX_PADDING = 3.0  # Original-image pixels on each edge, not display/zoom pixels.
MAX_TEXT_BOX_PADDING = 50.0
LAYOUT_DEFAULTS = {"text_threshold": .25, "bubble_threshold": .5, "panel_threshold": .5,
                   "containment_threshold": .9, "text_box_padding": TEXT_BOX_PADDING}
LAYOUT_VERSION = 9
NATIVE_TEXT_POLICY = {"enabled": True, "filter_sfx": True, "min_sfx_confidence": .2}
PADDING_SIDES = ("top", "bottom", "left", "right")


def text_box_padding(value: Any) -> dict[str, float]:
    """Accept legacy uniform margins and normalize independent edge margins."""
    if isinstance(value, dict) and set(value) - set(PADDING_SIDES):
        raise ValueError("文本框扩边只支持 top、bottom、left、right 四边")
    values = value if isinstance(value, dict) else dict.fromkeys(PADDING_SIDES, value)
    margins = {}
    for side in PADDING_SIDES:
        try:
            margin = float(values.get(side, TEXT_BOX_PADDING))
        except (TypeError, ValueError) as exc:
            raise ValueError("文本框各边扩边像素必须在 0–50 之间") from exc
        if not math.isfinite(margin) or not 0 <= margin <= MAX_TEXT_BOX_PADDING:
            raise ValueError("文本框各边扩边像素必须在 0–50 之间")
        margins[side] = margin
    return margins


def layout_options(config: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, default in LAYOUT_DEFAULTS.items():
        if name == "text_box_padding":
            values[name] = text_box_padding(config.get(name, default))
            continue
        value = float(config.get(name, default))
        minimum = .5 if name == "containment_threshold" else .2
        if not math.isfinite(value) or not minimum <= value <= 1:
            raise ValueError(f"{name} 必须在 {minimum}–1 之间")
        values[name] = value
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


def text_geometry(item: LayoutPrediction, width: int, height: int, *, padding: float | dict[str, float] = 0.) -> tuple[list[list[float]], str, float]:
    """Projection-profile angle/orientation, inspired by Koharu (not block PCA).

    Bounded samples prevent dense lettering from monopolizing the request CPU.
    Direction is independent of the text-block aspect ratio except on ties.
    """
    # RF-DETR masks may contain disconnected foreground outside the detected
    # text. Match Koharu's typography window instead of letting another word
    # elsewhere on the page determine this region's writing direction.
    bx, by, bw, bh = item.bbox
    mx, my = item.mask_origin
    mask_height, mask_width = item.mask.shape
    margin = 2
    left = max(mx, 0, math.floor(bx-margin))
    top = max(my, 0, math.floor(by-margin))
    right = min(mx+mask_width, width, math.ceil(bx+bw+margin))
    bottom = min(my+mask_height, height, math.ceil(by+bh+margin))
    window = item.mask[top-my:bottom-my, left-mx:right-mx] if right > left and bottom > top else np.empty((0, 0))
    rows, columns = np.nonzero(window)
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
                score = projection_score(projection)
                if score > scores[axis][0]:
                    scores[axis] = score, float(degrees)
        maximum = max(scores[0][0], scores[1][0])
        if abs(scores[0][0]-scores[1][0]) > maximum*.02:
            vertical = scores[1][0] > scores[0][0]
        angle = supported_text_angle(x, y, vertical, scores[int(vertical)][1])
    return _text_polygon(item.bbox, width, height, padding, angle), "vertical" if vertical else "horizontal", angle


def _text_polygon(box: list[float], width: int, height: int, padding: float | dict[str, float], angle: float) -> list[list[float]]:
    # Pad only the final frame, after measuring typography from the original
    # prediction. Expanding in local axes before rotation keeps slanted edges
    # parallel while giving each side its own margin. Rotation stays anchored
    # to the original frame, so an unchanged edge does not shift with its centre.
    bx, by, bw, bh = box
    margins = text_box_padding(padding)
    polygon = np.asarray(bbox_to_polygon([bx-margins["left"], by-margins["top"],
        bw+margins["left"]+margins["right"], bh+margins["top"]+margins["bottom"]]), np.float64)
    if angle:
        center = np.asarray([bx+bw/2, by+bh/2])
        rad = math.radians(angle)
        rotation = np.asarray([[math.cos(rad), -math.sin(rad)], [math.sin(rad), math.cos(rad)]])
        polygon = (polygon-center)@rotation.T+center
    polygon[:, 0] = np.clip(polygon[:, 0], 0, width)
    polygon[:, 1] = np.clip(polygon[:, 1], 0, height)
    return polygon.tolist()


def _instance_id(page_key: str, item: LayoutPrediction) -> str:
    content = f"{page_key}|{item.mask_origin}|{item.mask.shape}".encode()+np.packbits(item.mask > 0).tobytes()
    return "bubble-"+hashlib.sha256(content).hexdigest()[:20]


def _prose_frame(source: DetectionResult, box: list[float], neighbors: list[list[float]],
                 width: int, height: int, metadata: dict, *, padding: dict[str, float]) -> DetectionResult:
    x, y, w, h = box
    left, top = max(0., x-padding["left"]), max(0., y-padding["top"])
    right, bottom = min(width, x+w+padding["right"]), min(height, y+h+padding["bottom"])
    vertical_padding = padding["top"] + padding["bottom"]
    for nx, ny, nw, nh in neighbors:
        # Padding can bring horizontally separated pieces into the same column.
        # Share their vertical gap before those expanded corners intersect.
        if min(x+w+padding["right"], nx+nw+padding["right"]) <= max(x-padding["left"], nx-padding["left"]):
            continue
        # Share narrow gaps in proportion to the two requested margins. A
        # top-only expansion can use the gap left by a zero bottom margin.
        if vertical_padding and ny+nh <= y:
            top = max(top, ny+nh+(y-ny-nh)*padding["bottom"]/vertical_padding)
        if vertical_padding and ny >= y+h:
            bottom = min(bottom, y+h+(ny-y-h)*padding["bottom"]/vertical_padding)
    bbox = [left, top, right-left, bottom-top]
    return replace(source, bbox=bbox, polygon=bbox_to_polygon(bbox), orientation="horizontal",
                   duplicate_members=[], metadata={**source.metadata, "raw_bbox": box, "text_angle": 0.,
                                                   "long_text_partition": metadata})


def build_layout_regions(predictions: list[LayoutPrediction], width: int, height: int, config: dict[str, Any], *, page_key: str) -> list[DetectionResult]:
    options, policy = layout_options(config), dict(NATIVE_TEXT_POLICY)
    padding = options["text_box_padding"]
    thresholds = {"text": options["text_threshold"], "bubble": options["bubble_threshold"], "panel": options["panel_threshold"], "onomatopoeia": .2}
    predictions = _deduplicate([item for item in predictions if item.confidence > thresholds[item.label]])
    bubbles = [(i, item) for i, item in enumerate(predictions) if item.label == "bubble"]
    order, text_panels = _layout_tree(predictions)
    assignments: dict[int, tuple[int | None, float]] = {}
    geometries = {}
    for index, text in enumerate(predictions):
        if text.label != "text":
            continue
        candidates = [(i, mask_containment(text, bubble)) for i, bubble in bubbles]
        candidates = [pair for pair in candidates if pair[1] >= options["containment_threshold"]]
        assignments[index] = min(candidates, key=lambda pair: np.count_nonzero(predictions[pair[0]].mask)) if candidates else (None, 0.)
        # Keep filtering geometry stable: this setting only changes the final
        # frame, never which candidates are duplicates or belong to a balloon.
        geometries[index] = text_geometry(text, width, height, padding=TEXT_BOX_PADDING)
    duplicates = nested_text_duplicates(predictions, {i: pair[0] if pair[1] >= .95 else None for i, pair in assignments.items()},
                                        {i: (geometry[1], geometry[2]) for i, geometry in geometries.items()})
    partitions = long_text_partitions(predictions, {i: pair[0] for i, pair in assignments.items()},
                                     {i: (geometry[1], geometry[2]) for i, geometry in geometries.items()}, text_panels)
    suppressed = {i for children in duplicates.values() for i in children}
    suppressed.update(i for partition in partitions.values() for i in partition.children)
    # A candidate gets one decision only. In particular, the free-text pass
    # must never remove a parent whose children the dialogue pass just removed.
    covered = covered_text_parents(predictions, {i: pair[0] for i, pair in assignments.items()}, geometries,
                                   text_panels, reserved=suppressed | set(duplicates) | set(partitions))
    suppressed.update(covered)
    covered_by = {child: parent for parent, children in covered.items() for child in children}
    results: dict[int, DetectionResult] = {}
    for index in order:
        text = predictions[index]
        if text.label not in {"text", "onomatopoeia"}:
            continue
        # As in Koharu, prefer the smallest containing instance, without
        # collapsing independent text instances inside that balloon into one.
        bubble_index, coverage = assignments.get(index, (None, 0.))
        bubble = predictions[bubble_index] if bubble_index is not None else None
        bubble_id = _instance_id(page_key, bubble) if bubble is not None else None
        # Keep SFX only as excluded diagnostic candidates. They never become
        # OCR/translation/repair regions, even when located inside a balloon.
        excluded = text.label != "text"
        polygon, orientation, angle = (geometries[index] if not excluded
                                       else (bbox_to_polygon(text.bbox), "horizontal", 0.))
        if not excluded and any(margin != TEXT_BOX_PADDING for margin in padding.values()):
            polygon = _text_polygon(text.bbox, width, height, padding, angle)
        classification = {"version": 1, "status": "classified", "label": text.label, "excluded": excluded,
                          "confidence": text.confidence, "model": MODEL_REPOSITORY, "model_revision": MODEL_REVISION, "policy": policy}
        assignment = {"status": "assigned" if bubble is not None else "unassigned", "bubble_id": bubble_id,
                      "method": "text_mask_containment", "coverage": round(coverage, 4), "core_coverage": round(coverage, 4),
                      "balloon_confidence": bubble.confidence if bubble is not None else 0.}
        # Failure to meet the dialogue threshold does not prove free text.
        # Text crossing/near a balloon, or missing segmentation, is ambiguous.
        # This role gates typography only; detection/association/filtering and
        # all existing dialogue geometry remain unchanged.
        tx, ty, tw, th = text.bbox
        touches_balloon = any(
            max(0., min(tx+tw, bx+bw)-max(tx, bx))
            * max(0., min(ty+th, by+bh)-max(ty, by)) > .1*tw*th
            for _, candidate in bubbles for bx, by, bw, bh in [candidate.bbox]
        )
        lettering = _window(text)
        role = "dialogue" if bubble is not None else (
            "free_text" if not excluded and not touches_balloon
            and np.count_nonzero(lettering.pixels) >= 24 else "unknown"
        )
        metadata = {"detector": "rfdetr", "layout_version": LAYOUT_VERSION, "layout_settings": options,
                    "model": MODEL_REPOSITORY, "model_revision": MODEL_REVISION,
                    "text_classification": classification, "balloon_assignment": assignment,
                    "text_angle": angle, "independent_text_instance": True,
                    "text_role": {"version": 1, "label": role, "method": "rfdetr_balloon_relation"},
                    "raw_bbox": list(text.bbox), "text_box_padding": dict(padding) if not excluded else dict.fromkeys(PADDING_SIDES, 0.)}
        panel_index = text_panels.get(index)
        if panel_index is not None:
            panel = predictions[panel_index]
            metadata["panel"] = {"id": "panel-"+hashlib.sha256(str(panel.bbox).encode()).hexdigest()[:12],
                                 "bbox": panel.bbox, "method": "rfdetr"}
        if index in duplicates:
            metadata["nested_deduplication"] = {"method": "same_bubble_mask_coverage", "version": 1,
                                                "removed_count": len(duplicates[index]), "edge_tolerance": 1}
        if index in covered_by:
            parent = covered_by[index]
            metadata["covered_parent_deduplication"] = {"method": "free_text_mask_union", "version": 1,
                "parent_bbox": list(predictions[parent].bbox), "child_count": len(covered[parent]), "edge_tolerance": 1}
        results[index] = DetectionResult(
            polygon=polygon, bbox=polygon_to_bbox(polygon), confidence=text.confidence, orientation=orientation,
            metadata=metadata, bubble_id=bubble_id,
            balloon_mask=bubble.mask if bubble is not None else None,
            balloon_mask_origin=bubble.mask_origin if bubble is not None else None,
            balloon_mask_id=bubble_id, balloon_mask_confidence=bubble.confidence if bubble is not None else None,
            text_mask=lettering.pixels, text_mask_origin=(lettering.x, lettering.y),
        )
    for parent, children in duplicates.items():
        results[parent].duplicate_members = [results[i] for i in order if i in children]
    output = []
    for index, result in results.items():
        if index in suppressed:
            continue
        if index not in partitions:
            output.append(result)
            continue
        partition = partitions[index]
        neighbors = [piece.bbox for piece in partition.pieces]
        for piece in partition.pieces:
            source = results[piece.owner]
            metadata = {"version": 1, "method": "verified_horizontal_lines",
                        "parent_bbox": predictions[index].bbox, "source_bbox": predictions[piece.owner].bbox,
                        "line_count": len(piece.lines), "replaced_candidates": 1+len(partition.children)}
            frame = _prose_frame(source, piece.bbox, neighbors, width, height, metadata, padding=padding)
            if len(piece.lines) > 1:
                # Existing manual/locked frames may cover just one line. Reuse
                # the pipeline's per-member protection, not a whole-block drop.
                frame.duplicate_members = [_prose_frame(source, box, neighbors+piece.lines, width, height,
                                                       {**metadata, "line_count": 1}, padding=padding) for box in piece.lines]
            output.append(frame)
    return output


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
