"""Read-only unified-layout smoke test; writes previews only to --output.

No pipeline actions, database updates, page masks or translations are invoked.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"backend"))

import cv2
from app.core.config import get_settings
from app.core.db_engine import read_only_connection
from app.services.detection.layout import RFDetrTextDetector
from app.services.detection.text_roles import partition_text_roles
from PIL import Image, ImageDraw
from sqlalchemy import text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", action="append", default=[])
    parser.add_argument("--page-id", action="append", default=[])
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--output", type=Path, default=Path("tmp/unified-layout"))
    args = parser.parse_args()
    settings = get_settings()
    with read_only_connection(settings.database_url) as db:
        rows = []
        for project in args.project_id:
            rows.extend(db.execute(text("SELECT id,filename,original_path FROM image_pages WHERE project_id=:project ORDER BY order_index LIMIT :limit"), {"project": project, "limit": args.pages}).fetchall())
        for page in args.page_id:
            rows.extend(db.execute(text("SELECT id,filename,original_path FROM image_pages WHERE id=:page"), {"page": page}).fetchall())
    if not rows:
        raise ValueError("No selected sample pages")
    args.output.mkdir(parents=True, exist_ok=True)
    detector = RFDetrTextDetector({"device": args.device})
    start = time.perf_counter()
    reports = []
    try:
        detector.ensure_loaded()
        load_seconds = round(time.perf_counter()-start, 3)
        print(json.dumps({"model_load_seconds": load_seconds}), flush=True)
        for page_id, filename, original in rows:
            source = settings.data_dir/original
            start = time.perf_counter()
            detected = detector.detect(source)
            elapsed = round(time.perf_counter()-start, 3)
            kept, roles = partition_text_roles(detected)
            with Image.open(source) as opened:
                image = opened.convert("RGB")
            draw = ImageDraw.Draw(image)
            bubbles = set()
            for item in detected:
                excluded = item.metadata["text_classification"]["excluded"]
                draw.line([tuple(point) for point in item.polygon]+[tuple(item.polygon[0])], fill="#ef4444" if excluded else "#2563eb", width=3)
                if item.balloon_mask_id and item.balloon_mask_id not in bubbles:
                    bubbles.add(item.balloon_mask_id)
                    contours, _ = cv2.findContours(item.balloon_mask.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    ox, oy = item.balloon_mask_origin
                    for contour in contours:
                        points = [(int(x)+ox, int(y)+oy) for x, y in contour[:, 0]]
                        if len(points) > 2:
                            draw.line(points+[points[0]], fill="#16a34a", width=2)
            image.thumbnail((1400, 1800))
            image.save(args.output/f"{page_id}.png")
            report = {"page_id": page_id, "filename": filename, "seconds": elapsed,
                      "text_regions": len(kept), "excluded_sfx": roles["excluded_count"],
                      "associated_text_regions": sum(bool(item.bubble_id) for item in kept), "associated_bubbles": len(bubbles),
                      "regions": [{"bbox": item.bbox, "bubble_id": item.bubble_id, "orientation": item.orientation,
                                   "angle": item.metadata["text_angle"]} for item in kept]}
            reports.append(report)
            print(json.dumps({key: value for key, value in report.items() if key != "regions"}, ensure_ascii=False), flush=True)
    finally:
        detector.unload()
    (args.output/"report.json").write_text(json.dumps({"read_only": True, "model_load_seconds": load_seconds, "pages": reports}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
