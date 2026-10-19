"""One isolated 337-page original-image to rendered-translation acceptance run.

Prepare never starts workers or paid requests. The only paid dispatch requires
--allow-paid-requests and is intent-recorded before POST; never automatically
repeat an uncertain submission. Originals and production data are read-only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import psutil
from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.core.db_engine import create_database_engine
from app.core.migrations import upgrade_database
from app.models import (
    EditorHistoryEntry,
    ImagePage,
    ModelConfig,
    ProcessingTask,
    Project,
    StageCheckpoint,
    TaskExecutionRun,
    TaskWorkflow,
    TextRegion,
)
from app.services.database_transfer import file_digest
from app.services.workflow_state import decode, fingerprint, record
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.orm import Session

PROJECT_ID = "4a700a76-92e6-42da-974f-764ecf943089"
STAGES = ("detection", "ocr", "translation", "mask", "inpainting", "rendering")
SCOPE = "337_original_images_full_detection_ocr_translation_mask_repair_render_save"


def write(path, value, *, exclusive=False):
    with path.open("x" if exclusive else "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def prepare(args):
    settings = get_settings()
    root = settings.data_dir.resolve()
    if engine.dialect.name != "postgresql" or not (engine.url.database or "").startswith("mangaflow_test_full_"):
        raise ValueError("A fresh isolated mangaflow_test_full_* database is required")
    if args.output.exists() or root == args.source.resolve():
        raise ValueError("Fresh evidence and separate writable data directory required")
    args.output.mkdir(parents=True)
    # Closed, previously verified migration snapshot. Copy before upgrading;
    # neither production PostgreSQL nor the read-only mounted snapshot changes.
    snapshot = args.output / "source-snapshot.db"
    shutil.copyfile(args.snapshot, snapshot)
    original = create_database_engine(f"sqlite:///{snapshot}")
    upgrade_database(original)
    upgrade_database(engine)
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(Project)) or db.scalar(select(func.count()).select_from(ModelConfig)):
            raise ValueError("Destination database must be empty")
    with Session(original) as db:
        tasks = list(db.scalars(select(ProcessingTask).where(ProcessingTask.project_id == PROJECT_ID)))
        tasks = [row for row in tasks if row.payload.get("start_stage") == "translation"
                 and row.payload.get("end_stage") == "rendering"]
        identifiers = {row.image_id for row in tasks}
        if len(tasks) != 337 or len(identifiers) != 337:
            raise ValueError("Could not identify the exact historical 337 pages")
        project = record(db.get(Project, PROJECT_ID))
        pages = [record(row) for row in db.scalars(select(ImagePage).where(ImagePage.id.in_(identifiers))
                 .order_by(ImagePage.order_index, ImagePage.id))]
        models = [record(row) for row in db.scalars(select(ModelConfig))]
    original.dispose()
    key = (args.source / ".secret_key").read_bytes().strip()
    verified_keys = 0
    for model in models:
        if model["encrypted_api_key"]:
            Fernet(key).decrypt(model["encrypted_api_key"].encode())
            verified_keys += 1
    shutil.copyfile(args.source / ".secret_key", root / ".secret_key")
    (root / ".secret_key").chmod(0o600)
    shutil.copyfile(args.source / "execution-policy.json", root / "execution-policy.json")
    paths = {page["original_path"] for page in pages}
    for directory in (args.source / "fonts", args.source / "projects" / PROJECT_ID / "fonts"):
        if directory.is_dir():
            paths.update(path.relative_to(args.source).as_posix() for path in directory.rglob("*")
                         if path.is_file() and path.suffix.lower() in {".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2"})

    def copy(relative):
        source = (args.source / relative).resolve()
        target = (root / relative).resolve()
        if not source.is_relative_to(args.source.resolve()) or not target.is_relative_to(root):
            raise ValueError("Input path escapes its root")
        digest = file_digest(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if file_digest(target) != digest:
            raise ValueError("Copied original differs")
        return relative, {"bytes": source.stat().st_size, "sha256": digest}

    with ThreadPoolExecutor(max_workers=4) as pool:
        assets = dict(pool.map(copy, sorted(paths)))
    with SessionLocal() as db:
        db.add_all(ModelConfig(**decode(ModelConfig, value)) for value in models)
        db.add(Project(**decode(Project, project)))
        db.flush()
        for value in pages:
            # No region, OCR, translation, detection geometry or cached image
            # from the old run enters this new project.
            value.update(clean_path=None, rendered_path=None, text_layer_path=None, metadata_json={},
                         status="UPLOADED", current_stage=None, error_message=None)
            db.add(ImagePage(**decode(ImagePage, value)))
        db.commit()
        assert db.scalar(select(func.count()).select_from(TextRegion)) == 0
        assert db.scalar(select(func.count()).select_from(ProcessingTask)) == 0
        actual = [record(row) for row in db.scalars(select(ImagePage).order_by(ImagePage.order_index, ImagePage.id))]
    private = {"page_ids": [page["id"] for page in pages], "assets": assets,
               "originals_fingerprint": fingerprint(actual), "project_id": PROJECT_ID}
    write(args.output / "private-inputs.json", private, exclusive=True)
    report = {"scope": SCOPE, "pages": len(pages), "initial_regions": 0, "initial_ocr_texts": 0,
              "initial_translations": 0, "initial_masks_and_outputs": 0, "initial_checkpoints": 0,
              "source_snapshot_sha256": file_digest(args.snapshot), "assets_copied": len(assets),
              "asset_bytes": sum(value["bytes"] for value in assets.values()),
              "encrypted_keys_verified": verified_keys, "source_is_read_only": True,
              "storage_environment": "isolated_Docker_named_volume_not_source_Windows_bind_mount",
              "paid_requests": 0, "image_cache": "empty_new_process", "model_loading_excluded_from_batch_wall": True}
    write(args.output / "preparation-report.json", report, exclusive=True)
    print(json.dumps(report), flush=True)


def serve(args):
    import uvicorn
    from app.core.database import initialize_database
    from app.pipeline.processor import PipelineProcessor
    from app.services.execution import configure_native_threads, execution_runtime

    read(args.output / "preparation-report.json")
    initialize_database()
    configure_native_threads()
    started = time.monotonic()
    with SessionLocal() as db:
        processor = PipelineProcessor(db)
        for kind in ("detection", "ocr", "inpainting", "rendering"):
            provider, _ = processor._provider(kind, None)
            provider.ensure_loaded()
    write(args.output / "server.json", {"pid": os.getpid(), "model_loading_seconds": time.monotonic() - started,
          "inference": execution_runtime().snapshot()["inference"]}, exclusive=True)
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="warning", access_log=False)


def dispatch(args):
    if not args.allow_paid_requests:
        raise ValueError("Paid full-pipeline test requires --allow-paid-requests")
    private = read(args.output / "private-inputs.json")
    with SessionLocal() as db:
        actual = [record(row) for row in db.scalars(select(ImagePage).order_by(ImagePage.order_index, ImagePage.id))]
        assert fingerprint(actual) == private["originals_fingerprint"]
        assert db.scalar(select(func.count()).select_from(TextRegion)) == 0
        assert db.scalar(select(func.count()).select_from(ProcessingTask)) == 0
    marker = {"run_id": uuid4().hex, "started_at": datetime.now(timezone.utc).isoformat()}
    write(args.output / "dispatch-started.json", marker, exclusive=True)
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=180) as client:
        response = client.post(f"/api/projects/{PROJECT_ID}/batch-process", json={
            "start_stage": "detection", "end_stage": "rendering", "force": True,
            "image_ids": private["page_ids"], "only_with_regions": False,
            "options": {"repair_geometry": "source", "rebuild_clean": True, "acceptance_run_id": marker["run_id"]}})
        response.raise_for_status()
        tasks = response.json()
    assert len(tasks) == 337 and {row["image_id"] for row in tasks} == set(private["page_ids"])
    write(args.output / "dispatch.json", {**marker, "task_ids": [row["id"] for row in tasks]}, exclusive=True)
    observe(args)


def recover_dispatch(args):
    path = args.output / "dispatch.json"
    if path.exists():
        return read(path)
    marker = read(args.output / "dispatch-started.json")
    with SessionLocal() as db:
        tasks = [row for row in db.scalars(select(ProcessingTask))
                 if row.payload.get("options", {}).get("acceptance_run_id") == marker["run_id"]]
        if len(tasks) != 337:
            raise ValueError("Uncertain submission; inspect existing tasks, do not resubmit")
        state = {**marker, "task_ids": [row.id for row in tasks]}
    write(path, state, exclusive=True)
    return state


def observe(args):
    state = recover_dispatch(args)
    started = datetime.fromisoformat(state["started_at"])
    server = psutil.Process(read(args.output / "server.json")["pid"])
    processes = {}
    report = read(args.output / "progress.json") if (args.output / "progress.json").exists() else {}
    peaks = report.get("peaks", {"rss_sum_bytes": 0, "cpu_aggregate_percent": 0., "gpu_used_bytes": 0,
                 "gpu_utilization_percent": 0., "page_concurrency": 0, "cpu_concurrency": 0, "model_concurrency": 0})
    samples = report.get("samples", [])
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as client:
        while True:
            response = client.post("/api/tasks/status", json=state["task_ids"])
            response.raise_for_status()
            tasks = response.json()
            response = client.get("/api/execution")
            response.raise_for_status()
            runtime = response.json()
            rss, cpu = 0, 0.
            for process in [server, *server.children(recursive=True)]:
                try:
                    tracked = processes.setdefault(process.pid, process)
                    rss += tracked.memory_info().rss
                    cpu += tracked.cpu_percent()
                except psutil.Error:
                    pass
            peaks["rss_sum_bytes"] = max(peaks["rss_sum_bytes"], rss)
            peaks["cpu_aggregate_percent"] = max(peaks["cpu_aggregate_percent"], cpu)
            for device in runtime.get("gpu", {}).get("devices", []):
                peaks["gpu_used_bytes"] = max(peaks["gpu_used_bytes"], device["used_bytes"])
                peaks["gpu_utilization_percent"] = max(peaks["gpu_utilization_percent"], device["utilization_percent"] or 0)
            for key, source in (("page_concurrency", "pages"), ("cpu_concurrency", "cpu"), ("model_concurrency", "models")):
                peaks[key] = max(peaks[key], runtime[source].get("peak", runtime[source]["active"]))
            counts = dict(Counter(row["status"] for row in tasks))
            now = datetime.now(timezone.utc)
            sample = {"at": now.isoformat(), "elapsed_seconds": (now - started).total_seconds(), "statuses": counts,
                      "active_stages": dict(Counter(stage for row in tasks for stage in row.get("active_stages", []))),
                      "waiting": dict(Counter(row["waiting_reason"] for row in tasks if row.get("waiting_reason")))}
            samples.append(sample)
            report = {"scope": SCOPE, "task_count": len(tasks), "statuses": counts, "peaks": peaks, "samples": samples,
                      "elapsed_seconds": sample["elapsed_seconds"], "execution_status": runtime,
                      "terminal": all(row["status"] in {"COMPLETED", "FAILED", "CANCELLED"} for row in tasks),
                      "errors": [{"id": row["id"], "page_id": row["image_id"], "kind": row.get("error_kind"),
                                  "message": row.get("error_message")} for row in tasks if row["status"] in {"FAILED", "CANCELLED"}],
                      "no_automatic_unknown_outcome_retries": True,
                      "resource_sampling": {"interval_seconds": 5, "gpu_scope": "whole_device_including_production",
                                            "rss_scope": "sum_of_backend_and_child_RSS"},
                      "storage_environment": "isolated_Docker_named_volume_not_source_Windows_bind_mount"}
            if report["terminal"] and all(row.get("finished_at") for row in tasks):
                finished = max(datetime.fromisoformat(row["finished_at"]) for row in tasks)
                report["wall_seconds_from_dispatch"] = (finished - started).total_seconds()
            write(args.output / "progress.json", report)
            print(json.dumps(sample), flush=True)
            if report["terminal"] or counts.get("PAUSED"):
                write(args.output / "execution-report.json", report)
                return
            time.sleep(5)


def audit(args):
    import numpy as np
    from app.services.workflow_state import capture_page, page_version
    from PIL import Image

    private, dispatch = read(args.output / "private-inputs.json"), recover_dispatch(args)
    root = get_settings().data_dir
    assets_same = all(file_digest(args.source / path) == value["sha256"] for path, value in private["assets"].items())
    originals_same = all(file_digest(root / path) == value["sha256"] for path, value in private["assets"].items())
    cases, timings = [], {}
    with SessionLocal() as db:
        tasks = list(db.scalars(select(ProcessingTask).where(ProcessingTask.id.in_(dispatch["task_ids"]))))
        assert len(tasks) == 337 and all(row.status in {"COMPLETED", "FAILED", "CANCELLED"} for row in tasks)
        runs = list(db.scalars(select(TaskExecutionRun).where(TaskExecutionRun.task_id.in_(dispatch["task_ids"]))))
        for run in runs:
            for key, value in run.timings.items():
                aggregate = timings.setdefault(key, {"seconds": 0., "calls": 0})
                aggregate["seconds"] += value["seconds"]
                aggregate["calls"] += value["calls"]
        checkpoints = Counter()
        for task in tasks:
            workflow = db.scalar(select(TaskWorkflow).where(TaskWorkflow.task_id == task.id))
            if workflow is None:
                cases.append({"id": task.id, "status": task.status, "error": "missing_workflow"})
                continue
            final = capture_page(db, task.image_id)
            stages = {row.stage: row for row in db.scalars(select(StageCheckpoint).where(StageCheckpoint.workflow_id == workflow.id))}
            checkpoints.update(f"{stage}:{row.status}" for stage, row in stages.items())
            case = {"id": task.id, "page_id": task.image_id, "filename": final["page"]["filename"], "status": task.status,
                    "initial_regions": len(workflow.input_state["regions"]), "detected_regions": len(final["regions"]),
                    "source_texts": sum(bool(row["source_text"].strip()) for row in final["regions"].values()),
                    "translations": sum(bool(row["translated_text"].strip()) for row in final["regions"].values()),
                    "stages": {stage: row.status for stage, row in stages.items()}}
            if task.status == "COMPLETED":
                with Image.open(root / final["page"]["original_path"]) as image:
                    source = np.asarray(image.convert("RGB"))
                with Image.open(root / final["page"]["clean_path"]) as image:
                    clean = np.asarray(image.convert("RGB"))
                allowed = np.zeros(source.shape[:2], bool)
                for row in final["regions"].values():
                    if row["pixel_mask_path"]:
                        with Image.open(root / row["pixel_mask_path"]) as image:
                            allowed |= np.asarray(image.convert("L")) > 0
                case["outside_mask_identical"] = bool(source.shape == clean.shape and np.array_equal(source[~allowed], clean[~allowed]))
                dimensions = (final["page"]["width"], final["page"]["height"])
                case["output_dimensions_valid"] = True
                for field in ("rendered_path", "text_layer_path"):
                    with Image.open(root / final["page"][field]) as image:
                        case["output_dimensions_valid"] &= image.size == dimensions
                case["published"] = workflow.status == "PUBLISHED"
            else:
                case["unpublished_page_unchanged"] = page_version(workflow.input_state) == page_version(final)
                case["error_kind"] = task.error_kind
            cases.append(case)
        history_pending = db.scalar(select(func.count()).select_from(EditorHistoryEntry).where(EditorHistoryEntry.pending.is_(True)))
    report = {"scope": SCOPE, "source_assets_unchanged": assets_same, "copied_originals_unchanged": originals_same,
              "task_count": len(tasks), "timing_runs": len(runs), "stage_checkpoints": dict(checkpoints),
              "inclusive_timings_do_not_sum_to_batch_wall": timings, "cases": cases,
              "all_started_from_zero_regions": all(row["initial_regions"] == 0 for row in cases),
              "pending_history": history_pending}
    report["quality_pass"] = assets_same and originals_same and report["all_started_from_zero_regions"] and not history_pending and all(
        all(row.get(key) for key in ("outside_mask_identical", "output_dimensions_valid", "published"))
        and set(row["stages"]) == set(STAGES) and set(row["stages"].values()) == {"SUCCEEDED"}
        if row["status"] == "COMPLETED" else row.get("unpublished_page_unchanged", False) for row in cases)
    write(args.output / "quality-report.json", report)
    print(json.dumps({key: value for key, value in report.items() if key not in {"cases", "inclusive_timings_do_not_sum_to_batch_wall"}}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/source-data"))
    parser.add_argument("--snapshot", type=Path, default=Path("/snapshot-data/migration-snapshot.db"))
    parser.add_argument("--output", type=Path, default=Path("/data/full-pipeline-337"))
    parser.add_argument("--mode", choices=["prepare", "serve", "run", "observe", "audit"], required=True)
    parser.add_argument("--allow-paid-requests", action="store_true")
    args = parser.parse_args()
    {"prepare": prepare, "serve": serve, "run": dispatch, "observe": observe, "audit": audit}[args.mode](args)


if __name__ == "__main__":
    main()
