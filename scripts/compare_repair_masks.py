"""Read-only comparison of saved balloon geometry; never changes project results.

Run from the repository root with the backend environment. Outputs are isolated
diagnostics, NOT ground-truth quality scores. Optional inference uses local LaMa.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import types
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from app.services.detection.artifacts import bubble_geometry_items, load_balloon_mask
from app.services.inpainting import masking
from app.services.inpainting.local_text import (
    LocalResidualConfirmer,
    inspection_area,
    polygon_boxes,
    refine_probability,
)
from app.services.inpainting.providers import SimpleLaMaInpainter
from app.services.inpainting.repair import repair_masks
from app.services.inpainting.text_segmentation import text_probability
from app.services.inpainting.verification import verify_and_retry
from app.storage.files import StorageManager
from PIL import Image, ImageDraw


def render_prepared(root: Path, out: Path, device: str, model_dir: Path, verify: bool = False) -> None:
    """Run local inference separately, e.g. in the existing model container."""
    plan = json.loads((out / "inference-plan.json").read_text(encoding="utf-8"))
    provider = SimpleLaMaInpainter({"device": device})
    for page in plan:
        source = root / page["source"]
        stem = page["stem"]
        old_clean, new_clean = out / f"{stem}-old-clean.png", out / f"{stem}-new-clean.png"
        provider.inpaint(source, out / f"{stem}-old-mask.png", old_clean, "background_complex")
        repair_masks(
            provider,
            source,
            out / f"{stem}-new-mask.png",
            new_clean,
            [(out / item["path"], item["metadata"]) for item in page["regions"]],
        )
        if verify:
            def predict(path):
                return text_probability(path, model_dir, out / "probability-cache")[0]
            reports = verify_and_retry(provider, source, new_clean,
                                       [(out / item["path"], item["metadata"]) for item in page["regions"]], predict,
                                       inspection_masks=[inspection_area(cv2.imread(str(source)).shape[:2], item.get("polygons", []))
                                                         for item in page["regions"]],
                                       confirm=LocalResidualConfirmer(source, model_dir, out / "probability-cache"))
            (out / f"{stem}-verification.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
        with Image.open(source) as original, Image.open(old_clean) as old, Image.open(new_clean) as new:
            sheet = Image.new("RGB", (original.width * 3, original.height + 30), "#eeeeee")
            draw = ImageDraw.Draw(sheet)
            for column, (label, picture) in enumerate(
                zip(
                    ("Original", "Previous algorithm + LaMa", "New algorithm + repair routing"),
                    (original, old, new),
                    strict=True,
                )
            ):
                draw.text((column * original.width + 10, 8), label, fill="black")
                sheet.paste(picture, (column * original.width, 30))
            sheet.save(out / f"{stem}-clean-comparison.png")
            # Close-ups show text-only regions without requiring inspection of
            # the surrounding page artwork. Only reliable balloon crops qualify.
            crops = []
            for item in page["regions"]:
                if item["metadata"].get("constraint", {}).get("status") not in {"applied", "relaxed"}:
                    continue
                selected = cv2.imread(str(out / item["path"]), 0)
                x, y, w, h = cv2.boundingRect(selected)
                if not w or not h:
                    continue
                box = (max(0, x-5), max(0, y-5), min(original.width, x+w+5), min(original.height, y+h+5))
                pair = Image.new("RGB", ((box[2]-box[0])*2, box[3]-box[1]), "white")
                pair.paste(original.crop(box), (0, 0))
                pair.paste(new.crop(box), (box[2]-box[0], 0))
                crops.append(pair)
                if len(crops) == 4:
                    break
            if crops:
                gallery = Image.new("RGB", (max(item.width for item in crops), sum(item.height+12 for item in crops)), "#dddddd")
                top = 0
                for crop in crops:
                    gallery.paste(crop, (0, top))
                    top += crop.height+12
                gallery.save(out / f"{stem}-balloon-details.png")
        print(f"Rendered {stem}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pages", type=int, default=4)
    parser.add_argument("--inpaint", action="store_true")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--text-segmentation", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--filename", help="Only compare this original filename")
    parser.add_argument("--page-id", help="Compare an exact saved page when filenames repeat across projects")
    parser.add_argument("--all-regions", action="store_true", help="Include text outside trusted balloons")
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Use an existing inference-plan.json, without reading the database or Git",
    )
    args = parser.parse_args()
    root = args.data_dir.resolve()
    out = args.output_dir.resolve()
    if out == root or root in out.parents:
        raise ValueError("Diagnostics must be outside the project data directory")
    out.mkdir(parents=True, exist_ok=True)
    if args.render_only:
        render_prepared(root, out, args.device, args.model_dir, args.verify)
        return
    baseline = types.ModuleType("baseline_masking")
    baseline_source = subprocess.check_output(
        ["git", "show", "HEAD:backend/app/services/inpainting/masking.py"],
        text=True,
    )
    exec(compile(baseline_source, "baseline_masking.py", "exec"), baseline.__dict__)
    baseline_revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    storage = StorageManager(root)
    connection = sqlite3.connect(f"file:{(root / 'mangaflow.db').as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    provider = SimpleLaMaInpainter({"device": args.device}) if args.inpaint else None
    results = []
    pages = []
    inference_plan = []
    for page in connection.execute("SELECT * FROM image_pages ORDER BY updated_at DESC").fetchall():
        if args.page_id and page["id"] != args.page_id:
            continue
        if args.filename and page["filename"] != args.filename:
            continue
        manifest = json.loads(page["metadata_json"] or "{}")
        geometry = manifest.get("bubble_geometry", {})
        expected_source = {"path": page["original_path"], "width": page["width"], "height": page["height"]}
        if geometry.get("source") != expected_source:
            continue
        instances = bubble_geometry_items(manifest)
        if not instances:
            continue
        source = storage.absolute(page["original_path"])
        image, lab = masking.load_text_mask_source(source)
        probability, segmentation = (text_probability(source, args.model_dir, out / "probability-cache")
                                     if args.text_segmentation else (None, {"status": "disabled"}))
        rows = connection.execute(
            "SELECT * FROM text_regions WHERE image_id=? ORDER BY reading_order", (page["id"],)
        ).fetchall()
        exclusion = np.zeros(image.shape[:2], np.uint8)
        boxes = []
        for row in rows:
            data = json.loads(row["layout_data"] or "{}").get("detection", {})
            polygons = data.get("line_grouping", {}).get("source_polygons") or [json.loads(row["polygon"])]
            boxes.extend(polygon_boxes(polygons))
            for polygon in polygons:
                cv2.fillPoly(exclusion, [np.rint(polygon).astype(np.int32)], 255)
        if args.text_segmentation:
            probability, local = refine_probability(source, args.model_dir, probability, boxes, out / "probability-cache")
            segmentation = {**segmentation, "local_refinement": local}
        page_dir = root / "projects" / page["project_id"] / "pages" / page["id"]
        old_union = np.zeros(image.shape[:2], np.uint8)
        new_union = np.zeros_like(old_union)
        new_regions = []
        region_polygons = {}
        tiles = []
        stem = f"{Path(page['filename']).stem}-{page['id'][:8]}"
        for row in rows:
            detection = json.loads(row["layout_data"] or "{}").get("detection", {})
            assignment = detection.get("balloon_assignment", {})
            reference = detection.get("balloon_constraint", {}).get("instance_id")
            trusted = (
                assignment.get("status") == "assigned"
                and assignment.get("balloon_confidence", 0) >= 0.75
                and assignment.get("core_coverage", 0) >= 0.85
                and reference in instances
            )
            if not trusted and not args.all_regions:
                continue
            balloon = (
                load_balloon_mask(storage, page_dir, instances[reference], image_shape=image.shape[:2])
                if trusted
                else None
            )
            polygons = detection.get("line_grouping", {}).get("source_polygons") or [json.loads(row["polygon"])]
            key = f"{stem}-{row['region_key']}"
            old_path, new_path = out / f"{key}-old.png", out / f"{key}-new.png"
            conservative = not trusted and assignment.get("status") != "disabled"
            baseline.create_text_mask_union(
                source,
                polygons,
                old_path,
                source_image=image,
                source_lab=lab,
                balloon_mask=balloon,
                conservative=conservative,
            )
            meta = masking.create_text_mask_union(
                source,
                polygons,
                new_path,
                source_image=image,
                source_lab=lab,
                balloon_mask=balloon,
                sample_exclusion=exclusion,
                conservative=conservative,
                text_probability=probability,
            )
            meta["text_segmentation"] = segmentation
            old, new = cv2.imread(str(old_path), 0), cv2.imread(str(new_path), 0)
            meta["automatic_mask_sha256"] = hashlib.sha256(new.tobytes()).hexdigest()
            old_union |= old
            new_union |= new
            new_regions.append((new_path, meta))
            region_polygons[new_path.name] = polygons
            results.append(
                {
                    "page": page["filename"],
                    "region": row["region_key"],
                    "method": meta["method"],
                    "status": meta.get("constraint", {}).get("status"),
                    "flat": "repair" in meta,
                    "old_area": int(np.count_nonzero(old)),
                    "new_area": int(np.count_nonzero(new)),
                    "outside_balloon": int(np.count_nonzero((new > 0) & (balloon == 0))) if trusted else 0,
                    "outline_protected": meta.get("constraint", {}).get("outline_protected_pixels", 0),
                    "learned_added_pixels": meta.get("learned_added_pixels", 0),
                }
            )
            # Each row is original / baseline coverage / new coverage, not a
            # claimed clean image. Review the reduced and expanded areas alike.
            if trusted:
                bx, by, bw, bh = cv2.boundingRect(balloon)
            else:
                bx, by, bw, bh = cv2.boundingRect(np.rint(np.concatenate(polygons)).astype(np.int32))
            roi = np.s_[by : by + bh, bx : bx + bw]
            original = cv2.cvtColor(image[roi], cv2.COLOR_BGR2RGB)
            crops = [original]
            for mask in (old, new):
                overlay = original.copy()
                selection = mask[roi] > 0
                overlay[selection] = (overlay[selection] * 0.45 + np.array([255, 60, 60]) * 0.55).astype(np.uint8)
                crops.append(overlay)
            tiles.append((key, [Image.fromarray(crop) for crop in crops]))
        if not new_regions:
            continue
        old_combined, new_combined = out / f"{stem}-old-mask.png", out / f"{stem}-new-mask.png"
        cv2.imwrite(str(old_combined), old_union)
        cv2.imwrite(str(new_combined), new_union)
        inference_plan.append(
            {
                "stem": stem,
                "source": page["original_path"],
                "regions": [{"path": path.name, "metadata": meta, "polygons": region_polygons[path.name]}
                            for path, meta in new_regions],
            }
        )
        if provider is not None:
            provider.inpaint(source, old_combined, out / f"{stem}-old-clean.png", "background_complex")
            repair_masks(provider, source, new_combined, out / f"{stem}-new-clean.png", new_regions)
        for offset in range(0, len(tiles), 8):
            section = tiles[offset : offset + 8]
            sheet = Image.new("RGB", (900, len(section) * 300), "#eeeeee")
            draw = ImageDraw.Draw(sheet)
            for index, (key, crops) in enumerate(section):
                draw.text((8, index * 300 + 5), f"{key}: original / baseline / new", fill="black")
                for column, crop in enumerate(crops):
                    crop.thumbnail((290, 270))
                    sheet.paste(crop, (column * 300 + (300 - crop.width) // 2, index * 300 + 25))
            sheet.save(out / f"{stem}-compare-{offset // 8}.png")
        pages.append(stem)
        if len(pages) >= args.pages:
            break
    connection.close()
    (out / "inference-plan.json").write_text(json.dumps(inference_plan, indent=2), encoding="utf-8")
    report = {
        "baseline_revision": baseline_revision,
        "pages": pages,
        "regions": len(results),
        "statuses": dict(Counter(item["status"] for item in results)),
        "flat_regions": sum(item["flat"] for item in results),
        "outside_balloon_pixels": sum(item["outside_balloon"] for item in results),
        "samples": results,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "samples"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
