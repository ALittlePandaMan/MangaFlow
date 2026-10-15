from __future__ import annotations

from pathlib import Path

import cv2
from app.services.base import DetectionResult, ProviderCapabilities, TextDetector
from app.utils.geometry import bbox_to_polygon


class OpenCVTextDetector(TextDetector):
    """Dependency-free fallback detector based on clustered dark glyph components.

    It is intentionally conservative and serves as a runnable fallback. Production
    installations should use the unified RF-DETR layout detector.
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
                        "detector": "opencv-fallback",
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
