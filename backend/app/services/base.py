from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import nullcontext
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any, Generic, TypeVar


class ProviderState(StrEnum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


@dataclass(slots=True)
class ProviderCapabilities:
    name: str
    provider_type: str
    description: str
    devices: list[str] = field(default_factory=lambda: ["cpu"])
    orientations: list[str] = field(default_factory=lambda: ["horizontal", "vertical"])
    supports_batch: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after


T = TypeVar("T")


class ModelProvider(ABC, Generic[T]):
    capabilities: ProviderCapabilities

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        capability = getattr(cls, "capabilities", None)
        if capability and capability.name in {"manga-ocr", "paddleocr", "rfdetr", "lama", "hybrid"}:
            from app.services.execution import serialized_model_call
            for name in ("load", "unload", "detect", "recognize", "inpaint"):
                if name in cls.__dict__:
                    setattr(cls, name, serialized_model_call(cls.__dict__[name]))

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.state = ProviderState.UNLOADED
        self.last_error: str | None = None
        self._load_lock = RLock()

    def load(self) -> None:
        self.state = ProviderState.READY

    def unload(self) -> None:
        from app.services.execution import execution_runtime
        slot = execution_runtime().model_slot() if self.capabilities.name in {"manga-ocr", "paddleocr", "rfdetr", "lama", "hybrid"} else nullcontext()
        with slot, self._load_lock:
            self.state = ProviderState.UNLOADED

    def ensure_loaded(self) -> None:
        from app.services.execution import execution_runtime
        slot = execution_runtime().model_slot() if self.capabilities.name in {"manga-ocr", "paddleocr", "rfdetr", "lama", "hybrid"} else nullcontext()
        with slot, self._load_lock:
            if self.state == ProviderState.READY:
                return
            try:
                self.state = ProviderState.LOADING
                self.load()
                self.state = ProviderState.READY
                self.last_error = None
            except Exception as exc:
                self.state = ProviderState.ERROR
                self.last_error = str(exc)
                raise ProviderError(f"Failed to load {self.capabilities.name}: {exc}") from exc


@dataclass(slots=True)
class DetectionResult:
    polygon: list[list[float]]
    bbox: list[float]
    confidence: float
    orientation: str = "vertical"
    region_type: str = "background_complex"
    metadata: dict[str, Any] = field(default_factory=dict)
    bubble_id: str | None = None
    # Transient, detector-owned geometry used by the pipeline to persist an
    # exact speech-balloon repair constraint.  It intentionally does not live
    # in ``metadata``: numpy arrays are not JSON serializable and duplicating a
    # complete mask in every TextRegion would make API/database payloads huge.
    balloon_mask: Any | None = field(default=None, repr=False, compare=False)
    balloon_mask_origin: tuple[int, int] | None = None
    balloon_mask_id: str | None = None
    balloon_mask_parent_id: str | None = None
    balloon_mask_confidence: float | None = None


class TextDetector(ModelProvider[list[DetectionResult]], ABC):
    @abstractmethod
    def detect(self, image_path: Path) -> list[DetectionResult]: ...


@dataclass(slots=True)
class OCRResult:
    text: str
    confidence: float
    orientation: str
    metadata: dict[str, Any] = field(default_factory=dict)


class OCRProvider(ModelProvider[OCRResult], ABC):
    @abstractmethod
    def recognize(self, image_path: Path, bbox: list[float], orientation: str, padding: int = 4) -> OCRResult: ...


class Translator(ModelProvider[dict[str, str]], ABC):
    @abstractmethod
    async def translate_regions(
        self,
        regions: list[tuple[str, str]],
        *,
        source_language: str,
        target_language: str,
        context: dict[str, Any],
    ) -> dict[str, str]: ...


class Inpainter(ModelProvider[Path], ABC):
    @abstractmethod
    def inpaint(self, image_path: Path, mask_path: Path, output_path: Path, region_type: str) -> Path: ...


class Renderer(ModelProvider[dict[str, Any]], ABC):
    @abstractmethod
    def render(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
