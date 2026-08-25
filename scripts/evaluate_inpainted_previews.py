from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from app.services.detection.providers import PaddleTextDetector


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure text removal and outside-mask preservation.")
    parser.add_argument(
        "--case",
        action="append",
        nargs=3,
        metavar=("SOURCE", "MASK", "CLEAN"),
        required=True,
    )
    parser.add_argument("--device", default="auto")
    return parser.parse_args()


def _overlapping_detections(
    detector: PaddleTextDetector,
    image_path: Path,
    mask: np.ndarray,
) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    for region in detector.detect(image_path):
        polygon = np.rint(np.asarray(region.polygon, dtype=np.float32)).astype(np.int32)
        if len(polygon) < 3:
            continue
        region_mask = np.zeros_like(mask)
        cv2.fillPoly(region_mask, [polygon], 255)
        area = cv2.countNonZero(region_mask)
        overlap = cv2.countNonZero(cv2.bitwise_and(region_mask, mask))
        if area and overlap / area >= 0.2:
            matches.append(
                {
                    "bbox": [round(float(value), 2) for value in region.bbox],
                    "confidence": round(float(region.confidence), 3),
                    "mask_overlap": round(overlap / area, 3),
                }
            )
    return matches


def main() -> int:
    args = _arguments()
    detector = PaddleTextDetector(
        {
            "device": args.device,
            "language": "japan",
            "ocr_version": "PP-OCRv5",
            "bubble_grouping": False,
        }
    )
    results = []
    for source_value, mask_value, clean_value in args.case:
        source_path, mask_path, clean_path = map(Path, (source_value, mask_value, clean_value))
        source = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        clean = cv2.imread(str(clean_path), cv2.IMREAD_COLOR)
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if source is None or clean is None or mask is None or source.shape != clean.shape:
            raise ValueError(f"Cannot read matching evaluation case: {source_path}")
        binary = np.where(mask > 0, 255, 0).astype(np.uint8)
        outside = binary == 0
        gray_source = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
        gray_clean = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)
        dark_before = int(np.count_nonzero((gray_source < 128) & (binary > 0)))
        dark_after = int(np.count_nonzero((gray_clean < 128) & (binary > 0)))
        delta = np.max(np.abs(clean.astype(np.int16) - source.astype(np.int16)), axis=2)
        before_boxes = _overlapping_detections(detector, source_path, binary)
        after_boxes = _overlapping_detections(detector, clean_path, binary)
        results.append(
            {
                "source": source_path.name,
                "mask_pixels": cv2.countNonZero(binary),
                "dark_pixels_before": dark_before,
                "dark_pixels_after": dark_after,
                "dark_pixel_reduction": round(1 - dark_after / max(1, dark_before), 4),
                "overlapping_text_boxes_before": len(before_boxes),
                "overlapping_text_boxes_after": len(after_boxes),
                "remaining_text_boxes": after_boxes,
                "changed_pixels_outside_mask": int(np.count_nonzero((delta > 0) & outside)),
                "maximum_delta_outside_mask": int(delta[outside].max(initial=0)),
            }
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
