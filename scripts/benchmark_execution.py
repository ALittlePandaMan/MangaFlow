"""Isolated local-model benchmark; source DB is opened read-only and backed up.

Copies selected page assets into a new directory. Never calls a cloud provider.
Each measured run restores the same inputs. OCR examples are for human review,
not accuracy scores: saved OCR text is not ground truth.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from copy import deepcopy
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-data", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--cache-from", type=Path, help="Reuse only an earlier isolated probability cache")
    args = parser.parse_args()
    source_root, output = args.source_data.resolve(), args.output.resolve()
    if output.exists() or output == source_root or source_root in output.parents:
        raise ValueError("Output must be a fresh directory outside source data")
    output.mkdir(parents=True)
    copy_root = output / "data"
    copy_root.mkdir()
    source_db = sqlite3.connect(f"file:{(source_root / 'mangaflow.db').as_posix()}?mode=ro", uri=True)
    source_db.row_factory = sqlite3.Row
    snapshot_db = sqlite3.connect(copy_root / "mangaflow.db")
    source_db.backup(snapshot_db)
    pages = source_db.execute(
        "SELECT p.* FROM image_pages p WHERE p.project_id=? AND EXISTS "
        "(SELECT 1 FROM text_regions r WHERE r.image_id=p.id) ORDER BY p.order_index LIMIT ?",
        (args.project, args.pages),
    ).fetchall()
    if not pages:
        raise ValueError("Project has no regions to benchmark")
    for page in pages:
        directory = Path(page["original_path"]).parent.parent
        shutil.copytree(source_root / directory, copy_root / directory)
    for name in (".secret_key", "fonts"):
        origin, destination = source_root / name, copy_root / name
        if origin.is_dir():
            shutil.copytree(origin, destination)
        elif origin.is_file():
            shutil.copy2(origin, destination)
    # An unmeasured warmup populates only these pages' probability caches.
    snapshot_db.close()
    if args.cache_from:
        cache = args.cache_from.resolve() / "data" / "cache" / "text-segmentation"
        if cache.is_dir():
            shutil.copytree(cache, copy_root / "cache" / "text-segmentation")
    print("Isolated database and page assets ready", flush=True)
    os.environ.update(
        MANGAFLOW_DATA_DIR=str(copy_root),
        MANGAFLOW_DATABASE_URL=f"sqlite:///{copy_root / 'mangaflow.db'}",
        MANGAFLOW_MODEL_DIR=str(args.model_dir.resolve()),
        MANGAFLOW_MODEL_MANIFEST_PATH="",
        MANGAFLOW_ENVIRONMENT_FILE_PATH=str(output / "unused.env"),
        MANGAFLOW_AUTO_PROVISION_MODELS="false",
        MANGAFLOW_TASK_CONCURRENCY="4",
        MANGAFLOW_NATIVE_THREADS="2",
    )

    import psutil
    from app.core.database import SessionLocal, initialize_database
    from app.models import ImagePage, ProcessingTask
    from app.services.editor_history import PAGE_FIELDS, restore_page_state
    from app.services.execution import ExecutionPolicy, configure_native_threads, execution_runtime
    from app.services.regions import region_snapshot
    from app.storage import get_storage
    from app.tasks.manager import TaskManager
    from PIL import Image, ImageDraw

    initialize_database()
    configure_native_threads()
    print("Runtime ready", flush=True)
    runtime, storage = execution_runtime(), get_storage()
    ids = [p["id"] for p in pages]
    with SessionLocal() as db:
        baseline = {}
        for identifier in ids:
            page = db.get(ImagePage, identifier)
            baseline[identifier] = {
                "page": {key: deepcopy(getattr(page, key)) for key in PAGE_FIELDS},
                "regions": [{"id": r.id, "region_key": r.region_key, **region_snapshot(r)} for r in page.regions],
            }

    def source_fingerprint():
        result = {}
        for page in pages:
            result[page["id"]] = dict(
                source_db.execute("SELECT * FROM image_pages WHERE id=?", (page["id"],)).fetchone()
            )
            result[page["id"]]["regions"] = [
                dict(r)
                for r in source_db.execute("SELECT * FROM text_regions WHERE image_id=? ORDER BY id", (page["id"],))
            ]
        return hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    original_fingerprint = source_fingerprint()
    report = {
        "project": args.project,
        "pages": [{"id": p["id"], "filename": p["filename"], "pixels": p["width"] * p["height"]} for p in pages],
        "local_only": True,
        "source_fingerprint": original_fingerprint,
        "runs": [],
        "ocr": [],
    }

    def pixels_digest(path):
        with Image.open(path) as image:
            return hashlib.sha256(image.tobytes()).hexdigest()

    def outputs():
        with SessionLocal() as db:
            result = {}
            for identifier in ids:
                page = db.get(ImagePage, identifier)
                result[identifier] = {
                    "text": {r.region_key: r.source_text for r in page.regions},
                    "masks": {
                        r.region_key: pixels_digest(storage.absolute(r.pixel_mask_path))
                        for r in page.regions
                        if r.pixel_mask_path
                    },
                    "clean": pixels_digest(storage.absolute(page.clean_path)) if page.clean_path else None,
                }
            return result

    async def run(mode, stage, end, measured=True):
        with SessionLocal() as db:
            for identifier in ids:
                restore_page_state(db, db.get(ImagePage, identifier), baseline[identifier])
            db.commit()
        runtime.update(ExecutionPolicy(mode=mode, max_pages=4, cpu_workers=4), persist=False)
        runtime.pages.peak = runtime.cpu.peak = 0
        manager = TaskManager()
        manager.start()
        with SessionLocal() as db:
            tasks = [
                ProcessingTask(
                    project_id=args.project,
                    image_id=identifier,
                    task_type=stage,
                    status="QUEUED",
                    payload={
                        "start_stage": stage,
                        "end_stage": end,
                        "force": True,
                        **({"provider": "manga-ocr"} if stage == "ocr" else {}),
                    },
                )
                for identifier in ids
            ]
            db.add_all(tasks)
            db.commit()
            task_ids = [t.id for t in tasks]
        stop = threading.Event()
        measurements = []

        def sample():
            while not stop.wait(0.1):
                mem = psutil.Process().memory_info().rss
                state = runtime.snapshot()
                measurements.append((mem, state["pages"]["active"], state["cpu"]["active"]))

        monitor = threading.Thread(target=sample, daemon=True)
        monitor.start()
        begin = time.perf_counter()
        for identifier in task_ids:
            manager.dispatch(identifier)
        await asyncio.sleep(0.05)
        await asyncio.gather(*list(manager.running.values()))
        seconds = time.perf_counter() - begin
        await manager.shutdown()
        stop.set()
        monitor.join()
        with SessionLocal() as db:
            states = [db.get(ProcessingTask, identifier) for identifier in task_ids]
            statuses = [t.status for t in states]
            errors = [t.error_message for t in states if t.error_message]
        record = {
            "mode": mode,
            "stage": stage,
            "end_stage": end,
            "seconds": round(seconds, 3),
            "statuses": statuses,
            "errors": errors,
            "rss_peak_mb": round(max((x[0] for x in measurements), default=0) / 1024**2, 1),
            "pages_peak": runtime.pages.peak,
            "cpu_peak": runtime.cpu.peak,
            "cpu_final_limit": runtime.cpu.limit,
            "outputs": outputs(),
        }
        print(json.dumps({k: v for k, v in record.items() if k != "outputs"}, ensure_ascii=False), flush=True)
        if measured:
            report["runs"].append(record)
            (output / "report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        if errors or statuses != ["COMPLETED"] * len(ids):
            raise RuntimeError(f"Benchmark failed: {statuses} {errors}")
        return record

    def ocr_sheets():
        for identifier in ids:
            with SessionLocal() as db:
                page = db.get(ImagePage, identifier)
                regions = list(page.regions)
                with Image.open(storage.absolute(page.original_path)) as picture:
                    original = picture.convert("RGB")
                originals = {r["id"]: r for r in baseline[identifier]["regions"]}
                rows = []
                for region in regions:
                    box = region.bbox
                    crop = original.crop(
                        (
                            max(0, int(box[0] - 4)),
                            max(0, int(box[1] - 4)),
                            min(page.width, int(box[0] + box[2] + 4)),
                            min(page.height, int(box[1] + box[3] + 4)),
                        )
                    )
                    crop.thumbnail((250, 300))
                    sheet = Image.new("RGB", (300, max(70, crop.height + 40)), "white")
                    sheet.paste(crop, (0, 30))
                    ImageDraw.Draw(sheet).text((8, 8), region.region_key, fill="black")
                    rows.append(sheet)
                    report["ocr"].append(
                        {
                            "page": page.filename,
                            "region": region.region_key,
                            "bbox": box,
                            "recognized": region.source_text,
                            "previous": originals[region.id]["source_text"],
                            "image": f"ocr-{identifier}-{region.region_key}.png",
                        }
                    )
                    sheet.save(output / f"ocr-{identifier}-{region.region_key}.png")
                for start in range(0, len(rows), 12):
                    chunk = rows[start : start + 12]
                    height = max(r.height for r in chunk)
                    mosaic = Image.new("RGB", (1200, height * 3), "#eeeeee")
                    for index, row in enumerate(chunk):
                        mosaic.paste(row, ((index % 4) * 300, (index // 4) * height))
                    mosaic.save(output / f"contact-{page.filename}-{start // 12}.png")
        (output / "ocr-review.json").write_text(json.dumps(report["ocr"],ensure_ascii=False,indent=2),encoding="utf-8")

    async def benchmark():
        # Warm models and probability caches outside measured timings.
        if args.ocr:
            await run("serial", "ocr", "ocr", False)
            ocr_sheets()
        await run("serial", "mask", "inpainting" if args.repair else "mask", False)
        for repeat in range(args.repeats):
            order = ("serial", "auto") if repeat % 2 == 0 else ("auto", "serial")
            for mode in order:
                await run(mode, "mask", "inpainting" if args.repair else "mask")
                if args.ocr:
                    await run(mode, "ocr", "ocr")

    asyncio.run(benchmark())
    report["source_unchanged"] = source_fingerprint() == original_fingerprint
    stages = set(r["stage"] for r in report["runs"])
    report["comparisons"] = {}
    for stage in stages:
        runs = [r for r in report["runs"] if r["stage"] == stage]
        first = runs[0]["outputs"]
        report["comparisons"][stage] = {
            "outputs_identical": all(r["outputs"] == first for r in runs),
            "serial_seconds": [r["seconds"] for r in runs if r["mode"] == "serial"],
            "auto_seconds": [r["seconds"] for r in runs if r["mode"] == "auto"],
        }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"source_unchanged": report["source_unchanged"], "comparisons": report["comparisons"]}, ensure_ascii=False
        ),
        flush=True,
    )
    source_db.close()


if __name__ == "__main__":
    main()
