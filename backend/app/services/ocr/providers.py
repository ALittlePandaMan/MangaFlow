from __future__ import annotations

import logging
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from threading import RLock

import numpy as np
from app.services.base import OCRProvider, OCRResult, ProviderCapabilities, ProviderError
from app.services.infra.device import (
    is_accelerator_error,
    release_paddle_cuda,
    release_torch_cuda,
    resolve_paddle_device,
    resolve_torch_device,
)
from app.services.infra.paddle import extract_paddle_lines
from PIL import Image

logger = logging.getLogger(__name__)
_crop_lock = RLock()


@lru_cache(maxsize=2)
def _decoded_page(path: str) -> Image.Image:
    # Uploaded originals are immutable and page IDs make their paths unique.
    with Image.open(path) as source:
        return source.convert("RGB")


def _crop(image_path: Path, bbox: list[float], padding: int) -> Image.Image:
    path = image_path.resolve()
    # PIL crop is read-only with respect to the source, but serialize access to
    # the shared decoded page for deployments that raise task concurrency.
    with _crop_lock:
        image = _decoded_page(str(path))
        x, y, width, height = bbox
        box = (
            max(0, int(x - padding)),
            max(0, int(y - padding)),
            min(image.width, int(x + width + padding)),
            min(image.height, int(y + height + padding)),
        )
        return image.crop(box)


class NullOCRProvider(OCRProvider):
    capabilities = ProviderCapabilities(
        name="review-fallback",
        provider_type="ocr",
        description="Safe fallback that leaves text empty and sends regions to Needs Review",
        devices=["cpu"],
    )

    def recognize(self, image_path: Path, bbox: list[float], orientation: str, padding: int = 4) -> OCRResult:
        self.ensure_loaded()
        return OCRResult(text="", confidence=0.0, orientation=orientation, metadata={"fallback": True})


