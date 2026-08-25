from __future__ import annotations

import logging
from pathlib import Path

import cv2
from app.services.base import DetectionResult, ProviderCapabilities, ProviderError, TextDetector
from app.services.detection.bubbles import OnnxBubbleSegmenter
from app.services.detection.grouping import MIN_TRUSTED_BALLOON_CONFIDENCE, group_text_regions_by_bubbles
from app.services.infra.device import is_accelerator_error, release_paddle_cuda, resolve_paddle_device
from app.services.infra.paddle import extract_paddle_lines
from app.utils.geometry import bbox_to_polygon, polygon_to_bbox

logger = logging.getLogger(__name__)


class OpenCVTextDetector(TextDetector):
    """Dependency-free fallback detector based on clustered dark glyph components.

    It is intentionally conservative and serves as a runnable fallback. Production
    installations can register ComicTextDetector/DBNet adapters without changing the pipeline.
    """

    capabilities = ProviderCapabilities(
        name="opencv-fallback",
        provider_type="detection",
        description="CPU fallback using thresholding and morphological text-line grouping",
        devices=["cpu"],
        supports_batch=False,
        extra={"polygon": True, "pixel_mask": False},
    )

    def detect(self, image_path: Path) -> list[DetectionResult]:
        self.ensure_loaded()
        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError(f"Cannot read image: {image_path}")
        height, width = image.shape
        inverted = cv2.adaptiveThreshold(image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 18)
        # Two kernels support common horizontal and vertical manga typography.
        horizontal = cv2.morphologyEx(inverted, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3)))
        vertical = cv2.morphologyEx(inverted, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 9)))
        merged = cv2.bitwise_or(horizontal, vertical)
        contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        minimum_area = max(36, int(width * height * 0.00004))
        results: list[DetectionResult] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h
            if area < minimum_area or w < 6 or h < 6:
                continue
            if w > width * 0.8 and h > height * 0.8:
                continue
            padding = max(3, round(min(w, h) * 0.12))
            x0, y0 = max(0, x - padding), max(0, y - padding)
            x1, y1 = min(width, x + w + padding), min(height, y + h + padding)
            bbox = [float(x0), float(y0), float(x1 - x0), float(y1 - y0)]
            orientation = "vertical" if h > w * 1.2 else "horizontal"
            density = min(1.0, float(cv2.countNonZero(inverted[y : y + h, x : x + w])) / max(1, area) * 3)
            results.append(
                DetectionResult(
                    polygon=bbox_to_polygon(bbox),
                    bbox=bbox,
                    confidence=max(0.35, density),
                    orientation=orientation,
                    region_type="background_complex",
                    metadata={
                        "balloon_assignment": {
                            "status": "disabled",
                            "bubble_id": None,
                            "reason": "provider_unsupported",
                        },
                    },
                )
            )
        # Reading order is assigned by the pipeline; large false-positive sets are capped.
        results.sort(key=lambda item: item.bbox[2] * item.bbox[3], reverse=True)
        return results[:200]


