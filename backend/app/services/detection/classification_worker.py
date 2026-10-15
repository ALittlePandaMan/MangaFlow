"""Local JSON-line worker; deliberately does not import MangaOCR/Paddle models."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
from functools import lru_cache
from pathlib import Path

from app.services.detection.classification import MODEL_SHA256
from app.services.detection.layout_protocol import MAX_PAGE_PIXELS, encode_predictions
from app.services.detection.text_roles import CLASS_NAMES, LayoutPrediction
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
            from app.services.detection.layout_inference import DEFAULT_THRESHOLDS, predict_layout, validate_thresholds
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
            model.model.model.to(model.model.device).eval()
            model.model.class_names = list(CLASS_NAMES)
        _send({"ready": True, "device": device})
    except Exception as exc:
        _send({"error": f"model_load_failed: {type(exc).__name__}: {exc}"[:350]})
        return

    @lru_cache(maxsize=2)
    def predict(path: str, mtime_ns: int, size: int, thresholds: tuple[float, ...]) -> tuple[int, int, list[LayoutPrediction]]:
        with Image.open(path) as source:
            if source.width * source.height > MAX_PAGE_PIXELS:
                raise ValueError("layout_image_too_large")
            rgba = source.convert("RGBA")
            image = Image.new("RGBA", rgba.size, "white")
            image.alpha_composite(rgba)
            image = image.convert("RGB")
        with contextlib.redirect_stdout(sys.stderr), torch.inference_mode():
            predictions = predict_layout(model, image, thresholds)
        return image.width, image.height, predictions

    for line in sys.stdin:
        try:
            request = json.loads(line)
            path = Path(request["image_path"])
            stat = path.stat()
            thresholds = validate_thresholds(request.get("thresholds", DEFAULT_THRESHOLDS))
            width, height, predictions = predict(str(path), stat.st_mtime_ns, stat.st_size, thresholds)
            if request.get("operation") != "layout":
                raise ValueError("unsupported_layout_operation")
            _send(encode_predictions(predictions, width, height))
        except Exception as exc:
            _send({"error": f"layout_failed: {type(exc).__name__}: {exc}"[:350]})


if __name__ == "__main__":
    main()
