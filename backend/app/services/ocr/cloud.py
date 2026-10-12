from __future__ import annotations

import base64
import json
import math
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from app.services.base import OCRProvider, OCRResult, ProviderCapabilities, ProviderError
from app.services.cloud_budget import retry_after_seconds, validate_concurrency
from app.services.ocr.images import region_png
from app.services.ocr.prompts import DEFAULT_OCR_PROMPT, OCR_OUTPUT_CONTRACT
from app.services.translation.providers import api_auth_headers, normalize_api_base_url, resolve_api_protocol


class CloudMultimodalOCRProvider(OCRProvider):
    capabilities = ProviderCapabilities(
        name="cloud-multimodal", provider_type="ocr",
        description="多模态大语言模型直接看图识字，可复用翻译模型连接，独立设置识字 Prompt。",
        devices=["remote"], extra={"multimodal": True, "requires_api_key": True},
    )

    def load(self) -> None:
        # Validation/preloading must never upload an image or incur API charges.
        self.validate_config(self.config)
        if not str(self.config.get("api_key") or "").strip():
            raise ProviderError("云端 OCR 缺少 API Key，请先保存密钥。")
        super().load()

    @staticmethod
    def validate_config(config: dict[str, Any]) -> None:
        validate_concurrency(config)
        try:
            url = urlsplit(str(config.get("base_url") or "").strip())
            valid_url = url.scheme in {"http", "https"} and url.hostname and url.port != 0
        except ValueError:
            valid_url = False
        if not valid_url or url.username or url.password or url.query or url.fragment:
            raise ProviderError("云端 OCR 需要有效的 HTTP(S) API Base URL，不能包含密钥、查询参数或片段。")
        if not str(config.get("model") or "").strip():
            raise ProviderError("请选择或填写支持图片输入的 OCR 模型。")
        resolve_api_protocol(config.get("api_protocol"), str(config["base_url"]))
        for key, default, minimum, maximum in (
            ("timeout", 90, 10, 600), ("retries", 2, 0, 5), ("max_tokens", 2048, 128, 32768),
        ):
            try:
                value = float(config.get(key, default))
            except (TypeError, ValueError):
                value = float("nan")
            if not math.isfinite(value) or not minimum <= value <= maximum or (key != "timeout" and not value.is_integer()):
                raise ProviderError(f"云端 OCR 的 {key} 必须在 {minimum}–{maximum} 范围内。")
        if config.get("temperature") is not None:
            try:
                temperature = float(config["temperature"])
            except (TypeError, ValueError):
                temperature = float("nan")
            if not math.isfinite(temperature) or not 0 <= temperature <= 1:
                raise ProviderError("云端 OCR 的 Temperature 必须在 0–1 范围内，或留空使用模型默认值。")

    def recognize(self, image_path: Path, bbox: list[float], orientation: str, padding: int = 4) -> OCRResult:
        return self._recognize(image_path, bbox, orientation, padding, int(self.config.get("retries", 2)))

    def recognize_once(self, image_path: Path, bbox: list[float], orientation: str, padding: int = 4) -> OCRResult:
        return self._recognize(image_path, bbox, orientation, padding, 0)

    def _recognize(self, image_path: Path, bbox: list[float], orientation: str, padding: int, retries: int) -> OCRResult:
        self.ensure_loaded()
        encoded = base64.b64encode(region_png(image_path, bbox, padding)).decode("ascii")
        base_url = str(self.config["base_url"]).strip()
        protocol = resolve_api_protocol(self.config.get("api_protocol"), base_url)
        endpoint, payload = self._request(protocol, encoded, orientation)
        headers = api_auth_headers(protocol, str(self.config["api_key"]).strip())
        with httpx.Client(timeout=float(self.config.get("timeout", 90)), follow_redirects=False) as client:
            for attempt in range(retries + 1):
                try:
                    response = client.post(normalize_api_base_url(base_url) + endpoint, headers=headers, json=payload)
                    if response.status_code != 200:
                        status = response.status_code
                        hint = "请检查密钥和接口权限。" if status in {401, 403} else "请检查接口协议、模型的图片输入能力和请求参数。"
                        raise ProviderError(f"云端 OCR 请求失败（HTTP {status}），{hint}", retryable=status in {408, 429} or status >= 500, status_code=status, retry_after=retry_after_seconds(response.headers.get("Retry-After")))
                    text = self._parse_response(response.json(), protocol)
                    return OCRResult(text=text, confidence=0.0, orientation=orientation, metadata={
                        "provider": "cloud-multimodal", "model": str(self.config["model"]).strip(),
                        "api_protocol": protocol, "confidence_available": False,
                    })
                except httpx.RequestError:
                    error = ProviderError("云端 OCR 连接失败或超时，请检查服务地址和网络。", retryable=True, status_code=0)
                except (ValueError, KeyError, TypeError, IndexError):
                    error = ProviderError('云端 OCR 未返回有效识别结果，需要 JSON 格式 {"text":"原文"}。', retryable=True)
                except ProviderError as exc:
                    error = exc
                if not error.retryable or attempt == retries:
                    # Never expose upstream response bodies, request URLs or credentials.
                    raise error from None
                time.sleep(max(min(2 ** attempt, 4), error.retry_after or 0))
        raise ProviderError("云端 OCR 识别失败。")

    def _request(self, protocol: str, encoded: str, orientation: str) -> tuple[str, dict[str, Any]]:
        prompt = str(self.config.get("prompt") or DEFAULT_OCR_PROMPT).strip() or DEFAULT_OCR_PROMPT
        system = prompt + "\n\n" + OCR_OUTPUT_CONTRACT
        instruction = f"Transcribe this cropped text region. Text orientation: {orientation}."
        model = str(self.config["model"]).strip()
        limit = int(self.config.get("max_tokens", 2048))
        common: dict[str, Any] = {"model": model}
        if self.config.get("temperature") is not None:
            common["temperature"] = float(self.config["temperature"])
        image_url = f"data:image/png;base64,{encoded}"
        if protocol == "anthropic":
            return "/messages", {**common, "max_tokens": limit, "system": system, "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": encoded}},
                {"type": "text", "text": instruction},
            ]}]}
        if protocol == "responses":
            return "/responses", {**common, "max_output_tokens": limit, "store": False, "instructions": system, "input": [{"role": "user", "content": [
                {"type": "input_image", "image_url": image_url, "detail": "high"},
                {"type": "input_text", "text": instruction},
            ]}]}
        return "/chat/completions", {**common, "max_completion_tokens": limit, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
                {"type": "text", "text": instruction},
            ]},
        ]}

    @staticmethod
    def _parse_response(data: dict[str, Any], protocol: str) -> str:
        if not isinstance(data, dict):
            raise ValueError("Invalid response object")
        if protocol == "anthropic":
            stop = data.get("stop_reason")
            blocks = data["content"]
        elif protocol == "responses":
            stop = data.get("status")
            blocks = [block for item in data.get("output", []) for block in item.get("content", [])]
        else:
            choice = data["choices"][0]
            stop = choice.get("finish_reason")
            if choice["message"].get("refusal"):
                raise ProviderError("云端 OCR 模型拒绝了识别请求，未写入原文。")
            content = choice["message"]["content"]
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
        if stop in {"length", "max_tokens", "incomplete"}:
            raise ProviderError("云端 OCR 输出被截断，请增大最大输出 Token 数或缩小识别区域。")
        if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
            raise ValueError("Invalid response content")
        if stop in {"content_filter", "refusal", "failed"} or any(block.get("type") == "refusal" for block in blocks):
            raise ProviderError("云端 OCR 未完成识别，未写入原文。")
        content = "".join(block["text"] for block in blocks if block.get("type") in {"text", "output_text"})
        if not content and protocol == "responses":
            content = data.get("output_text", "")
        content = content.strip()
        if content.startswith("```json\n") and content.endswith("```"):
            content = content[8:-3].strip()
        elif content.startswith("```\n") and content.endswith("```"):
            content = content[4:-3].strip()
        result = json.loads(content)
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise ValueError("Missing OCR text")
        return result["text"].strip()
