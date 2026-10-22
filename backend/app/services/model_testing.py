"""Disposable provider probes; never read or write a project or saved configuration."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from types import SimpleNamespace

import httpx
from app.services.base import ProviderError
from app.services.registry import registry
from PIL import Image, ImageDraw, ImageFont
from starlette.concurrency import run_in_threadpool


def _sample(directory: Path) -> Path:
    path = directory / "sample.png"
    image = Image.new("RGB", (256, 96), "white")
    ImageDraw.Draw(image).text((32, 24), "TEST", font=ImageFont.load_default(size=32), fill="black")
    image.save(path)
    return path


def _probe_image(kind: str, provider, directory: Path) -> None:
    source = _sample(directory)
    provider.ensure_loaded()
    if kind == "detection":
        provider.detect(source)
    elif kind == "ocr":
        result = provider.recognize(source, [0, 0, 256, 96], "horizontal", padding=0)
        if "remote" in provider.capabilities.devices and not result.text.strip():
            raise ProviderError("接口未返回有效识别结果")
    elif kind == "inpainting":
        mask = Image.new("L", (256, 96), 0)
        ImageDraw.Draw(mask).rectangle((28, 24, 150, 72), fill=255)
        mask_path, output = directory / "mask.png", directory / "clean.png"
        mask.save(mask_path)
        provider.inpaint(source, mask_path, output, "background_complex")
        with Image.open(output) as image:
            if image.size != (256, 96):
                raise ProviderError("修复服务返回了无效图片")
    elif kind == "rendering":
        box = [8, 8, 240, 80]
        polygon = [[8, 8], [248, 8], [248, 88], [8, 88]]
        region = SimpleNamespace(id="TEST", visible=True, translated_text="Test", bbox=box, polygon=polygon,
            translated_bbox=box, translated_polygon=polygon, font_size=24, auto_font_size=False,
            font_family="Noto Sans CJK SC", font_weight=400, text_color="#000000", stroke_color="#ffffff",
            stroke_width=0, alignment="center", line_spacing=1.15, character_spacing=0, rotation=0,
            perspective_warp=False, opacity=1, orientation="horizontal", reading_order=1, layout_data={})
        output, layer = directory / "rendered.png", directory / "text.png"
        provider.render(source, [region], output, layer)
        with Image.open(layer) as image:
            if image.getbbox() is None:
                raise ProviderError("排版服务未生成有效文字图层")


async def probe_provider(kind: str, name: str, supplied: dict) -> dict:
    started = monotonic()
    remote = kind == "translation" or name in {"cloud-multimodal", "cloud-ocr"}
    config = deepcopy(supplied)
    # A connection test is a single bounded request, not a production page job.
    if remote:
        config.update(timeout=30, retries=0, max_tokens=2048)
    try:
        if kind == "translation":
            provider = registry.translators[name](config)
            result = await provider.translate_regions([("TEST", "こんにちは")], source_language="ja", target_language="zh", context={})
            if not isinstance(result.get("TEST"), str) or not result["TEST"].strip():
                raise ProviderError("接口未返回有效译文")
        else:
            def run():
                provider = registry.ocr[name](config) if remote else registry.create(kind, name, config)
                with TemporaryDirectory(prefix="mangaflow-model-test-") as temporary:
                    _probe_image(kind, provider, Path(temporary))
            await run_in_threadpool(run)
        return {"ok": True, "mode": "remote" if remote else "local", "elapsed_ms": round((monotonic() - started) * 1000)}
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if remote:
            # Provider/network exceptions can embed URLs, headers or response
            # bodies. Only allow a status code out of this diagnostic boundary.
            message = f"连接测试失败（HTTP {status}），请检查密钥、模型及接口权限。" if isinstance(status, int) else (
                "连接测试超时，请检查网络和服务状态。" if isinstance(exc, httpx.TimeoutException)
                else "连接测试失败，请检查接口地址、密钥、模型及服务状态。")
        else:
            message = str(exc) if isinstance(exc, (ProviderError, ImportError, FileNotFoundError)) else "本地服务检测失败，请检查模型文件、运行依赖和设备配置。"
        return {"ok": False, "mode": "remote" if remote else "local", "message": message,
                "elapsed_ms": round((monotonic() - started) * 1000)}