class PaddleTextDetector(TextDetector):
    capabilities = ProviderCapabilities(
        name="paddleocr",
        provider_type="detection",
        description="Optional PaddleOCR Japanese text detector with quadrilateral polygons",
        devices=["cpu", "cuda"],
        supports_batch=True,
        extra={
            "polygon": True,
            "bubble_instance_grouping": True,
            "optional_dependency": "paddleocr",
        },
    )

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._model = None
        self._device = "cpu"
        self._bubble_segmenter: OnnxBubbleSegmenter | None = None
        self._bubble_unavailable_reason: str | None = None

    def load(self) -> None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise ProviderError(
                "PaddleOCR is not installed; install paddleocr and a matching paddlepaddle package"
            ) from exc
        self._device = resolve_paddle_device(self.config.get("device", "auto"))
        try:
            self._model = self._create_model(PaddleOCR, self._device)
        except Exception as exc:
            if self._device == "cpu" or not is_accelerator_error(exc):
                raise
            logger.warning("Paddle detection could not start on %s; retrying on CPU: %s", self._device, exc)
            self._device = "cpu"
            release_paddle_cuda()
            self._model = self._create_model(PaddleOCR, self._device)
        bubble_config = _bubble_grouping_config(self.config)
        self._bubble_unavailable_reason = None
        if bool(bubble_config.get("enabled", True)):
            self._bubble_segmenter = OnnxBubbleSegmenter(bubble_config)
            try:
                self._bubble_segmenter.ensure_loaded()
            except Exception as exc:
                # Text detection remains usable offline or when the optional
                # balloon model cannot be loaded. Keep a circuit breaker for
                # this provider session so every page does not repeat a long
                # network timeout. Reconfiguration or restart creates a retry.
                self._bubble_unavailable_reason = "model_unavailable"
                logger.warning("Bubble segmentation is unavailable; text boxes will remain separate: %s", exc)

    def unload(self) -> None:
        if self._bubble_segmenter is not None:
            self._bubble_segmenter.unload()
        self._bubble_segmenter = None
        self._bubble_unavailable_reason = None
        self._model = None
        super().unload()

    def _create_model(self, model_class: type, device: str):
        return model_class(
            lang=str(self.config.get("language", "japan")),
            ocr_version=str(self.config.get("ocr_version", "PP-OCRv5")),
            device=device,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            text_det_box_thresh=float(self.config.get("box_threshold", 0.45)),
            text_det_unclip_ratio=float(self.config.get("unclip_ratio", 1.8)),
        )

    def detect(self, image_path: Path) -> list[DetectionResult]:
        self.ensure_loaded()
        try:
            raw = self._model.predict(str(image_path))
        except Exception as exc:
            if self._device == "cpu" or not is_accelerator_error(exc):
                raise
            logger.warning("Paddle detection failed on %s; retrying on CPU: %s", self._device, exc)
            self._model = None
            self._device = "cpu"
            release_paddle_cuda()
            from paddleocr import PaddleOCR

            self._model = self._create_model(PaddleOCR, self._device)
            raw = self._model.predict(str(image_path))
        output: list[DetectionResult] = []
        for item in extract_paddle_lines(raw):
            raw_polygon = item["polygon"]
            if not isinstance(raw_polygon, (list, tuple)):
                continue
            polygon = [
                [float(point[0]), float(point[1])]
                for point in raw_polygon
                if isinstance(point, (list, tuple)) and len(point) >= 2
            ]
            if len(polygon) < 3:
                continue
            bbox = polygon_to_bbox(polygon)
            confidence = float(item["confidence"] or 0.7)
            output.append(
                DetectionResult(
                    polygon=polygon,
                    bbox=bbox,
                    confidence=confidence,
                    orientation="vertical" if bbox[3] > bbox[2] * 1.2 else "horizontal",
                    region_type="background_complex",
                )
            )
        bubble_config = _bubble_grouping_config(self.config)
        if not output:
            return output
        if not bool(bubble_config.get("enabled", True)):
            return _mark_bubble_grouping_disabled(output)
        if self._bubble_unavailable_reason is not None:
            return _mark_bubble_grouping_unavailable(output, self._bubble_unavailable_reason)
        try:
            if self._bubble_segmenter is None:
                self._bubble_segmenter = OnnxBubbleSegmenter(bubble_config)
            bubbles = self._bubble_segmenter.segment(image_path)
            return group_text_regions_by_bubbles(
                output,
                bubbles,
                page_key=str(image_path.resolve()),
                image_path=image_path,
                min_bubble_confidence=max(
                    MIN_TRUSTED_BALLOON_CONFIDENCE,
                    float(bubble_config.get("min_bubble_confidence", MIN_TRUSTED_BALLOON_CONFIDENCE)),
                ),
                min_containment=float(bubble_config.get("min_containment", 0.55)),
                min_core_containment=float(bubble_config.get("min_core_containment", 0.8)),
                ambiguity_margin=float(bubble_config.get("ambiguity_margin", 0.12)),
                max_second_containment=float(bubble_config.get("max_second_containment", 0.45)),
                mask_padding=int(bubble_config.get("mask_padding", 3)),
                split_connected_instances=bool(bubble_config.get("split_connected_instances", True)),
                split_max_neck_ratio=float(bubble_config.get("split_max_neck_ratio", 0.22)),
                split_min_boundary_coverage=float(
                    bubble_config.get("split_min_boundary_coverage", 0.7)
                ),
                split_min_boundary_run=float(bubble_config.get("split_min_boundary_run", 0.65)),
            )
        except Exception as exc:
            self._bubble_unavailable_reason = "segmentation_failed"
            logger.warning("Bubble segmentation failed; keeping %d text boxes separate: %s", len(output), exc)
            return _mark_bubble_grouping_unavailable(output, self._bubble_unavailable_reason)


def _bubble_grouping_config(config: dict) -> dict:
    value = config.get("bubble_grouping")
    if value is False:
        return {"enabled": False}
    if isinstance(value, dict):
        return {"enabled": True, **value}
    # Existing recommended configurations predate this key. Treat omission as
    # enabled while preserving an explicit opt-out.
    return {"enabled": True}


def _mark_bubble_grouping_unavailable(
    output: list[DetectionResult],
    reason: str,
) -> list[DetectionResult]:
    for result in output:
        result.metadata = {
            **result.metadata,
            "balloon_assignment": {
                "status": "unavailable",
                "bubble_id": None,
                "reason": reason,
            },
        }
    return output


def _mark_bubble_grouping_disabled(output: list[DetectionResult]) -> list[DetectionResult]:
    for result in output:
        result.metadata = {
            **result.metadata,
            "balloon_assignment": {
                "status": "disabled",
                "bubble_id": None,
                "reason": "configuration_disabled",
            },
        }
    return output
