from __future__ import annotations

import logging
from pathlib import Path

import cv2
from app.services.base import DetectionResult, ProviderCapabilities, ProviderError, TextDetector
from app.services.device import is_accelerator_error, release_paddle_cuda, resolve_paddle_device
from app.services.paddle import extract_paddle_lines
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
        extra={"polygon": True, "optional_dependency": "paddleocr"},
    )

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._model = None
        self._device = "cpu"

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
        return output
