"""RF-DETR inference with Koharu's bounded, per-instance mask postprocessing.

Imported only by the isolated worker (and inference tests). Never allocate a
top-K-by-page tensor: filter class scores first, resize one mask, retain its crop.
Reference: koharu-ml/src/koharu_layout_rfdetr_seg_2xl/processor.rs.
"""
from __future__ import annotations

import math

import numpy as np
import torch
import torch.nn.functional as F
from app.services.detection.layout_protocol import MAX_PAGE_PIXELS, MAX_TOTAL_MASK_PIXELS
from app.services.detection.text_roles import CLASS_NAMES, LayoutPrediction
from PIL import Image

DEFAULT_THRESHOLDS = (.25, .2, .5, .5)


def validate_thresholds(values) -> tuple[float, ...]:
    thresholds = tuple(float(value) for value in values)
    if len(thresholds) != 4 or any(not math.isfinite(value) or not 0 <= value <= 1 for value in thresholds):
        raise ValueError("invalid_layout_thresholds")
    return thresholds


def prepare_image(image: Image.Image, device, resolution: int = 1152) -> torch.Tensor:
    pixels = torch.from_numpy(np.array(image.convert("RGB"))).permute(2, 0, 1).unsqueeze(0)
    pixels = pixels.to(device=device, dtype=torch.float32).div_(255)
    # Match Koharu/training's bilinear resize, without torchvision's default AA.
    if pixels.shape[-2:] != (resolution, resolution):
        pixels = F.interpolate(pixels, size=(resolution, resolution), mode="bilinear", align_corners=False)
    mean = pixels.new_tensor([.485, .456, .406]).view(1, 3, 1, 1)
    std = pixels.new_tensor([.229, .224, .225]).view(1, 3, 1, 1)
    return pixels.sub_(mean).div_(std)


@torch.inference_mode()
def postprocess_layout(outputs, width: int, height: int, thresholds=DEFAULT_THRESHOLDS, *, num_select=160):
    thresholds = validate_thresholds(thresholds)
    if min(width, height) <= 0 or width * height > MAX_PAGE_PIXELS:
        raise ValueError("layout_image_too_large")
    logits, boxes, masks = outputs["pred_logits"], outputs["pred_boxes"], outputs["pred_masks"]
    if (logits.ndim != 3 or logits.shape[0] != 1 or logits.shape[2] != 5
            or boxes.shape != (1, logits.shape[1], 4)
            or masks.ndim != 4 or masks.shape[:2] != logits.shape[:2]):
        raise ValueError("invalid_layout_model_output")
    scores, indices = logits.sigmoid().flatten().topk(min(num_select, logits.numel()))
    labels, queries = indices % 5, indices // 5
    limits = scores.new_tensor([*thresholds, float("inf")])
    selected = (scores > limits[labels]).nonzero().flatten()
    # Transfer only compact metadata; native masks stay on the inference device.
    selected_scores = scores[selected].cpu().tolist()
    selected_labels = labels[selected].cpu().tolist()
    selected_queries = queries[selected].cpu().tolist()
    selected_boxes = boxes[0, queries[selected]].cpu().tolist()
    predictions, total_pixels = [], 0
    for score, label, query, box in zip(selected_scores, selected_labels, selected_queries, selected_boxes, strict=True):
        cx, cy, w, h = box
        bbox = [(cx-w/2)*width, (cy-h/2)*height, w*width, h*height]
        if not all(math.isfinite(value) for value in bbox) or min(w, h) <= 0:
            continue
        foreground = F.interpolate(masks[0, query][None, None], size=(height, width),
                                   mode="bilinear", align_corners=False)[0, 0] > 0
        rows = foreground.any(dim=1).nonzero().flatten()
        columns = foreground.any(dim=0).nonzero().flatten()
        if not rows.numel():
            del foreground
            continue
        top, bottom = int(rows[0]), int(rows[-1])+1
        left, right = int(columns[0]), int(columns[-1])+1
        total_pixels += (bottom-top)*(right-left)
        if total_pixels > MAX_TOTAL_MASK_PIXELS:
            raise ValueError("layout_mask_budget_exceeded")
        crop = foreground[top:bottom, left:right].to(device="cpu", dtype=torch.uint8).numpy().copy()
        predictions.append(LayoutPrediction(CLASS_NAMES[label], score, bbox, crop, (left, top)))
        del foreground
    return predictions


@torch.inference_mode()
def predict_layout(model, image: Image.Image, thresholds=DEFAULT_THRESHOLDS):
    thresholds = validate_thresholds(thresholds)
    inputs = prepare_image(image, model.model.device, model.model.resolution)
    outputs = model.model.model(inputs)
    return postprocess_layout(outputs, image.width, image.height, thresholds, num_select=160)
