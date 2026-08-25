from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from app.services.base import DetectionResult
from app.services.detection.bubbles import OnnxBubbleSegmenter
from app.services.detection.grouping import MIN_TRUSTED_BALLOON_CONFIDENCE, group_text_regions_by_bubbles
from app.services.inpainting.masking import (
    create_text_mask,
    create_text_mask_union,
    load_text_mask_source,
)
from app.utils.geometry import polygon_to_bbox


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate balloon-constrained repair masks against existing MangaFlow pages."
    )
    parser.add_argument("--database", type=Path, default=Path("data/mangaflow.db"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("models/bubbles/manga109_segmentation_bubble_1024.onnx"),
    )
    parser.add_argument("--project-id")
    parser.add_argument("--filenames", nargs="*", default=["00000005.jpg", "00000006.jpg", "00000007.jpg", "00000008.jpg"])
    parser.add_argument("--output-dir", type=Path, help="Optionally save page-level raw and constrained mask unions")
    parser.add_argument("--summary-only", action="store_true", help="Omit per-balloon samples from JSON output")
    return parser.parse_args()


def _dictionary(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return list(value) if isinstance(value, list) else []


def _source_detections(rows: list[sqlite3.Row]) -> list[DetectionResult]:
    detections: list[DetectionResult] = []
    for row in rows:
        polygon = _list(row["polygon"])
        layout = _dictionary(row["layout_data"])
        detection = _dictionary(layout.get("detection"))
        grouping = _dictionary(detection.get("line_grouping"))
        polygons = _list(grouping.get("source_polygons")) or [polygon]
        confidences = _list(grouping.get("source_confidences"))
        orientations = _list(grouping.get("source_orientations"))
        for index, source_polygon in enumerate(polygons):
            if not isinstance(source_polygon, list) or len(source_polygon) < 3:
                continue
            confidence = (
                float(confidences[index])
                if index < len(confidences) and isinstance(confidences[index], (int, float))
                else float(row["confidence"])
            )
            orientation = (
                str(orientations[index])
                if index < len(orientations) and isinstance(orientations[index], str)
                else str(row["orientation"])
            )
            detections.append(
                DetectionResult(
                    polygon=source_polygon,
                    bbox=polygon_to_bbox(source_polygon),
                    confidence=confidence,
                    orientation=orientation,
                )
            )
    return detections


def _full_balloon_mask(region: DetectionResult, image_shape: tuple[int, int]) -> np.ndarray:
    output = np.zeros(image_shape, dtype=np.uint8)
    if region.balloon_mask is None or region.balloon_mask_origin is None:
        return output
    x, y = region.balloon_mask_origin
    height, width = region.balloon_mask.shape
    output[y : y + height, x : x + width] = np.where(region.balloon_mask > 0, 255, 0).astype(np.uint8)
    return output


def _shape_metrics(mask: np.ndarray) -> dict[str, float]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return {"circularity": 0.0, "solidity": 0.0, "extent": 0.0, "aspect": 0.0}
    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    perimeter = float(cv2.arcLength(contour, True))
    hull_area = float(cv2.contourArea(cv2.convexHull(contour)))
    _, _, width, height = cv2.boundingRect(contour)
    return {
        "circularity": round(4 * np.pi * area / max(1.0, perimeter * perimeter), 3),
        "solidity": round(area / max(1.0, hull_area), 3),
        "extent": round(area / max(1.0, width * height), 3),
        "aspect": round(width / max(1.0, height), 3),
    }


def _constraint_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    constraint = metadata.get("constraint")
    if not isinstance(constraint, dict):
        return {"status": "missing"}
    components = constraint.get("components")
    if isinstance(components, list):
        glyph_retentions = [
            float(item.get("glyph_safe_retention", 0.0))
            for item in components
            if isinstance(item, dict)
        ]
        raw_areas = [int(item.get("raw_mask_area", 0)) for item in components if isinstance(item, dict)]
        final_areas = [int(item.get("final_mask_area", 0)) for item in components if isinstance(item, dict)]
        margins = [int(item.get("safe_margin_px", 0)) for item in components if isinstance(item, dict)]
        return {
            "status": constraint.get("status"),
            "glyph_retention": round(min(glyph_retentions), 4) if glyph_retentions else 0.0,
            "raw_area": sum(raw_areas),
            "final_area": sum(final_areas),
            "margin_px": max(margins, default=0),
            "outside_after": int(constraint.get("outside_pixels_after", 0)),
        }
    return {
        "status": constraint.get("status"),
        "glyph_retention": float(constraint.get("glyph_safe_retention", 0.0)),
        "glyph_balloon_retention": float(constraint.get("glyph_balloon_retention", 0.0)),
        "raw_area": int(constraint.get("raw_mask_area", 0)),
        "final_area": int(constraint.get("final_mask_area", 0)),
        "margin_px": int(constraint.get("safe_margin_px", 0)),
        "outside_after": int(constraint.get("outside_pixels_after", 0)),
    }


def main() -> int:
    args = _arguments()
    database = args.database.resolve()
    data_dir = args.data_dir.resolve()
    model = args.model.resolve()
    if not database.is_file() or not model.is_file():
        raise SystemExit("Database or balloon model is missing")

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    where = [f"filename IN ({','.join('?' for _ in args.filenames)})"]
    parameters: list[Any] = list(args.filenames)
    if args.project_id:
        where.append("project_id = ?")
        parameters.append(args.project_id)
    pages = connection.execute(
        f"SELECT * FROM image_pages WHERE {' AND '.join(where)} ORDER BY filename, project_id",
        parameters,
    ).fetchall()
    segmenter = OnnxBubbleSegmenter({"model_path": str(model)})
    samples: list[dict[str, Any]] = []
    outside_count = 0
    preview_pages: list[dict[str, str]] = []
    output_directory = args.output_dir.resolve() if args.output_dir else None
    if output_directory is not None:
        output_directory.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mangaflow-balloon-eval-") as temporary_name:
        temporary = Path(temporary_name)
        for page in pages:
            image_path = data_dir / str(page["original_path"])
            if not image_path.is_file():
                continue
            rows = connection.execute(
                "SELECT * FROM text_regions WHERE image_id = ? ORDER BY reading_order",
                (page["id"],),
            ).fetchall()
            detections = _source_detections(rows)
            if not detections:
                continue
            bubbles = segmenter.segment(image_path)
            grouped = group_text_regions_by_bubbles(
                detections,
                bubbles,
                page_key=str(image_path.resolve()),
                image_path=image_path,
            )
            image, lab = load_text_mask_source(image_path)
            page_raw_union = np.zeros(image.shape[:2], dtype=np.uint8)
            page_final_union = np.zeros(image.shape[:2], dtype=np.uint8)
            for index, region in enumerate(grouped):
                assignment = _dictionary(region.metadata.get("balloon_assignment"))
                if assignment.get("status") != "assigned" or region.balloon_mask is None:
                    outside_count += 1
                    continue
                grouping = _dictionary(region.metadata.get("line_grouping"))
                polygons = _list(grouping.get("source_polygons")) or [region.polygon]
                full_balloon = _full_balloon_mask(region, image.shape[:2])
                raw_path = temporary / f"{page['id']}-{index}-raw.png"
                final_path = temporary / f"{page['id']}-{index}-final.png"
                creator = create_text_mask_union if len(polygons) > 1 else create_text_mask
                geometry: Any = polygons if len(polygons) > 1 else polygons[0]
                creator(
                    image_path,
                    geometry,
                    raw_path,
                    source_image=image,
                    source_lab=lab,
                )
                metadata = creator(
                    image_path,
                    geometry,
                    final_path,
                    source_image=image,
                    source_lab=lab,
                    balloon_mask=full_balloon,
                    balloon_context={"bubble_id": region.bubble_id},
                )
                raw = cv2.imread(str(raw_path), cv2.IMREAD_GRAYSCALE)
                final = cv2.imread(str(final_path), cv2.IMREAD_GRAYSCALE)
                if raw is None or final is None:
                    continue
                page_raw_union = cv2.bitwise_or(page_raw_union, raw)
                page_final_union = cv2.bitwise_or(page_final_union, final)
                summary = _constraint_summary(metadata)
                unsafe = int(np.count_nonzero((final > 0) & (full_balloon == 0)))
                samples.append(
                    {
                        "filename": page["filename"],
                        "project_id": page["project_id"],
                        "bubble_id": region.bubble_id,
                        "bbox": [round(float(value), 2) for value in region.bbox],
                        "source_count": len(polygons),
                        "bubble_confidence": float(assignment.get("balloon_confidence", 0.0)),
                        "assignment_coverage": float(assignment.get("coverage", 0.0)),
                        "core_coverage": float(assignment.get("core_coverage", 0.0)),
                        "production_eligible": bool(
                            float(assignment.get("balloon_confidence", 0.0))
                            >= MIN_TRUSTED_BALLOON_CONFIDENCE
                            and float(assignment.get("core_coverage", 0.0)) >= 0.85
                        ),
                        **_shape_metrics(full_balloon),
                        **summary,
                        "raw_outside_balloon": int(np.count_nonzero((raw > 0) & (full_balloon == 0))),
                        "unsafe_final_pixels": unsafe,
                    }
                )
            if output_directory is not None and cv2.countNonZero(page_final_union):
                stem = f"{Path(str(page['filename'])).stem}-{str(page['id'])[:8]}"
                raw_output = output_directory / f"{stem}-raw-mask.png"
                final_output = output_directory / f"{stem}-safe-mask.png"
                if not cv2.imwrite(str(raw_output), page_raw_union) or not cv2.imwrite(
                    str(final_output), page_final_union
                ):
                    raise ValueError("Cannot write evaluation mask previews")
                preview_pages.append(
                    {
                        "filename": str(page["filename"]),
                        "image_path": str(image_path),
                        "raw_mask_path": str(raw_output),
                        "safe_mask_path": str(final_output),
                    }
                )

    connection.close()
    statuses = Counter(str(item["status"]) for item in samples)
    uncertain_samples = sorted(
        (
            {
                key: item[key]
                for key in (
                    "filename",
                    "project_id",
                    "bubble_id",
                    "bbox",
                    "source_count",
                    "assignment_coverage",
                    "glyph_retention",
                    "glyph_balloon_retention",
                    "status",
                )
            }
            for item in samples
            if item["status"] in {"partial", "fallback"}
        ),
        key=lambda item: float(item.get("glyph_balloon_retention", 0.0)),
    )
    report = {
        "pages_evaluated": len({(item["project_id"], item["filename"]) for item in samples}),
        "total_grouped_regions": len(samples) + outside_count,
        "assigned_samples": len(samples),
        "assignment_rate": round(len(samples) / max(1, len(samples) + outside_count), 4),
        "production_eligible_samples": sum(bool(item["production_eligible"]) for item in samples),
        "outside_or_unassigned_regions": outside_count,
        "status_counts": dict(statuses),
        "unsafe_final_pixels": sum(int(item["unsafe_final_pixels"]) for item in samples),
        "raw_outside_balloon_pixels": sum(int(item["raw_outside_balloon"]) for item in samples),
        "minimum_glyph_retention": min((float(item["glyph_retention"]) for item in samples), default=0.0),
        "minimum_glyph_balloon_retention": min(
            (float(item.get("glyph_balloon_retention", 0.0)) for item in samples),
            default=0.0,
        ),
        "fallbacks": [item for item in samples if item["status"] == "fallback"],
        "uncertain_sample_count": len(uncertain_samples),
        "uncertain_samples": uncertain_samples[:8] if args.summary_only else uncertain_samples,
        "preview_page_count": len(preview_pages),
    }
    if not args.summary_only:
        report["samples"] = samples
        report["preview_pages"] = preview_pages
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["unsafe_final_pixels"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
