"""Isolated 1 vs 2 independent CTD CUDA sessions; never modifies source data."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import queue
import sqlite3
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import cv2
import numpy as np
import torch
from app.services.execution import configure_native_threads
from app.services.inpainting.segmentation_cuda import CudaSegmentationSession
from app.services.inpainting.text_segmentation import MODEL_FILENAME, MODEL_SHA256, _probability_map


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",type=Path,required=True)
    parser.add_argument("--model-dir",type=Path,required=True)
    parser.add_argument("--project",required=True)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new report filename")
    configure_native_threads()
    path = args.model_dir / MODEL_FILENAME
    assert hashlib.sha256(path.read_bytes()).hexdigest() == MODEL_SHA256
    with closing(sqlite3.connect(f"file:{(args.data_dir/'mangaflow.db').as_posix()}?mode=ro",uri=True)) as db:
        paths = [args.data_dir/row[0] for row in db.execute("""SELECT original_path FROM image_pages p WHERE project_id=?
            AND EXISTS(SELECT 1 FROM text_regions r WHERE r.image_id=p.id) ORDER BY order_index LIMIT 24""",(args.project,))]
    blobs = []
    for source in paths:
        image = cv2.imread(str(source))
        h,w = image.shape[:2]
        scale = 1024/max(h,w)
        canvas = np.zeros((1024,1024,3),np.uint8)
        hh,ww = round(h*scale),round(w*scale)
        canvas[:hh,:ww] = cv2.resize(image,(ww,hh),interpolation=cv2.INTER_LINEAR)
        blobs.append(cv2.dnn.blobFromImage(canvas,1/255.,swapRB=True))
    if len(blobs) != 24:
        raise ValueError("Need 24 real text pages")
    sessions = [CudaSegmentationSession(path,2) for _ in range(2)]
    references = [_probability_map(sessions[0].forward(blob)).copy() for blob in blobs]
    for session in sessions:
        session.forward(blobs[0])
    measurements = {1:[],2:[]}
    baseline_used = torch.cuda.mem_get_info(0)
    reserve = max(2*1024**3,int(baseline_used[1]*.15))
    free_samples = []
    stop = threading.Event()

    def monitor():
        while not stop.wait(.02):
            free_samples.append(torch.cuda.mem_get_info(0)[0])

    sampler = threading.Thread(target=monitor,daemon=True)
    sampler.start()
    try:
        for repeat in range(4):
            for count in ((1,2) if repeat%2 == 0 else (2,1)):
                available = queue.Queue()
                for session in sessions[:count]:
                    available.put(session)

                def infer(index,available=available):
                    session = available.get()
                    started = time.perf_counter()
                    try:
                        result = _probability_map(session.forward(blobs[index]))
                        elapsed = time.perf_counter()-started
                        reference = references[index]
                        threshold_change = max(np.count_nonzero((result>=threshold) != (reference>=threshold)) /
                            max(1,np.count_nonzero((result>=threshold) | (reference>=threshold))) for threshold in (.25,.65))
                        return elapsed,float(np.max(np.abs(result-reference))),threshold_change
                    finally:
                        available.put(session)

                started = time.perf_counter()
                with ThreadPoolExecutor(count) as pool:
                    rows = list(pool.map(infer,range(len(blobs))))
                wall = time.perf_counter()-started
                item = {"repeat":repeat,"instances":count,"wall_seconds":wall,
                        "throughput":len(blobs)/wall,"latency_p95":sorted(row[0] for row in rows)[math.ceil(len(rows)*.95)-1],
                        "max_abs_difference":max(row[1] for row in rows),"max_threshold_change":max(row[2] for row in rows)}
                measurements[count].append(item)
                print(json.dumps(item),flush=True)
    finally:
        stop.set()
        sampler.join()
    summaries = {count:{key:statistics.median(row[key] for row in rows) for key in ("throughput","latency_p95")} for count,rows in measurements.items()}
    gain = summaries[2]["throughput"]/summaries[1]["throughput"]
    p95_ratio = summaries[2]["latency_p95"]/summaries[1]["latency_p95"]
    quality = all(row["max_threshold_change"]<=.001 for rows in measurements.values() for row in rows)
    memory = min(free_samples,default=0)>=reserve
    report = {"scope":"native_CTD_only_24_pages_4_alternating_repeats","model_sha256":MODEL_SHA256,
              "device":torch.cuda.get_device_name(0),"measurements":measurements,"summaries":summaries,
              "throughput_gain":gain,"p95_ratio":p95_ratio,"quality_pass":quality,
              "minimum_free_bytes":min(free_samples,default=0),"required_reserve_bytes":reserve,"memory_pass":memory,
              "two_instances_pass":gain>=1.1 and p95_ratio<=1.2 and quality and memory}
    args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({key:value for key,value in report.items() if key != "measurements"}),flush=True)


if __name__ == "__main__":
    main()
