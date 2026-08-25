from app.services.detection.bubbles import BubbleInstance, OnnxBubbleSegmenter
from app.services.detection.grouping import (
    assign_text_to_bubbles,
    group_text_lines,
    group_text_regions_by_bubbles,
)
from app.services.detection.providers import OpenCVTextDetector, PaddleTextDetector

__all__ = [
    "BubbleInstance",
    "OnnxBubbleSegmenter",
    "OpenCVTextDetector",
    "PaddleTextDetector",
    "assign_text_to_bubbles",
    "group_text_lines",
    "group_text_regions_by_bubbles",
]
