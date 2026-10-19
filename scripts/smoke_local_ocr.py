"""Read-only crop OCR check. Does not create tasks, mutate projects or call cloud APIs."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import unicodedata
from pathlib import Path

from app.services.registry import registry
from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--providers", nargs="+", default=["paddleocr-vl-1.6", "baberu-ocr", "hayai-ocr"])
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--box", nargs=4, type=float)
    parser.add_argument("--expected")
    args = parser.parse_args()
    before = hashlib.sha256(args.image.read_bytes()).hexdigest()
    with Image.open(args.image) as image:
        box = args.box or [0, 0, image.width, image.height]
    results = []
    for name in args.providers:
        provider = registry.create("ocr", name, {"device": args.device})
        started = time.monotonic()
        try:
            provider.ensure_loaded()
            loaded = time.monotonic()
            result = provider.recognize(args.image, box, "vertical" if box[3] > box[2] else "horizontal", 0)
            row = {"provider": name, "load_seconds": round(loaded-started, 3),
                   "inference_seconds": round(time.monotonic()-loaded, 3),
                   "text": result.text, "metadata": result.metadata}
            if args.expected is not None:
                def normalize(text):
                    return "".join(unicodedata.normalize("NFKC", text).split())
                row["matches_expected"] = normalize(result.text) == normalize(args.expected)
        except Exception as exc:
            row = {"provider": name, "error": str(exc)}
        finally:
            provider.unload()
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    intact = hashlib.sha256(args.image.read_bytes()).hexdigest() == before
    print(json.dumps({"source_unchanged": intact, "paid_requests": 0, "results": results}, ensure_ascii=False), flush=True)
    return 0 if intact and all("error" not in row for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
