"""Read-only source preparation benchmark, not a model/pipeline speed score.

Run with PYTHONPATH=backend. No database writes, model loads, or cloud calls.
Each repetition starts with an empty decoded-source cache; OS caches may be warm.
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
from app.services.inpainting import text_segmentation as segmentation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    source = args.source.resolve()
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()

    def legacy(window):
        image = cv2.imread(str(source))
        if image is None:
            raise ValueError("Cannot decode benchmark source")
        key = hashlib.sha256(source.read_bytes() + segmentation.MODEL_SHA256.encode() + str(window).encode()).hexdigest()
        return image, key

    def cached(window):
        image, digest = segmentation._source_image(source)
        digest = digest.copy()
        digest.update(segmentation.MODEL_SHA256.encode() + str(window).encode())
        return image, digest.hexdigest()

    before, old_key = legacy(None)
    after, new_key = cached(None)
    if not np.array_equal(before, after) or old_key != new_key:
        raise AssertionError("Decoded pixels or probability-cache identity changed")
    h, w = before.shape[:2]
    windows = [None, *[(int((w - min(w, 512)) * index / 5), 0, min(w, 512), min(h, 512)) for index in range(6)]]
    times: dict[str, list[float]] = {"legacy": [], "cached": []}
    for repeat in range(args.repeats):
        order = ("legacy", "cached") if repeat % 2 == 0 else ("cached", "legacy")
        for mode in order:
            with segmentation._cache_lock:
                segmentation._sources.clear()
                segmentation._source_bytes = 0
            operation = legacy if mode == "legacy" else cached
            started = time.perf_counter()
            keys = [operation(window)[1] for window in windows]
            times[mode].append(time.perf_counter() - started)
            if mode == "legacy":
                expected = keys
            elif repeat == 0 and keys != expected:
                raise AssertionError("Local-window cache identity changed")
    medians = {key: statistics.median(values) for key, values in times.items()}
    print(json.dumps({
        "scope": "source_decode_and_hash_only_not_model_or_pipeline",
        "pixels_identical": True, "cache_keys_identical": True,
        "source_unchanged": hashlib.sha256(source.read_bytes()).hexdigest() == fingerprint,
        "shape": list(before.shape), "windows_per_run": len(windows), "repeats": args.repeats,
        "seconds": times, "median_seconds": medians,
        "preparation_speedup": medians["legacy"] / max(1e-9, medians["cached"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
