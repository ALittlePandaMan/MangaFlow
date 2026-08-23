from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import urlsplit

import httpx
from app.services.base import ProviderCapabilities, ProviderError, Translator
from app.services.translation.prompts import DEFAULT_TRANSLATION_PROMPT


API_PROTOCOL_ALIASES = {
    "auto": "auto",
    "openai": "openai",
    "openai-chat": "openai",
    "chat-completions": "openai",
    "response": "responses",
    "responses": "responses",
    "anthropic": "anthropic",
    "anthropic-messages": "anthropic",
}


def normalize_api_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/messages", "/models"):
        if normalized.endswith(suffix):
            return normalized.removesuffix(suffix)
    return normalized


def resolve_api_protocol(value: object, base_url: str) -> str:
    configured = API_PROTOCOL_ALIASES.get(str(value or "auto").strip().lower())
    if configured is None:
        raise ProviderError(f"Unsupported translation API protocol: {value}")
    if configured != "auto":
        return configured
    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/").lower()
    hostname = (parsed.hostname or "").lower()
    if path.endswith("/messages") or hostname == "anthropic.com" or hostname.endswith(".anthropic.com"):
        return "anthropic"
    if path.endswith("/responses"):
        return "responses"
    return "openai"


def api_auth_headers(protocol: str, api_key: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if protocol == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        if api_key:
            headers["x-api-key"] = api_key
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


class PassthroughTranslator(Translator):
    capabilities = ProviderCapabilities(
        name="passthrough",
        provider_type="translation",
        description="Offline fallback preserving source text; results remain marked for review",
        devices=["cpu"],
        supports_batch=True,
        extra={"fallback": True},
    )

    async def translate_regions(
        self,
        regions: list[tuple[str, str]],
        *,
        source_language: str,
        target_language: str,
        context: dict[str, Any],
    ) -> dict[str, str]:
        self.ensure_loaded()
        return {region_id: text for region_id, text in regions}


class OpenAICompatibleTranslator(Translator):
    capabilities = ProviderCapabilities(
        name="openai-compatible",
        provider_type="translation",
        description="Structured region translation using any OpenAI-compatible chat endpoint",
        devices=["remote"],
        supports_batch=True,
        extra={"structured_json": True},
    )

    async def translate_regions(
        self,
        regions: list[tuple[str, str]],
        *,
        source_language: str,
        target_language: str,
        context: dict[str, Any],
    ) -> dict[str, str]:
        self.ensure_loaded()
        if not regions:
            return {}
        raw_base_url = str(self.config.get("base_url", "http://localhost:11434/v1"))
        protocol = resolve_api_protocol(self.config.get("api_protocol", "auto"), raw_base_url)
        base_url = normalize_api_base_url(raw_base_url)
        model = str(self.config.get("model", "qwen2.5:7b"))
        api_key = str(self.config.get("api_key") or "")
        timeout = float(self.config.get("timeout", 90))
        retries = int(self.config.get("retries", 2))
        expected_ids = {region_id for region_id, _ in regions}
        region_lines = "\n".join(f"[{region_id}] {text}" for region_id, text in regions)
        system_prompt = str(
            self.config.get(
                "prompt",
                DEFAULT_TRANSLATION_PROMPT,
            )
        )
        instructions = {
            "source_language": source_language,
            "target_language": target_language,
            "required_output": {region_id: "translated text" for region_id in sorted(expected_ids)},
            "rules": ["Do not change IDs", "Do not omit or merge entries", "Return one JSON object only"],
            "project_context": context,
        }
        user_prompt = json.dumps(instructions, ensure_ascii=False) + "\n\n" + region_lines
        endpoint, headers, payload = self._build_request(
            protocol=protocol,
            base_url=base_url,
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            expected_ids=expected_ids,
            temperature=float(self.config.get("temperature", 0.2)),
            max_tokens=int(self.config.get("max_tokens", 4096)),
        )
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        endpoint,
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()
                body = response.json()
                content = self._extract_content(body, protocol)
                translations = self._parse(content)
                received_ids = set(translations)
                if received_ids != expected_ids:
                    missing = sorted(expected_ids - received_ids)
                    extra = sorted(received_ids - expected_ids)
                    raise ProviderError(f"Translation ID mismatch; missing={missing}, extra={extra}", retryable=True)
                if any(not isinstance(value, str) or not value.strip() for value in translations.values()):
                    raise ProviderError("Translation response contains empty or non-string values", retryable=True)
                return {key: value.strip() for key, value in translations.items()}
            except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError, ProviderError) as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(min(2**attempt, 4))
        raise ProviderError(f"Translation failed after {retries + 1} attempts: {last_error}", retryable=True)

    @staticmethod
    def _build_request(
        *,
        protocol: str,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        expected_ids: set[str],
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        headers = api_auth_headers(protocol, api_key)
        if protocol == "anthropic":
            return f"{base_url}/messages", headers, {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            }
        if protocol == "responses":
            properties = {region_id: {"type": "string"} for region_id in sorted(expected_ids)}
            return f"{base_url}/responses", headers, {
                "model": model,
                "temperature": temperature,
                "instructions": system_prompt,
                "input": user_prompt,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "manga_region_translations",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": properties,
                            "required": sorted(expected_ids),
                            "additionalProperties": False,
                        },
                    }
                },
            }
        return f"{base_url}/chat/completions", headers, {
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }

    @staticmethod
    def _extract_content(body: object, protocol: str) -> str | dict[str, Any]:
        if not isinstance(body, dict):
            raise ProviderError("Translation API returned an invalid response", retryable=True)
        if protocol == "anthropic":
            content = body.get("content")
            if isinstance(content, list):
                texts = [item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"]
                joined = "".join(item for item in texts if isinstance(item, str)).strip()
                if joined:
                    return joined
        elif protocol == "responses":
            if isinstance(body.get("output_text"), str) and body["output_text"].strip():
                return body["output_text"]
            output = body.get("output")
            if isinstance(output, list):
                texts: list[str] = []
                for item in output:
                    if not isinstance(item, dict) or not isinstance(item.get("content"), list):
                        continue
                    for part in item["content"]:
                        if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                            texts.append(part["text"])
                if texts:
                    return "".join(texts)
        else:
            choices = body.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), (str, dict)):
                    return message["content"]
        raise ProviderError(f"{protocol} response does not contain translated text", retryable=True)

    @staticmethod
    def _parse(content: str | dict[str, Any]) -> dict[str, str]:
        if isinstance(content, dict):
            parsed = content
        else:
            cleaned = content.strip()
            fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
            parsed = json.loads(fenced.group(1) if fenced else cleaned)
        if "translations" in parsed and isinstance(parsed["translations"], dict):
            parsed = parsed["translations"]
        if not isinstance(parsed, dict):
            raise ProviderError("Translation response must be a JSON object", retryable=True)
        return {str(key).strip("[]"): str(value) for key, value in parsed.items()}
