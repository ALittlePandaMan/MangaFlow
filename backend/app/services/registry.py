from __future__ import annotations

import hashlib
import importlib.util
import json
from threading import RLock
from typing import Any

from app.services.base import Inpainter, OCRProvider, Renderer, TextDetector, Translator
from app.services.detection import OpenCVTextDetector, RFDetrTextDetector
from app.services.inpainting import HybridInpainter, OpenCVInpainter, SimpleLaMaInpainter
from app.services.ocr import (
    CloudMultimodalOCRProvider,
    GenericCloudOCRProvider,
    MangaOCRProvider,
    NullOCRProvider,
    PaddleOCRProvider,
    TesseractOCRProvider,
)
from app.services.ocr.local import BaberuOCRProvider, HayaiOCRProvider, PaddleOCRVLProvider
from app.services.rendering import PillowRenderer
from app.services.translation import GoogleWebTranslator, OpenAICompatibleTranslator, PassthroughTranslator


class ProviderRegistry:
    """Central provider factory; business services only depend on abstract interfaces."""

    detectors: dict[str, type[TextDetector]] = {"opencv-fallback": OpenCVTextDetector, "rfdetr": RFDetrTextDetector}
    ocr: dict[str, type[OCRProvider]] = {
        "review-fallback": NullOCRProvider,
        "paddleocr-vl-1.6": PaddleOCRVLProvider,
        "manga-ocr": MangaOCRProvider,
        "baberu-ocr": BaberuOCRProvider,
        "hayai-ocr": HayaiOCRProvider,
        "paddleocr": PaddleOCRProvider,
        "tesseract": TesseractOCRProvider,
        "cloud-multimodal": CloudMultimodalOCRProvider,
        "cloud-ocr": GenericCloudOCRProvider,
    }
    translators: dict[str, type[Translator]] = {
        "passthrough": PassthroughTranslator,
        "openai-compatible": OpenAICompatibleTranslator,
        "google-web": GoogleWebTranslator,
    }
    inpainters: dict[str, type[Inpainter]] = {
        "opencv": OpenCVInpainter,
        "lama": SimpleLaMaInpainter,
        "hybrid": HybridInpainter,
    }
    renderers: dict[str, type[Renderer]] = {"pillow": PillowRenderer}

    defaults = {
        "detection": "opencv-fallback",
        "ocr": "review-fallback",
        "translation": "passthrough",
        "inpainting": "lama",
        "rendering": "pillow",
    }

    dependencies = {
        ("ocr", "paddleocr"): "paddleocr",
        ("ocr", "manga-ocr"): "manga_ocr",
        ("ocr", "tesseract"): "pytesseract",
        ("inpainting", "lama"): "simple_lama_inpainting",
        ("inpainting", "hybrid"): "simple_lama_inpainting",
    }

    def __init__(self) -> None:
        self._instances: dict[tuple[str, str, str], Any] = {}
        self._layout_workers: dict[tuple[str, str], Any] = {}
        self._ocr_workers: dict[tuple[str, str], Any] = {}
        self._lock = RLock()

    def create(self, kind: str, provider: str | None = None, config: dict[str, Any] | None = None) -> Any:
        collections = {
            "detection": self.detectors,
            "ocr": self.ocr,
            "translation": self.translators,
            "inpainting": self.inpainters,
            "rendering": self.renderers,
        }
        if kind not in collections:
            raise KeyError(f"Unknown provider kind: {kind}")
        name = provider or self.defaults[kind]
        provider_class = collections[kind].get(name)
        if provider_class is None:
            raise KeyError(f"Unknown {kind} provider: {name}")
        normalized = json.dumps(config or {}, ensure_ascii=False, sort_keys=True, default=str)
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        key = (kind, name, fingerprint)
        with self._lock:
            instance = self._instances.get(key)
            if instance is None:
                instance = provider_class(config)
                if kind == "detection" and name == "rfdetr":
                    from app.services.detection.classification import classifier_python
                    worker = instance._text_classifier
                    worker_key = (worker.device, str(classifier_python(worker.options)))
                    # Threshold/filter changes are CPU postprocessing settings,
                    # not a reason to duplicate RF-DETR weights in VRAM.
                    instance._text_classifier = self._layout_workers.setdefault(worker_key, worker)
                if kind == "ocr" and isinstance(instance, (PaddleOCRVLProvider, BaberuOCRProvider, HayaiOCRProvider)):
                    worker = instance.worker
                    instance.worker = self._ocr_workers.setdefault((name, worker.device), worker)
                self._instances[key] = instance
            return instance

    def clear_cache(self) -> None:
        with self._lock:
            for instance in self._instances.values():
                instance.unload()
            self._instances.clear()
            self._layout_workers.clear()
            self._ocr_workers.clear()

    def is_installed(self, kind: str, provider: str) -> bool:
        if kind == "ocr" and provider in {"paddleocr-vl-1.6", "baberu-ocr", "hayai-ocr"}:
            from app.services.ocr.local_catalog import local_ocr_python
            return local_ocr_python().is_file()
        if (kind, provider) == ("detection", "rfdetr"):
            from app.core.config import get_settings
            from app.services.detection.classification import MODEL_RELATIVE_PATH, classifier_python
            return classifier_python({}).is_file() and (get_settings().model_dir / MODEL_RELATIVE_PATH).is_file()
        dependency = self.dependencies.get((kind, provider))
        return dependency is None or importlib.util.find_spec(dependency) is not None

    def describe(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for kind, providers in (
            ("detection", self.detectors),
            ("ocr", self.ocr),
            ("translation", self.translators),
            ("inpainting", self.inpainters),
            ("rendering", self.renderers),
        ):
            for name, provider_class in providers.items():
                capabilities = provider_class.capabilities
                output.append(
                    {
                        "kind": kind,
                        "name": name,
                        "description": capabilities.description,
                        "devices": capabilities.devices,
                        "orientations": capabilities.orientations,
                        "supports_batch": capabilities.supports_batch,
                        "capabilities": capabilities.extra,
                        "is_fallback": bool(capabilities.extra.get("fallback")) or "fallback" in name,
                        "installed": self.is_installed(kind, name),
                    }
                )
        return output


registry = ProviderRegistry()
