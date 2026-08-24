import pytest
from app.services.base import ProviderError
from app.services.translation.providers import (
    OpenAICompatibleTranslator,
    PassthroughTranslator,
    api_auth_headers,
    normalize_api_base_url,
    resolve_api_protocol,
)


def test_structured_translation_parser_accepts_fenced_json() -> None:
    parsed = OpenAICompatibleTranslator._parse('```json\n{"R001":"你好","R002":"去哪？"}\n```')
    assert parsed == {"R001": "你好", "R002": "去哪？"}


def test_structured_translation_parser_normalizes_bracket_ids() -> None:
    assert OpenAICompatibleTranslator._parse('{"translations":{"[R001]":"你好"}}') == {"R001": "你好"}


@pytest.mark.asyncio
async def test_passthrough_is_explicit_and_id_stable() -> None:
    provider = PassthroughTranslator()
    result = await provider.translate_regions(
        [("R001", "こんにちは"), ("R002", "学校だよ")],
        source_language="ja",
        target_language="zh-CN",
        context={},
    )
    assert result == {"R001": "こんにちは", "R002": "学校だよ"}
    assert provider.capabilities.extra["fallback"] is True


def test_parser_rejects_non_object_json() -> None:
    with pytest.raises(ProviderError):
        OpenAICompatibleTranslator._parse('["R001", "你好"]')


def test_api_protocol_auto_detection_and_base_url_normalization() -> None:
    assert resolve_api_protocol("auto", "https://api.anthropic.com/v1") == "anthropic"
    assert resolve_api_protocol("auto", "https://api.openai.com/v1/responses") == "responses"
    assert resolve_api_protocol("auto", "https://example.com/v1") == "openai"
    assert normalize_api_base_url("https://api.openai.com/v1/responses") == "https://api.openai.com/v1"


def test_api_protocol_uses_correct_authentication_headers() -> None:
    assert api_auth_headers("openai", "secret")["Authorization"] == "Bearer secret"
    anthropic = api_auth_headers("anthropic", "secret")
    assert anthropic["x-api-key"] == "secret"
    assert anthropic["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in anthropic


def test_responses_request_and_content_extraction() -> None:
    endpoint, _, payload = OpenAICompatibleTranslator._build_request(
        protocol="responses",
        base_url="https://api.openai.com/v1",
        api_key="secret",
        model="demo",
        system_prompt="translate",
        user_prompt="[R001] text",
        expected_ids={"R001"},
        temperature=0.2,
        max_tokens=4096,
    )
    assert endpoint == "https://api.openai.com/v1/responses"
    assert payload["text"]["format"]["schema"]["required"] == ["R001"]
    body = {"output": [{"content": [{"type": "output_text", "text": '{"R001":"你好"}'}]}]}
    assert OpenAICompatibleTranslator._extract_content(body, "responses") == '{"R001":"你好"}'


def test_anthropic_request_and_content_extraction() -> None:
    endpoint, headers, payload = OpenAICompatibleTranslator._build_request(
        protocol="anthropic",
        base_url="https://api.anthropic.com/v1",
        api_key="secret",
        model="demo",
        system_prompt="translate",
        user_prompt="[R001] text",
        expected_ids={"R001"},
        temperature=0.2,
        max_tokens=4096,
    )
    assert endpoint == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "secret"
    assert payload["system"] == "translate"
    body = {"content": [{"type": "text", "text": '{"R001":"你好"}'}]}
    assert OpenAICompatibleTranslator._extract_content(body, "anthropic") == '{"R001":"你好"}'
