"""Local JSON-line worker; deliberately does not import MangaOCR/Paddle models."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
from app.services.detection.classification import MODEL_SHA256
from app.services.detection.text_roles import CLASS_NAMES, LayoutPrediction, classify_text_polygons
from PIL import Image

PROTOCOL = "mangaflow-text-classifier-v1"


def _send(value: dict) -> None:
    print(json.dumps({"protocol": PROTOCOL, **value}, ensure_ascii=False, allow_nan=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()
    try:
        with args.weights.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != MODEL_SHA256:
                raise ValueError("model_checksum_mismatch")
        with contextlib.redirect_stdout(sys.stderr):
            import torch
            from rfdetr import RFDETRSeg2XLarge
            from safetensors.torch import load_file

            torch.set_num_threads(2)
            device = args.device
            if device == "auto":
                device = "cuda:0" if torch.cuda.is_available() else "cpu"
            if device.startswith(("cuda", "gpu")) and not torch.cuda.is_available():
                device = "cpu"
            model = RFDETRSeg2XLarge(
                pretrain_weights=None, resolution=1152,
                num_select=160, num_classes=4, device=device,
            )
            model.model.model.load_state_dict(load_file(str(args.weights), device="cpu"), strict=True)
            model.model.class_names = list(CLASS_NAMES)
        _send({"ready": True, "device": device})
    except Exception as exc:
        _send({"error": f"model_load_failed: {type(exc).__name__}: {exc}"[:350]})
        return

    @lru_cache(maxsize=2)
    def predict(path: str, mtime_ns: int, size: int) -> list[LayoutPrediction]:
        with Image.open(path) as source:
            rgba = source.convert("RGBA")
            image = Image.new("RGBA", rgba.size, "white")
            image.alpha_composite(rgba)
            image = image.convert("RGB")
        with contextlib.redirect_stdout(sys.stderr), torch.inference_mode():
            result = model.predict(image, threshold=0.2)
        if result.mask is None or len(result.mask) != len(result.xyxy):
            raise ValueError("model_missing_masks")
        height, width = image.height, image.width
        predictions = []
        for label, confidence, box, mask in zip(result.class_id, result.confidence, result.xyxy, result.mask, strict=True):
            if not 0 <= int(label) < len(CLASS_NAMES) or not np.isfinite(box).all():
                continue
            x0, y0, x1, y1 = box
            left, top = max(0, int(np.floor(x0))), max(0, int(np.floor(y0)))
            right, bottom = min(width, int(np.ceil(x1)) + 1), min(height, int(np.ceil(y1)) + 1)
            if right <= left or bottom <= top or mask.shape != (height, width):
                continue
            predictions.append(LayoutPrediction(
                CLASS_NAMES[int(label)], float(confidence), [float(x0), float(y0), float(x1 - x0), float(y1 - y0)],
                np.ascontiguousarray(mask[top:bottom, left:right], dtype=np.uint8), (left, top),
            ))
        return predictions

    for line in sys.stdin:
        try:
            request = json.loads(line)
            path = Path(request["image_path"])
            stat = path.stat()
            predictions = predict(str(path), stat.st_mtime_ns, stat.st_size)
            values = classify_text_polygons(
                request["polygons"], predictions,
                min_sfx_confidence=float(request["min_sfx_confidence"]), filter_sfx=request["filter_sfx"] is True,
                protected_indices=set(request.get("protected_indices", [])),
            )
            _send({"classifications": values})
        except Exception as exc:
            _send({"error": f"classification_failed: {type(exc).__name__}: {exc}"[:350]})


if __name__ == "__main__":
    main()
