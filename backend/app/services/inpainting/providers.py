from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
from app.services.base import Inpainter, ProviderCapabilities, ProviderError
from app.services.device import is_accelerator_error, release_torch_cuda, resolve_torch_device
from app.utils.image_metadata import load_rgb_with_metadata, save_png_with_metadata
from PIL import Image

logger = logging.getLogger(__name__)


class OpenCVInpainter(Inpainter):
    capabilities = ProviderCapabilities(
        name="opencv",
        provider_type="inpainting",
        description="Uniform bubble fill plus OpenCV Telea/Navier-Stokes fallback",
        devices=["cpu"],
        extra={"strategies": ["uniform_fill", "telea", "navier_stokes"], "fallback": True},
    )

    def inpaint(self, image_path: Path, mask_path: Path, output_path: Path, region_type: str) -> Path:
        self.ensure_loaded()
        source, metadata = load_rgb_with_metadata(image_path)
        image = cv2.cvtColor(np.asarray(source), cv2.COLOR_RGB2BGR)
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            raise ValueError("Input image or mask cannot be read")
        if image.shape[:2] != mask.shape[:2]:
            raise ValueError("Mask dimensions must match the image")
        binary = np.where(mask > 8, 255, 0).astype(np.uint8)
        if cv2.countNonZero(binary) == 0:
            result = image.copy()
        elif region_type == "bubble_simple":
            result = self._uniform_fill(image, binary)
        else:
            radius = float(self.config.get("radius", 3.0))
            algorithm = cv2.INPAINT_NS if self.config.get("algorithm") == "ns" else cv2.INPAINT_TELEA
            result = cv2.inpaint(image, binary, radius, algorithm)
        # OpenCV's inpainting and the uniform-fill edge blur can touch pixels a
        # few positions beyond the requested mask. Composite the generated
        # repair through the binary mask so every outside pixel remains
        # byte-for-byte identical to the source page.
        generated = Image.fromarray(cv2.cvtColor(result, cv2.COLOR_BGR2RGB))
        composited = Image.composite(generated, source, Image.fromarray(binary))
        save_png_with_metadata(composited, output_path, metadata)
        return output_path

    @staticmethod
    def _uniform_fill(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        # For a glyph-shaped mask (such as text inside a button), the remaining
        # pixels inside its bounding box are the most reliable background. A
        # full line/polygon mask has no such pixels, so sample its outside ring.
        x, y, width, height = cv2.boundingRect(mask)
        crop_mask = mask[y : y + height, x : x + width]
        occupancy = cv2.countNonZero(crop_mask) / max(1, width * height)
        inside_samples = image[y : y + height, x : x + width][crop_mask <= 8]
        if occupancy < 0.88 and len(inside_samples) >= 16:
            samples = inside_samples
        else:
            outer = cv2.dilate(mask, np.ones((15, 15), np.uint8), iterations=1)
            inner = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)
            ring = cv2.subtract(outer, inner)
            samples = image[ring > 0]
        if samples.size:
            # Bubble edges, adjacent lettering and antialiasing can make the
            # ring's raw variance high even though its background is flat.
            # Select its largest color cluster instead of falling back to
            # texture inpainting, which creates grey smears in white bubbles.
            compact = samples.reshape(-1, 3).astype(np.float32)
            cluster_count = max(1, min(4, len(compact) // 80))
            if cluster_count == 1:
                fill_color = np.median(compact, axis=0).astype(np.uint8)
            else:
                criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.2)
                _, labels, centers = cv2.kmeans(
                    compact,
                    cluster_count,
                    None,
                    criteria,
                    3,
                    cv2.KMEANS_PP_CENTERS,
                )
                counts = np.bincount(labels.ravel(), minlength=cluster_count)
                fill_color = centers[int(np.argmax(counts))].astype(np.uint8)
            filled = image.copy()
            filled[mask > 0] = fill_color
            # Blend only mask edges so the bubble border stays intact.
            soft = cv2.GaussianBlur(mask, (5, 5), 0).astype(np.float32)[:, :, None] / 255.0
            return (filled.astype(np.float32) * soft + image.astype(np.float32) * (1 - soft)).astype(np.uint8)
        return cv2.inpaint(image, mask, 3, cv2.INPAINT_TELEA)


class SimpleLaMaInpainter(Inpainter):
    capabilities = ProviderCapabilities(
        name="lama",
        provider_type="inpainting",
        description="Optional LaMa deep inpainting adapter via simple-lama-inpainting",
        devices=["cpu", "cuda"],
        extra={"structure_aware": True, "optional_dependency": "simple-lama-inpainting"},
    )

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._model = None
        self._device = "cpu"

    def load(self) -> None:
        try:
            import torch
            from simple_lama_inpainting import SimpleLama
        except ImportError as exc:
            raise ProviderError("simple-lama-inpainting is not installed") from exc
        self._device = resolve_torch_device(self.config.get("device", "auto"))
        try:
            self._model = SimpleLama(device=torch.device(self._device))
        except Exception as exc:
            if self._device == "cpu" or not is_accelerator_error(exc):
                raise
            logger.warning("LaMa could not start on %s; retrying on CPU: %s", self._device, exc)
            self._device = "cpu"
            release_torch_cuda()
            self._model = SimpleLama(device=torch.device(self._device))

    def inpaint(self, image_path: Path, mask_path: Path, output_path: Path, region_type: str) -> Path:
        self.ensure_loaded()
        image, metadata = load_rgb_with_metadata(image_path)
        with Image.open(mask_path) as mask_source:
            mask = mask_source.convert("L")
        if image.size != mask.size:
            raise ValueError("Mask dimensions must match the image")
        try:
            result = self._model(image, mask)
        except Exception as exc:
            if self._device == "cpu" or not is_accelerator_error(exc):
                raise
            logger.warning("LaMa failed on %s; retrying on CPU: %s", self._device, exc)
            self._model = None
            self._device = "cpu"
            release_torch_cuda()
            import torch
            from simple_lama_inpainting import SimpleLama

            self._model = SimpleLama(device=torch.device(self._device))
            result = self._model(image, mask)
        # The reference model pads inputs to a multiple of eight. Never let that
        # implementation detail change page dimensions between pipeline regions.
        # Some model versions also slightly alter pixels outside the mask, so
        # explicitly composite only the requested repair area over the input.
        generated = result.convert("RGB").crop((0, 0, image.width, image.height))
        composited = Image.composite(generated, image, mask)
        save_png_with_metadata(composited, output_path, metadata)
        return output_path


class HybridInpainter(Inpainter):
    """Backward-compatible provider alias that routes every repair to LaMa."""

    capabilities = ProviderCapabilities(
        name="hybrid",
        provider_type="inpainting",
        description="Backward-compatible alias using LaMa for every repair region",
        devices=["cpu", "cuda"],
        extra={"structure_aware": True, "complex_only": True, "optional_dependency": "simple-lama-inpainting"},
    )

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._lama = SimpleLaMaInpainter(
            {"device": self.config.get("device", "auto"), **dict(self.config.get("lama", {}))}
        )

    def load(self) -> None:
        self._lama.ensure_loaded()

    def unload(self) -> None:
        self._lama.unload()
        super().unload()

    def inpaint(self, image_path: Path, mask_path: Path, output_path: Path, region_type: str) -> Path:
        self.ensure_loaded()
        return self._lama.inpaint(image_path, mask_path, output_path, "background_complex")
