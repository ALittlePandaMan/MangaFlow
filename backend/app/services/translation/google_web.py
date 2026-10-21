"""Anonymous webpage translation, not the authenticated Google Cloud API.

Only region text is sent, never credentials, images or project context. The
public compatibility endpoint may change or rate-limit requests.
"""
from __future__ import annotations

import asyncio
import html
import math
import re
import time
from collections.abc import Callable
from typing import Any

import httpx
from app.core.timing import record_timing
from app.services.base import ProviderCapabilities, ProviderError, Translator
from app.services.cloud_budget import request_budget, retry_after_seconds
from app.services.cloud_results import CloudResultCorrupt, current_cloud_results
from app.services.workflow_state import fingerprint
from app.utils.text import has_text

GOOGLE_WEB_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
GOOGLE_WEB_DEFAULTS = {"timeout": 30}
_LANGUAGE_ALIASES = {
    "jp": "ja", "ja-jp": "ja", "kr": "ko", "ko-kr": "ko",
    "zh": "zh-CN", "zh-cn": "zh-CN", "zh-hans": "zh-CN", "zh-sg": "zh-CN",
    "zh-tw": "zh-TW", "zh-hk": "zh-TW", "zh-hant": "zh-TW",
}


def google_language(value: str, *, target: bool = False) -> str:
    code = str(value).strip().replace("_", "-").lower()
    if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*|auto", code) or target and code == "auto":
        raise ProviderError("网页翻译需要有效的源语言和明确的目标语言。", error_kind="translation_language_invalid")
    return _LANGUAGE_ALIASES.get(code, code)


def text_chunks(text: str, limit: int = 4000) -> list[str]:
    chunks = []
    while len(text) > limit:
        end = max(text.rfind(marker, limit // 2, limit) for marker in ("\n", "。", "！", "？", ". ", " "))
        end = end + 1 if end >= limit // 2 else limit
        chunks.append(text[:end])
        text = text[end:]
    if text:
        chunks.append(text)
    return chunks


class GoogleWebTranslator(Translator):
    capabilities = ProviderCapabilities(
        name="google-web", provider_type="translation",
        description="Google 网页翻译（免登录、免密钥、免模型；网页接口可能限流）",
        devices=["remote"], supports_batch=True,
        extra={"keyless": True, "web_translation": True, "supports_context": False},
    )

    def load(self) -> None:
        timeout = self.config.get("timeout", 30)
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 1 <= timeout <= 120):
            raise ProviderError("网页翻译超时必须为 1–120 秒。")
        super().load()

    async def translate_regions(
        self, regions: list[tuple[str, str]], *, source_language: str, target_language: str,
        context: dict[str, Any], check_interruption: Callable[[], None] | None = None,
    ) -> dict[str, str]:
        if not regions:
            return {}
        if len({key for key, _ in regions}) != len(regions):
            raise ProviderError("Translation region IDs must be unique", error_kind="translation_duplicate_ids")
        if any(not has_text(text) for _, text in regions):
            raise ProviderError("翻译输入包含空原文，未发送请求；请先核对 OCR 结果。", error_kind="translation_empty_source")
        source, target = google_language(source_language), google_language(target_language, target=True)
        self.ensure_loaded()
        # One conservative anonymous-service budget shared by all pages.
        # Never inherit an LLM key, URL, concurrency override or image option.
        policy = {"base_url": GOOGLE_WEB_ENDPOINT, "concurrency_mode": "serial",
                  "max_concurrency": 1, "requests_per_minute": 30}
        budget = request_budget(policy)
        budget.configure(policy)
        cache = current_cloud_results.get()
        results = {}
        async with httpx.AsyncClient(timeout=float(self.config.get("timeout", 30))) as client:
            for region_id, text in regions:
                parts = []
                for index, chunk in enumerate(text_chunks(text)):
                    if check_interruption:
                        check_interruption()
                    if not has_text(chunk):
                        continue
                    key = fingerprint({"format": "google-web-v1", "source": source, "target": target,
                                       "region": region_id, "part": index, "text": chunk})
                    saved = cache.get(key) if cache else None
                    if saved is not None and (set(saved) != {"text"} or not has_text(saved["text"])):
                        if not cache.allow_invalid:
                            raise CloudResultCorrupt()
                        cache.archive_received(key)
                        saved = None
                    if saved is not None:
                        translated = saved["text"]
                    else:
                        translated = await self._translate_text(client, chunk, source, target, budget, check_interruption)
                        if cache:
                            cache.put(key, {"text": translated})
                    parts.append(translated)
                    if check_interruption:
                        check_interruption()
                separator = "" if target in {"ja", "zh-CN", "zh-TW"} else " "
                results[region_id] = separator.join(parts).strip()
        return results

    async def _translate_text(self, client, text, source, target, budget, check) -> str:
        while not budget.try_acquire():
            if check:
                check()
            await asyncio.sleep(budget.poll_interval())
        started, error = time.monotonic(), None
        try:
            if check:
                check()
            # POST keeps source text out of request URLs and access logs.
            response = await client.post(GOOGLE_WEB_ENDPOINT,
                params={"client": "gtx", "sl": source, "tl": target, "dt": "t", "dj": "1"}, data={"q": text})
            if response.status_code == 429:
                raise ProviderError("Google 网页翻译已限流，请稍后重试或切换翻译服务。", status_code=429,
                    retry_after=retry_after_seconds(response.headers.get("retry-after")) or 60,
                    error_kind="translation_rate_limited")
            if response.status_code != 200:
                raise ProviderError(f"Google 网页翻译暂不可用（HTTP {response.status_code}），未覆盖已有译文。",
                    status_code=response.status_code, error_kind="translation_provider_error")
            try:
                body = response.json()
                sentences = body.get("sentences") if isinstance(body, dict) else None
                if not isinstance(sentences, list) or any(not isinstance(item, dict) or not isinstance(item.get("trans"), str) for item in sentences):
                    raise ValueError()
                translated = html.unescape("".join(item["trans"] for item in sentences)).strip()
            except (ValueError, TypeError):
                raise ProviderError("Google 网页翻译返回了无效响应，可能需要验证或接口已变化；请稍后重试。",
                    error_kind="translation_response_invalid") from None
            if not has_text(translated):
                raise ProviderError("Google 网页翻译返回空译文，未覆盖已有译文。", error_kind="translation_empty_values")
            return translated
        except httpx.HTTPError:
            error = ProviderError("Google 网页翻译连接失败或超时，请检查网络后重试。",
                status_code=0, error_kind="translation_transport_error")
            raise error from None
        except ProviderError as exc:
            error = exc
            raise
        finally:
            elapsed = time.monotonic() - started
            record_timing("cloud.translation_request", elapsed)
            budget.finish(elapsed, error, kind="translation", units=max(.1, len(text) / 1000))
