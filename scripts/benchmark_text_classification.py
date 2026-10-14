"""Read-only classification smoke benchmark. Outputs never replace page assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from PIL import Image, ImageDraw  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services.detection.providers import PaddleTextDetector  # noqa: E402
from app.services.detection.text_roles import partition_text_roles  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--pages", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("tmp/text-classification"))
    args = parser.parse_args()
    settings = get_settings()
    database = settings.data_dir / "mangaflow.db"
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT id, filename, original_path FROM image_pages WHERE project_id=? ORDER BY order_index LIMIT ?", (args.project_id, args.pages)).fetchall()
        configuration = db.execute("SELECT config FROM model_configs WHERE kind='detection' AND enabled=1 AND is_default=1 LIMIT 1").fetchone()
    if not rows:
        raise ValueError("No pages found in the selected project")
    config = json.loads(configuration[0]) if configuration else {"device": "auto"}
    config["text_classification"] = {"enabled": True, "filter_sfx": True, "min_sfx_confidence": 0.6}
    detector = PaddleTextDetector(config)
    args.output.mkdir(parents=True, exist_ok=True)
    reports = []
    try:
        for page_id, filename, original in rows:
            source = settings.data_dir / original
            started = time.perf_counter()
            detected = detector.detect(source)
            elapsed = round(time.perf_counter() - started, 3)
            kept, report = partition_text_roles(detected)
            report.update(page_id=page_id, filename=filename, seconds=elapsed)
            report["regions"] = [{"bbox": r.bbox, "classification": r.metadata.get("text_classification", {}), "bubble_id": r.bubble_id} for r in kept]
            reports.append(report)
            with Image.open(source) as original_image:
                image = original_image.convert("RGB")
            draw = ImageDraw.Draw(image)
            for candidate in report["excluded_candidates"]:
                draw.line([tuple(p) for p in candidate["polygon"]] + [tuple(candidate["polygon"][0])], fill="#f97316", width=4)
                x, y, _, _ = candidate["bbox"]
                draw.text((x, max(0, y - 12)), f"SFX {candidate['classification'].get('confidence', 0):.2f}", fill="#c2410c")
            for region in kept:
                draw.line([tuple(p) for p in region.polygon] + [tuple(region.polygon[0])], fill="#3b82f6", width=2)
            image.thumbnail((1400, 1800))
            image.save(args.output / f"{page_id}.png")
            print(json.dumps({k: report[k] for k in ("filename", "status", "kept_count", "excluded_count", "uncertain_count", "seconds")}, ensure_ascii=False), flush=True)
        # The last source is still cached in the persistent worker.
        started = time.perf_counter()
        detector.detect(settings.data_dir / rows[-1][2])
        cached_seconds = round(time.perf_counter() - started, 3)
    finally:
        detector.unload()
    unchanged = hashlib.sha256(database.read_bytes()).hexdigest() == before
    result = {"database_unchanged": unchanged, "cached_last_page_seconds": cached_seconds, "pages": reports}
    (args.output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"database_unchanged": unchanged, "cached_last_page_seconds": cached_seconds}), flush=True)
    if not unchanged or any(page["status"] == "unavailable" for page in reports):
        raise RuntimeError("Benchmark failed: classification unavailable or database changed")


if __name__ == "__main__":
    main()