class MangaOCRProvider(OCRProvider):
    capabilities = ProviderCapabilities(
        name="manga-ocr",
        provider_type="ocr",
        description="Optional MangaOCR adapter for Japanese manga text",
        devices=["cpu", "cuda"],
        supports_batch=True,
        extra={"optional_dependency": "manga-ocr", "manga_specialized": True},
    )

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._model = None
        self._device = "cpu"
        self._batch_ceiling = 8
        try:
            configured = self.config.get("batch_size")
            self._batch_ceiling = max(1, min(8, int(configured))) if configured not in (None, "") else 8
        except (TypeError, ValueError, OverflowError):
            pass

    def load(self) -> None:
        try:
            from manga_ocr import MangaOcr
        except ImportError as exc:
            raise ProviderError(
                "manga-ocr is not installed; use review-fallback or install the optional dependency"
            ) from exc
        self._device = resolve_torch_device(self.config.get("device", "auto"))
        self._fallback_reason = None
        try:
            self._model = self._create_model(MangaOcr, self._device)
        except Exception as exc:
            if self._device == "cpu" or not is_accelerator_error(exc):
                raise
            logger.warning("MangaOCR could not start on %s; retrying on CPU: %s", self._device, exc)
            self._fallback_reason = "accelerator_initialization_failed"
            self._device = "cpu"
            release_torch_cuda()
            self._model = self._create_model(MangaOcr, self._device)

    def _create_model(self, model_class: type, device: str):
        return model_class(
            pretrained_model_name_or_path=str(self.config.get("model", "kha-white/manga-ocr-base")),
            force_cpu=device == "cpu",
        )

    def batch_size(self) -> int:
        """Bound batches by current free VRAM, and remember an OOM reduction.

        CPU execution remains single-crop. This is a scheduling hint, not a
        reservation: recognize_batch also handles memory changes after probing.
        """
        if not self._device.startswith("cuda"):
            return 1
        try:
            import torch
            from app.services.execution import gpu_reserve_bytes

            free, total = torch.cuda.mem_get_info(self._device)
            reserve = gpu_reserve_bytes(total)
            available = max(0, free - reserve)
            return max(1, min(self._batch_ceiling, available // (256 * 1024**2)))
        except Exception:
            return 1

    def _result(self, text: str, orientation: str, batch_size: int = 1) -> OCRResult:
        # The numeric confidence is legacy compatibility, not a model score.
        return OCRResult(
            text=text, confidence=0.8 if text else 0.0, orientation=orientation,
            metadata={"model": str(self.config.get("model", "kha-white/manga-ocr-base")),
                      "device": self._device, "confidence_available": False,
                      "inference_batch_size": batch_size},
        )

    def _recognize_crops(self, crops: list[Image.Image]) -> list[str]:
        import torch
        from manga_ocr.ocr import post_process

        # Match MangaOcr.__call__ exactly: grayscale -> RGB, the same processor,
        # generate parameters and text postprocessing. No dtype/model change.
        with torch.inference_mode():
            inputs = torch.stack([self._model._preprocess(crop.convert("L").convert("RGB")) for crop in crops])
            tokens = self._model.model.generate(inputs.to(self._model.model.device), max_length=300).cpu()
            return [post_process(text).strip() for text in self._model.tokenizer.batch_decode(tokens, skip_special_tokens=True)]

    def recognize_batch(self, image_path: Path, regions: list[tuple[list[float], str]], padding: int = 4,
                        *, check: Callable[[], None] | None = None) -> list[OCRResult]:
        """Infer a bounded microbatch; degrade on OOM without losing crop order."""
        if not regions:
            return []
        if check:
            check()
        self.ensure_loaded()
        size = min(len(regions), self.batch_size())
        if size < len(regions):
            return [result for start in range(0, len(regions), size)
                    for result in self.recognize_batch(image_path, regions[start:start+size], padding, check=check)]
        if size == 1:
            box, orientation = regions[0]
            return [self.recognize(image_path, box, orientation, padding)]
        crops = [_crop(image_path, box, padding) for box, _ in regions]
        try:
            texts = self._recognize_crops(crops)
        except Exception as exc:
            if not is_accelerator_error(exc):
                raise
            # Release failed generation tensors before retrying smaller chunks.
            # Fatal GPU errors reach the existing single-crop CPU fallback.
            self._batch_ceiling = max(1, size // 2) if "out of memory" in str(exc).lower() else 1
            exc.__traceback__ = None
            release_torch_cuda()
            return self.recognize_batch(image_path, regions, padding, check=check)
        if len(texts) != len(regions):
            raise ProviderError("MangaOCR 批量结果数量不匹配，原文未更新。")
        return [self._result(text, orientation, size) for text, (_, orientation) in zip(texts, regions, strict=True)]

    def recognize(self, image_path: Path, bbox: list[float], orientation: str, padding: int = 4) -> OCRResult:
        self.ensure_loaded()
        crop = _crop(image_path, bbox, padding)
        try:
            text = str(self._model(crop)).strip()
        except Exception as exc:
            if self._device == "cpu" or not is_accelerator_error(exc):
                raise
            logger.warning("MangaOCR failed on %s; retrying on CPU: %s", self._device, exc)
            self._fallback_reason = "accelerator_inference_failed"
            self._model = None
            self._device = "cpu"
            release_torch_cuda()
            from manga_ocr import MangaOcr

            self._model = self._create_model(MangaOcr, self._device)
            text = str(self._model(crop)).strip()
        return self._result(text, orientation)


class TesseractOCRProvider(OCRProvider):
    capabilities = ProviderCapabilities(
        name="tesseract",
        provider_type="ocr",
        description="Optional local Tesseract adapter with confidence reporting",
        devices=["cpu"],
    )

    def recognize(self, image_path: Path, bbox: list[float], orientation: str, padding: int = 4) -> OCRResult:
        self.ensure_loaded()
        try:
            import pytesseract
            from pytesseract import Output
        except ImportError as exc:
            raise ProviderError("pytesseract is not installed") from exc
        crop = _crop(image_path, bbox, padding)
        language = str(self.config.get("language", "jpn_vert" if orientation == "vertical" else "jpn"))
        data = pytesseract.image_to_data(crop, lang=language, output_type=Output.DICT)
        tokens = [text.strip() for text in data["text"] if text.strip()]
        scores = [float(score) for score in data["conf"] if str(score) not in {"-1", ""}]
        confidence = max(0.0, min(1.0, sum(scores) / max(1, len(scores)) / 100.0))
        separator = "" if orientation == "vertical" else " "
        return OCRResult(text=separator.join(tokens), confidence=confidence, orientation=orientation)


class PaddleOCRProvider(OCRProvider):
    capabilities = ProviderCapabilities(
        name="paddleocr",
        provider_type="ocr",
        description="Optional PaddleOCR Japanese adapter with line confidence",
        devices=["cpu", "cuda"],
        supports_batch=True,
        extra={"optional_dependency": "paddleocr"},
    )

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._model = None
        self._device = "cpu"

    def load(self) -> None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise ProviderError(
                "PaddleOCR is not installed; install paddleocr and a matching paddlepaddle package"
            ) from exc
        self._device = resolve_paddle_device(self.config.get("device", "auto"))
        self._fallback_reason = None
        try:
            self._model = self._create_model(PaddleOCR, self._device)
        except Exception as exc:
            if self._device == "cpu" or not is_accelerator_error(exc):
                raise
            logger.warning("Paddle OCR could not start on %s; retrying on CPU: %s", self._device, exc)
            self._fallback_reason = "accelerator_initialization_failed"
            self._device = "cpu"
            release_paddle_cuda()
            self._model = self._create_model(PaddleOCR, self._device)

    def _create_model(self, model_class: type, device: str):
        return model_class(
            lang=str(self.config.get("language", "japan")),
            ocr_version=str(self.config.get("ocr_version", "PP-OCRv5")),
            device=device,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    def recognize(self, image_path: Path, bbox: list[float], orientation: str, padding: int = 4) -> OCRResult:
        self.ensure_loaded()
        crop = _crop(image_path, bbox, padding)
        try:
            raw = self._model.predict(np.asarray(crop))
        except Exception as exc:
            if self._device == "cpu" or not is_accelerator_error(exc):
                raise
            logger.warning("Paddle OCR failed on %s; retrying on CPU: %s", self._device, exc)
            self._fallback_reason = "accelerator_inference_failed"
            self._model = None
            self._device = "cpu"
            release_paddle_cuda()
            from paddleocr import PaddleOCR

            self._model = self._create_model(PaddleOCR, self._device)
            raw = self._model.predict(np.asarray(crop))
        texts: list[str] = []
        scores: list[float] = []
        for item in extract_paddle_lines(raw):
            if item["text"]:
                texts.append(str(item["text"]))
                scores.append(float(item["confidence"]))
        separator = "" if orientation == "vertical" else " "
        return OCRResult(
            text=separator.join(text for text in texts if text),
            confidence=sum(scores) / len(scores) if scores else 0.0,
            orientation=orientation,
        )
