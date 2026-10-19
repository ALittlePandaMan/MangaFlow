"""Frozen, offline mask+repair acceptance on a read-only source project.

Prepare once, then run smoke or all. Only generated files under DATA_DIR and
the requested new report directory are written. Translation is never called.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import shutil
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from app.core.config import get_settings
from app.core.db_engine import create_database_engine
from app.core.migrations import upgrade_database
from app.core.timing import TimingCollector, current_timings
from app.models import ImagePage, ModelConfig, ProcessingTask
from app.pipeline.staged import run_stage
from app.services.database_transfer import backup_sqlite, file_digest
from app.services.execution import ExecutionPolicy, configure_native_threads, execution_runtime
from app.services.inpainting import text_segmentation
from app.services.registry import registry
from app.services.workflow_state import apply_delta, capture_page, fingerprint, record
from app.storage import StorageManager
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session


def assets(value):
    if isinstance(value,str) and value.startswith("projects/"):
        yield value
    elif isinstance(value,dict):
        for key,item in value.items():
            yield from assets(key)
            yield from assets(item)
    elif isinstance(value,list):
        for item in value:
            yield from assets(item)


def prepare(args):
    if args.output.exists():
        raise ValueError("Use a new preparation/report directory")
    args.output.mkdir(parents=True)
    root = get_settings().data_dir
    source = args.source.resolve()
    database = args.output / "input-snapshot.db"
    backup_sqlite(source/"mangaflow.db",database)
    engine = create_database_engine(f"sqlite:///{database}")
    upgrade_database(engine)
    with Session(engine) as db:
        pages = list(db.scalars(select(ImagePage).where(ImagePage.project_id == args.project).order_by(ImagePage.order_index)))
        pages = [page for page in pages if page.regions]
        failed = set(db.scalars(select(ProcessingTask.image_id).where(ProcessingTask.project_id == args.project,
            ProcessingTask.error_message.contains("database is locked"))))
        manual = [page for page in pages if any((region.layout_data or {}).get("mask_generation",{}).get("method") == "manual_mask" for region in page.regions)]
        selected = []
        groups = [manual[:2],[page for page in pages if page.id in failed][:4],
                  sorted(pages,key=lambda page:len(page.regions),reverse=True)[:4],
                  sorted(pages,key=lambda page:min(min(region.bbox[2:]) for region in page.regions if len(region.bbox)==4))[:4],
                  sorted(pages,key=lambda page:page.width*page.height,reverse=True)[:2]]
        darkness = {}
        for page in pages:
            image = cv2.imread(str(source/page.original_path),cv2.IMREAD_GRAYSCALE)
            darkness[page.id] = float(np.mean(cv2.resize(image,(64,64))<80))
        groups += [sorted(pages,key=lambda page:darkness[page.id],reverse=True)[:4],
                   [pages[i] for i in np.linspace(0,len(pages)-1,24,dtype=int)],pages]
        for group in groups:
            for page in group:
                if page.id not in selected and len(selected)<24:
                    selected.append(page.id)
        if len(selected)!=24:
            raise ValueError("Need 24 pages with regions")
        states = [capture_page(db,identifier) for identifier in selected]
        models = [record(row) for row in db.scalars(select(ModelConfig).where(ModelConfig.enabled.is_(True)))]
        source_fingerprint = fingerprint(states)
    engine.dispose()
    manifest = {}
    for state in states:
        for relative in assets(state):
            origin = (source/relative).resolve()
            if not origin.is_relative_to(source) or not origin.is_file():
                raise ValueError("Input asset missing or outside the source root")
            destination = root/relative
            destination.parent.mkdir(parents=True,exist_ok=True)
            digest = file_digest(origin)
            if not destination.exists():
                shutil.copyfile(origin,destination)
            if file_digest(destination)!=digest:
                raise ValueError("Input asset copy differs")
            manifest[relative] = digest
    secret = root/".secret_key"
    if secret.exists():
        raise ValueError("The isolated target already has a secret key")
    shutil.copyfile(source/".secret_key",secret)
    secret.chmod(0o600)
    cases = [{"state":state,"local":False,"synthetic_manual":False} for state in states]
    # Explicitly exercise hand-painted-mask semantics even if this book did
    # not contain a user mask. The synthetic edit is confined to the copy.
    candidate = next(case for case in cases if any(region["pixel_mask_path"] for region in case["state"]["regions"].values()))
    region = next(region for region in candidate["state"]["regions"].values() if region["pixel_mask_path"])
    layout = deepcopy(region["layout_data"])
    layout["mask_generation"] = {**layout.get("mask_generation",{}),"method":"manual_mask","repair_pending":True}
    region["layout_data"] = layout
    candidate["synthetic_manual"] = True
    # Keep existing repaired backgrounds outside a one-region incremental edit.
    for case in [case for case in reversed(cases) if case["state"]["page"]["clean_path"]][:2]:
        case["local"] = True
    private = {"cases":cases,"models":models,"assets":manifest,"source_fingerprint":source_fingerprint,
               "source_page_ids":selected,"project_id":args.project}
    (args.output/"private-inputs.json").write_text(json.dumps(private,ensure_ascii=False),encoding="utf-8")
    public = {"pages":[{"id":case["state"]["page"]["id"],"order_index":case["state"]["page"]["order_index"],
                       "regions":len(case["state"]["regions"]),"prior_db_failure":case["state"]["page"]["id"] in failed,
                       "darkness":darkness[case["state"]["page"]["id"]],"local_repair":case["local"],
                       "synthetic_manual_mask":case["synthetic_manual"]} for case in cases],
              "source_fingerprint":source_fingerprint,"asset_count":len(manifest),"models":[{"kind":row["kind"],"provider":row["provider"],"default":row["is_default"]} for row in models]}
    (args.output/"manifest.json").write_text(json.dumps(public,indent=2),encoding="utf-8")
    print(json.dumps(public),flush=True)


def pixels(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def audit(args):
    """Re-read the live source through a fresh private backup, never migrate it."""
    private = json.loads((args.output/"private-inputs.json").read_text(encoding="utf-8"))
    database = args.output/f"source-audit-{uuid4().hex}.db"
    backup_sqlite(args.source/"mangaflow.db",database)
    engine = create_database_engine(f"sqlite:///{database}")
    try:
        upgrade_database(engine)
        with Session(engine) as db:
            current = fingerprint([capture_page(db,identifier) for identifier in private["source_page_ids"]])
        result = {"source_fingerprint_before":private["source_fingerprint"],"source_fingerprint_after":current,
                  "source_database_unchanged":current==private["source_fingerprint"],
                  "source_assets_unchanged":all(file_digest(args.source/relative)==digest
                                                for relative,digest in private["assets"].items()),
                  "pages":len(private["source_page_ids"]),"assets":len(private["assets"])}
        (args.output/"source-audit.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        print(json.dumps(result),flush=True)
    finally:
        engine.dispose()


def run(args):
    private = json.loads((args.output/"private-inputs.json").read_text(encoding="utf-8"))
    root = get_settings().data_dir
    storage = StorageManager(root)
    configure_native_threads()
    execution_runtime().update(ExecutionPolicy(mode="fixed",max_pages=1,cpu_workers=2),persist=False)
    results = []
    references = {}
    modes = [("onnx_cuda","smoke")] if args.mode=="smoke" else [("opencv","cold"),("opencv","warm"),("onnx_cuda","cold"),("onnx_cuda","warm")]
    for backend,temperature in modes:
        if temperature in {"cold","smoke"}:
            registry.clear_cache()
            text_segmentation._segmenter.cache_clear()
            with text_segmentation._cache_lock:
                text_segmentation._sources.clear()
                text_segmentation._source_bytes = 0
            gc.collect()
            import torch
            torch.cuda.empty_cache()
        for index,case in enumerate(private["cases"][:1] if args.mode=="smoke" else private["cases"]):
            initial = deepcopy(case["state"])
            selected = list(initial["regions"])
            if case["local"]:
                selected = selected[:1]
            options = {"repair_geometry":"source","rebuild_clean":not case["local"]}
            if case["local"]:
                options["region_ids"] = selected
            settings = {"text_segmentation_enabled":True,"repair_auto_retry":True,"ocr_review_threshold":.65,
                        "segmentation_backend":backend,"data_dir":args.output/(backend+"-cache-"+("smoke" if temperature=="smoke" else "acceptance"))}
            policy = {"request":{"start_stage":"mask","end_stage":"inpainting","force":True,"options":options},
                      "settings":settings,"models":private["models"]}
            directory = storage.page_dir(initial["page"]["project_id"],initial["page"]["id"])/"workflows"/uuid4().hex
            collector = TimingCollector()
            token = current_timings.set(collector)
            started = time.perf_counter()
            try:
                mask = run_stage("mask",initial,policy,directory/uuid4().hex,storage,lambda: None)
                masked = apply_delta(initial,mask["changes"])
                painted = run_stage("inpainting",masked,policy,directory/uuid4().hex,storage,lambda: None)
                final = apply_delta(masked,painted["changes"])
            finally:
                elapsed = time.perf_counter()-started
                current_timings.reset(token)
            allowed = np.zeros((initial["page"]["height"],initial["page"]["width"]),np.uint8)
            manual_same = True
            masks = {}
            for identifier in selected:
                row = final["regions"][identifier]
                if row["pixel_mask_path"]:
                    masks[identifier] = storage.absolute(row["pixel_mask_path"])
                    allowed |= cv2.imread(str(masks[identifier]),cv2.IMREAD_GRAYSCALE)
                old = initial["regions"][identifier]
                if old["pixel_mask_path"]:
                    if case["local"]:
                        allowed |= cv2.imread(str(storage.absolute(old["pixel_mask_path"])),cv2.IMREAD_GRAYSCALE)
                    if old["layout_data"].get("mask_generation",{}).get("method")=="manual_mask":
                        manual_same &= file_digest(storage.absolute(old["pixel_mask_path"]))==file_digest(masks[identifier])
            baseline_path = initial["page"]["clean_path"] if case["local"] else initial["page"]["original_path"]
            background = pixels(storage.absolute(baseline_path))
            repaired = pixels(storage.absolute(final["page"]["clean_path"]))
            outside_same = np.array_equal(background[allowed==0],repaired[allowed==0])
            change,lost = 0.,0
            if backend=="opencv" and temperature=="cold":
                references[index] = masks
            if index in references:
                for identifier,path in masks.items():
                    before = cv2.imread(str(references[index][identifier]),cv2.IMREAD_GRAYSCALE)
                    after = cv2.imread(str(path),cv2.IMREAD_GRAYSCALE)
                    union = np.count_nonzero((before>0)|(after>0))
                    change = max(change,np.count_nonzero((before>0)!=(after>0))/max(1,union))
                    count,labels,stats,_ = cv2.connectedComponentsWithStats((before>0).astype(np.uint8))
                    for label in range(1,count):
                        if stats[label,cv2.CC_STAT_AREA]>=3 and not np.any(after[labels==label]):
                            lost += 1
            row = {"backend":backend,"cache":temperature,"index":index,"page_id":initial["page"]["id"],
                   "seconds":elapsed,"regions":len(selected),"local":case["local"],"manual_mask_same":manual_same,
                   "outside_mask_same":outside_same,"max_mask_changed_fraction":change,"lost_glyph_components":lost,
                   "timings":collector.snapshot()}
            results.append(row)
            (args.output/(args.mode+"-partial.json")).write_text(json.dumps(results,indent=2),encoding="utf-8")
            print(json.dumps(row),flush=True)
    summaries = {}
    for backend,temperature in modes:
        values = sorted(row["seconds"] for row in results if row["backend"]==backend and row["cache"]==temperature)
        summaries[f"{backend}-{temperature}"] = {"total":sum(values),"p95":values[math.ceil(len(values)*.95)-1],"count":len(values)}
    source_unchanged = all(file_digest(args.source/relative)==digest for relative,digest in private["assets"].items())
    report = {"scope":"mask_and_real_inpainting_with_residual_checks_no_translation","cache_definition":"application_probability_and_model_cache_not_OS_page_cache",
              "results":results,"summaries":summaries,"source_assets_unchanged":source_unchanged,
              "quality_pass":source_unchanged and all(row["manual_mask_same"] and row["outside_mask_same"] and row["max_mask_changed_fraction"]<=.001 and not row["lost_glyph_components"] for row in results)}
    if args.mode=="all":
        report["cold_gain"] = summaries["opencv-cold"]["total"]/summaries["onnx_cuda-cold"]["total"]
        report["warm_ratio"] = summaries["onnx_cuda-warm"]["total"]/summaries["opencv-warm"]["total"]
        report["timing_pass"] = report["cold_gain"]>=1/.7 and report["warm_ratio"]<=1.1
    (args.output/(args.mode+"-report.json")).write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({key:value for key,value in report.items() if key!="results"}),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--project",required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--mode",choices=["prepare","smoke","all","audit"],required=True)
    args = parser.parse_args()
    if args.mode=="prepare":
        prepare(args)
    elif args.mode=="audit":
        audit(args)
    else:
        run(args)


if __name__=="__main__":
    main()
