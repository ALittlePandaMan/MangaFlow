"""Read-only, real-weight CTD parity/timing gate. Never calls a cloud API.

Use an isolated GPU process with the source data and weights mounted read-only.
The report covers full pages and the same small-text refinement windows used
by the application. It does not claim whole-pipeline or translation speedup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

import cv2
import numpy as np
from app.core.config import get_settings
from app.core.db_engine import read_only_connection
from app.services.execution import configure_native_threads
from app.services.inpainting.local_text import focus_windows, polygon_boxes
from app.services.inpainting.masking import create_text_mask, load_text_mask_source
from app.services.inpainting.text_segmentation import (
    MODEL_FILENAME,
    MODEL_SHA256,
    ComicTextSegmenter,
    learned_text_mask,
)
from sqlalchemy import text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.count < 1:
        parser.error("Use a new output directory and a positive page count")
    args.output.mkdir(parents=True)
    with read_only_connection(get_settings().database_url) as db:
        pages = list(db.execute(text(
            "SELECT id, original_path, width, height, order_index FROM image_pages WHERE project_id=:project ORDER BY order_index,id"), {"project": args.project}).mappings())
        pages = [dict(row) for row in pages]
        regions = {page["id"]: list(db.execute(text(
            "SELECT id,polygon,layout_data FROM text_regions WHERE image_id=:page ORDER BY reading_order,created_at,id"), {"page": page["id"]}).mappings()) for page in pages}
    if len(pages) < args.count:
        raise ValueError("Not enough source pages")
    # Include low/high text density, dark backgrounds and evenly spread pages.
    for page in pages:
        thumbnail = cv2.imread(str(args.data_dir / page["original_path"]), cv2.IMREAD_GRAYSCALE)
        if thumbnail is None:
            raise ValueError("Unreadable source page")
        page["darkness"] = float(np.mean(cv2.resize(thumbnail, (64, 64)) < 80))
    ordered = sorted(pages, key=lambda page: len(regions[page["id"]]))
    selected = []
    quarter = max(1, args.count // 4)
    for candidates in (ordered[:quarter], list(reversed(ordered))[:quarter],
                       sorted(pages, key=lambda page: page["darkness"], reverse=True)[:quarter],
                       [pages[i] for i in np.linspace(0, len(pages) - 1, args.count, dtype=int)], pages):
        for page in candidates:
            if page not in selected and len(selected) < args.count:
                selected.append(page)
    configure_native_threads()
    model = args.model_dir / MODEL_FILENAME
    settings = get_settings()
    settings.segmentation_backend = "opencv"
    started = time.perf_counter()
    cpu = ComicTextSegmenter(model)
    cpu_load = time.perf_counter() - started
    settings.segmentation_backend = "onnx_cuda"
    started = time.perf_counter()
    cuda = ComicTextSegmenter(model)
    cuda_load = time.perf_counter() - started
    if cuda.backend != "onnx_cuda":
        raise RuntimeError(f"CUDA acceptance requires CUDA: {cuda.fallback_reason}")
    report = {"scope":"ctd_full_page_local_refinement_and_masks_only", "model_sha256":MODEL_SHA256,
              "pages":[], "load_seconds":{"opencv":cpu_load,"onnx_cuda":cuda_load}}
    total_times = {"opencv":[], "onnx_cuda":[]}
    for page in selected:
        source = args.data_dir / page["original_path"]
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        image, lab = load_text_mask_source(source)
        rows = regions[page["id"]]
        polygons = [row["polygon"] for row in rows]
        boxes = polygon_boxes(polygons)
        scale = 1024 / max(image.shape[:2])
        small = [box for box in boxes if 0 < min(box[2:]) * scale < 24]
        windows = focus_windows(image.shape[:2], small)
        allowed = np.zeros(image.shape[:2], np.uint8)
        for x,y,w,h in small:
            allowed[max(0,y-4):min(image.shape[0],y+h+4),max(0,x-4):min(image.shape[1],x+w+4)] = 255
        results, timings = {}, {}
        for name, segmenter in (("opencv",cpu),("onnx_cuda",cuda)):
            started = time.perf_counter()
            probability = segmenter.predict(image)
            for x,y,w,h in windows:
                local = segmenter.predict(image[y:y+h,x:x+w])
                roi = np.s_[y:y+h,x:x+w]
                probability[roi] = np.where(allowed[roi] > 0,np.maximum(probability[roi],local),probability[roi])
            timings[name] = time.perf_counter() - started
            total_times[name].append(timings[name])
            results[name] = probability
        if cuda.backend != "onnx_cuda":
            raise RuntimeError("CUDA fell back during acceptance")
        masks, glyphs, manual = [], [], 0
        for row, polygon in zip(rows,polygons,strict=True):
            layout = row["layout_data"] or {}
            generation = layout.get("mask_generation", {})
            if generation.get("method") == "manual_mask":
                manual += 1
                continue
            region = np.zeros(image.shape[:2],np.uint8)
            cv2.fillPoly(region,[np.rint(polygon).astype(np.int32)],255)
            pair = []
            for name in ("opencv","onnx_cuda"):
                output = args.output / f"{row['id']}-{name}.png"
                create_text_mask(source,polygon,output,source_image=image,source_lab=lab,text_probability=results[name])
                pair.append(cv2.imread(str(output),cv2.IMREAD_GRAYSCALE))
            union = cv2.countNonZero(cv2.bitwise_or(*pair))
            changed = cv2.countNonZero(cv2.bitwise_xor(*pair))
            masks.append(changed / max(1,union))
            old_glyphs = learned_text_mask(results["opencv"],region)
            new_glyphs = learned_text_mask(results["onnx_cuda"],region)
            count, labels, stats, _ = cv2.connectedComponentsWithStats(old_glyphs)
            for index in range(1,count):
                if stats[index,cv2.CC_STAT_AREA] >= 3:
                    glyphs.append(bool(np.any(new_glyphs[labels == index])))
        item = {"page_id":page["id"],"order_index":page["order_index"],"shape":list(image.shape),
                "regions":len(rows),"local_windows":len(windows),"seconds":timings,
                "mask_max_changed_fraction":max(masks,default=0),"lost_glyph_components":glyphs.count(False),
                "manual_masks_skipped":manual,"source_unchanged":hashlib.sha256(source.read_bytes()).hexdigest()==digest}
        report["pages"].append(item)
        print(json.dumps(item),flush=True)
        (args.output / "partial.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    import onnxruntime as ort
    import torch

    report["runtime"] = {"onnxruntime":ort.__version__,"torch":torch.__version__,"cuda":torch.version.cuda,
                         "cudnn":torch.backends.cudnn.version(),"device":torch.cuda.get_device_name(0)}
    report["seconds"] = {name:{"total":sum(values),"median":statistics.median(values),
                        "p95":sorted(values)[min(len(values)-1,int(len(values)*.95))],"first_page":values[0]} for name,values in total_times.items()}
    report["quality_pass"] = all(page["mask_max_changed_fraction"] <= .001 and not page["lost_glyph_components"] and page["source_unchanged"] for page in report["pages"])
    report["throughput_gain"] = report["seconds"]["opencv"]["total"] / report["seconds"]["onnx_cuda"]["total"]
    report["timing_pass"] = report["throughput_gain"] >= 1.1 and report["seconds"]["onnx_cuda"]["p95"] <= report["seconds"]["opencv"]["p95"] * 1.2
    (args.output / "report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({key:value for key,value in report.items() if key != "pages"}),flush=True)


if __name__ == "__main__":
    main()
