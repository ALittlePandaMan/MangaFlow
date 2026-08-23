from enum import StrEnum


class PageStatus(StrEnum):
    UPLOADED = "UPLOADED"
    DETECTING = "DETECTING"
    DETECTED = "DETECTED"
    OCR_RUNNING = "OCR_RUNNING"
    OCR_DONE = "OCR_DONE"
    TRANSLATING = "TRANSLATING"
    TRANSLATED = "TRANSLATED"
    MASK_GENERATING = "MASK_GENERATING"
    INPAINTING = "INPAINTING"
    INPAINTED = "INPAINTED"
    LAYOUTING = "LAYOUTING"
    RENDERING = "RENDERING"
    COMPLETED = "COMPLETED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class Orientation(StrEnum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    ROTATED = "rotated"


class RegionType(StrEnum):
    BUBBLE_SIMPLE = "bubble_simple"
    BUBBLE_COMPLEX = "bubble_complex"
    BACKGROUND_SIMPLE = "background_simple"
    BACKGROUND_COMPLEX = "background_complex"
    SFX = "sfx"


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PipelineStage(StrEnum):
    DETECTION = "detection"
    OCR = "ocr"
    TRANSLATION = "translation"
    MASK = "mask"
    INPAINTING = "inpainting"
    RENDERING = "rendering"
    FULL = "full"
