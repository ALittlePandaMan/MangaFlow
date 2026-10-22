from __future__ import annotations

import base64
import json
import math
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from app.services.base import OCRProvider, OCRResult, ProviderCapabilities, ProviderError
from app.services.cloud_budget import retry_after_seconds, validate_concurrency
from app.services.ocr.images import region_png

GENERIC_OCR_DEFAULTS = {
    "request_format": "json", "image_field": "image", "image_encoding": "base64",
    "auth_mode": "bearer", "api_key_header": "X-API-Key", "response_text_path": "text",
    "extra_fields": {}, "timeout": 90, "retries": 2,
}


class GenericCloudOCRProvider(OCRProvider):
    capabilities = ProviderCapabilities(
        name="cloud-ocr", provider_type="ocr", devices=["remote"],
        description="专用云端 OCR 的通用 HTTP 适配器，支持 JSON/Base64 或文件上传及返回字段映射。",
        extra={"generic_http": True},
    )

    @staticmethod
    def validate_config(config: dict[str, Any]) -> None:
        validate_concurrency(config)
        try:
            url = urlsplit(str(config.get("base_url") or "").strip())
            valid = url.scheme in {"http", "https"} and url.hostname and url.port != 0
        except ValueError:
            valid = False
        if not valid or url.username or url.password or url.query or url.fragment:
            raise ProviderError("云端 OCR 需要完整 HTTP(S) 识别接口地址，不能含密钥、查询参数或片段。")
        for key, options in {
            "request_format": {"json", "multipart", "chat_completions"}, "image_encoding": {"base64", "data_url"},
            "auth_mode": {"bearer", "api_key", "none"},
        }.items():
            if config.get(key, GENERIC_OCR_DEFAULTS[key]) not in options:
                raise ProviderError(f"云端 OCR 的 {key} 配置无效。")
        field = str(config.get("image_field", "image"))
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", field):
            raise ProviderError("图片字段名只支持字母、数字、下划线和连字符。")
        header = str(config.get("api_key_header", "X-API-Key"))
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", header) or header.lower() in {
            "host", "content-length", "content-type", "cookie", "connection", "transfer-encoding",
        }:
            raise ProviderError("API Key 请求头名称无效。")
        path = str(config.get("response_text_path", "text"))
        if not path or len(path) > 256 or not all(re.fullmatch(r"[\w-]+|\*", part) for part in path.split(".")):
            raise ProviderError("文字字段路径无效，例如 text 或 data.words.*.text。")
        extra = config.get("extra_fields", {})
        if not isinstance(extra, dict):
            raise ProviderError("附加参数必须是 JSON 对象。")
        def has_secret(value: Any) -> bool:
            if isinstance(value, dict):
                return any(str(key).lower().replace("-", "_") in {
                    "key", "api_key", "apikey", "authorization", "token", "access_token", "secret", "password",
                } or has_secret(item) for key, item in value.items())
            return isinstance(value, list) and any(has_secret(item) for item in value)
        if has_secret(extra):
            raise ProviderError("请勿在附加参数中保存密钥；请使用独立 API Key 字段。")
        if field in extra:
            raise ProviderError("附加参数不能覆盖图片字段。")
        if config.get("request_format") == "chat_completions":
            if not str(config.get("model") or "").strip():
                raise ProviderError("请填写专用 OCR 模型名称。")
            if set(extra) & {"model", "messages", "stream", "n", "max_tokens"}:
                raise ProviderError("附加参数不能覆盖 OCR 模型、消息、流式设置或输出限制。")
            limit = config.get("max_tokens", 2048)
            if not isinstance(limit, int) or isinstance(limit, bool) or not 128 <= limit <= 32768:
                raise ProviderError("最大输出 Token 数必须是 128–32768 的整数。")
        if config.get("request_format") == "multipart" and any(isinstance(v, (dict, list)) for v in extra.values()):
            raise ProviderError("文件上传的附加参数只支持字符串、数字、布尔值或 null。")
        for key, default, minimum, maximum in (("timeout", 90, 10, 600), ("retries", 2, 0, 5)):
            try:
                value = float(config.get(key, default))
            except (TypeError, ValueError):
                value = float("nan")
            if not math.isfinite(value) or not minimum <= value <= maximum or (key == "retries" and not value.is_integer()):
                raise ProviderError(f"云端 OCR 的 {key} 必须在 {minimum}–{maximum} 范围内。")

    def load(self) -> None:
        self.validate_config(self.config)
        if self.config.get("auth_mode", "bearer") != "none" and not str(self.config.get("api_key") or "").strip():
            raise ProviderError("云端 OCR 缺少 API Key，请先保存密钥。")
        super().load()

    def recognize(self, image_path: Path, bbox: list[float], orientation: str, padding: int = 4, *, polygon=None, crop_angle=None) -> OCRResult:
        return self._recognize(image_path, bbox, orientation, padding, int(self.config.get("retries", 2)), polygon=polygon, crop_angle=crop_angle)

    def recognize_once(self, image_path: Path, bbox: list[float], orientation: str, padding: int = 4, *, polygon=None, crop_angle=None) -> OCRResult:
        return self._recognize(image_path, bbox, orientation, padding, 0, polygon=polygon, crop_angle=crop_angle)

    def _recognize(self, image_path: Path, bbox: list[float], orientation: str, padding: int, retries: int, *, polygon=None, crop_angle=None) -> OCRResult:
        self.ensure_loaded()
        png = region_png(image_path, bbox, padding, polygon=polygon, orientation=orientation, crop_angle=crop_angle)
        field = str(self.config.get("image_field", "image"))
        extra = dict(self.config.get("extra_fields", {}))
        headers = {}
        mode = self.config.get("auth_mode", "bearer")
        if mode == "bearer":
            headers["Authorization"] = "Bearer " + str(self.config["api_key"]).strip()
        elif mode == "api_key":
            headers[str(self.config.get("api_key_header", "X-API-Key"))] = str(self.config["api_key"]).strip()
        format = self.config.get("request_format", "json")
        if format == "chat_completions":
            request = {"json": {
                **extra, "model": str(self.config["model"]).strip(), "stream": False,
                "max_tokens": self.config.get("max_tokens", 2048),
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}},
                    {"type": "text", "text": str(self.config.get("prompt") or "OCR:")},
                ]}],
            }}
        elif format == "multipart":
            request = {"files": {field: ("region.png", png, "image/png")}, "data": {
                key: value if isinstance(value, str) else json.dumps(value) for key, value in extra.items()
            }}
        else:
            encoded = base64.b64encode(png).decode("ascii")
            if self.config.get("image_encoding") == "data_url":
                encoded = "data:image/png;base64," + encoded
            request = {"json": {**extra, field: encoded}}
        with httpx.Client(timeout=float(self.config.get("timeout", 90)), follow_redirects=False) as client:
            for attempt in range(retries + 1):
                try:
                    response = client.post(str(self.config["base_url"]).strip(), headers=headers, **request)
                    if not 200 <= response.status_code < 300:
                        status = response.status_code
                        raise ProviderError(f"云端 OCR 请求失败（HTTP {status}），请检查接口、鉴权和请求格式。", retryable=status in {408, 429} or status >= 500, status_code=status, retry_after=retry_after_seconds(response.headers.get("Retry-After")))
                    body = response.json()
                    if format == "chat_completions":
                        choice = body["choices"][0]
                        if choice.get("finish_reason") in {"length", "max_tokens"}:
                            raise ProviderError("云端 OCR 输出被截断，请增大最大输出 Token 数；原文未更新。")
                        if choice.get("finish_reason") in {"content_filter", "refusal"} or choice["message"].get("refusal"):
                            raise ProviderError("云端 OCR 拒绝识别；原文未更新。")
                        text = self.extract_text(body, "choices.0.message.content")
                    else:
                        text = self.extract_text(body, str(self.config.get("response_text_path", "text")))
                    return OCRResult(text=text, confidence=0.0, orientation=orientation, metadata={
                        "provider": "cloud-ocr", "confidence_available": False,
                        "model": self.config.get("model"), "request_format": format,
                    })
                except httpx.RequestError:
                    error = ProviderError("云端 OCR 连接失败或超时，请检查接口地址和网络。", retryable=True, status_code=0)
                except (ValueError, KeyError, TypeError, IndexError):
                    error = ProviderError("云端 OCR 返回格式不匹配，请检查 JSON 文字字段路径；原文未更新。")
                except ProviderError as exc:
                    error = exc
                if not error.retryable or attempt == retries:
                    raise error from None
                time.sleep(max(min(2 ** attempt, 4), error.retry_after or 0))
        raise ProviderError("云端 OCR 识别失败。")

    @staticmethod
    def extract_text(data: Any, path: str) -> str:
        values = [data]
        for part in path.split("."):
            next_values = []
            for value in values:
                if part == "*" and isinstance(value, list):
                    next_values.extend(value)
                elif isinstance(value, dict) and part in value:
                    next_values.append(value[part])
                elif isinstance(value, list) and part.isdigit():
                    next_values.append(value[int(part)])
                else:
                    raise ValueError("Invalid text path")
            values = next_values
        if not all(isinstance(value, str) for value in values):
            raise ValueError("Text path must select strings")
        return "\n".join(value.strip() for value in values).strip()
