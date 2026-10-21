import asyncio
import json

import httpx
import pytest
from app.services import cloud_budget as budgets
from app.services.base import ProviderError
from app.services.cloud_results import CloudResultCache, current_cloud_results
from app.services.translation.providers import OpenAICompatibleTranslator as Translator
from app.services.workflow_state import fingerprint


@pytest.fixture(autouse=True)
def isolated_budgets():
    budgets._budgets.clear()
    yield
    budgets._budgets.clear()


@pytest.mark.parametrize("protocol,body", [
    ("openai", {"choices": [{"message": {"content": [{"type": "text", "text": '{"R001":"译文"}'}]}}]}),
    ("openai", {"choices": [{"message": {"content": None, "parsed": {"R001": "译文"}}}]}),
    ("responses", {"error": None, "output": [{"content": [{"type": "output_text", "text": '{"R001":"译文"}'}]}]}),
    ("anthropic", {"content": [{"type": "thinking", "text": "not translation"}, {"type": "text", "text": '{"R001":"译文"}'}]}),
])
def test_standard_content_forms_and_replay_projection(protocol, body):
    for value in (body, Translator._received_body(body)):
        assert Translator._parse(Translator._extract_content(value, protocol)) == {"R001": "译文"}


@pytest.mark.parametrize("protocol,body,kind", [
    ("openai", {"choices": [{"message": {"refusal": "provider policy"}}]}, "translation_response_refused"),
    ("openai", {"choices": [{"finish_reason": "content_filter", "message": {"content": None}}]}, "translation_response_refused"),
    ("openai", {"choices": [{"finish_reason": "prohibited_content", "message": {"content": None}}]}, "translation_response_refused"),
    ("openai", {"choices": [{"finish_reason": "PROHIBITED_CONTENT", "message": {"content": None}}]}, "translation_response_refused"),
    ("openai", {"choices": [{"finish_reason": "SAFETY", "message": {"content": None}}]}, "translation_response_refused"),
    ("openai", {"choices": [{"message": {"content": [{"type": "refusal", "refusal": "no"}]}}]}, "translation_response_refused"),
    ("responses", {"output": [{"content": [{"type": "refusal", "refusal": "no"}]}]}, "translation_response_refused"),
    ("openai", {"choices": [{"finish_reason": "length", "message": {"content": None}}]}, "translation_response_truncated"),
    ("responses", {"status": "incomplete", "output": []}, "translation_response_truncated"),
    ("openai", {"choices": []}, "translation_response_empty"),
    ("openai", {"error": {"message": "private upstream detail"}}, "translation_provider_error"),
])
def test_received_errors_are_specific_and_do_not_echo_provider_content(protocol, body, kind):
    for value in (body, Translator._received_body(body)):
        with pytest.raises(ProviderError) as error:
            Translator._extract_content(value, protocol)
        assert error.value.error_kind == kind
        assert "private upstream detail" not in str(error.value)


@pytest.mark.parametrize("content,expected", [
    ('{"R001":"完整译文","R002":"被截断', {"R001": "完整译文"}),
    ('```json\n{"translations":{"R001":"完整译文","R002":', {"R001": "完整译文"}),
    ('{"R001":"反斜杠\\\\和转义\\\"引号","R002":', {"R001": '反斜杠\\和转义"引号'}),
])
def test_only_complete_json_pairs_are_recovered_from_truncation(content, expected):
    assert Translator._parse(content) == expected


@pytest.mark.parametrize("content", ['[]', 'null', '{"R001":12}', 'not json', '{"R001":"unfinished'])
def test_invalid_or_nonobject_responses_fail_as_diagnosed_provider_errors(content):
    with pytest.raises(ProviderError) as error:
        Translator._parse(content)
    assert error.value.error_kind.startswith("translation_")


@pytest.mark.parametrize('content', ['{"R001":"first","R001":"second"}',
    '{"translations":{"R001":"first","R001":"second"}}', '{"R001":"first","[R001]":"second"}'])
def test_duplicate_ids_cannot_silently_overwrite_translation(content):
    with pytest.raises(ProviderError) as error:
        Translator._parse(content)
    assert error.value.error_kind == 'translation_duplicate_ids'


@pytest.mark.asyncio
async def test_acknowledged_partial_retry_requests_only_the_missing_ids(tmp_path, monkeypatch):
    requested = []

    def reply(request):
        instructions = json.loads(json.loads(request.content)['messages'][1]['content'].split('\n\n')[0])
        requested.append(set(instructions['required_output']))
        text = '{"R001":"first"}' if len(requested) == 1 else '{"R002":"second"}'
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':text}}]})

    model = transport(monkeypatch, reply)
    cache = CloudResultCache(tmp_path,tmp_path/'.requests')
    token, checkpoint = current_cloud_results.set(cache), budgets.checkpoint_requests.set(True)
    try:
        with pytest.raises(ProviderError) as error:
            await model.translate_regions([('R001','one'),('R002','two')],source_language='ja',target_language='zh',context={})
        assert error.value.error_kind == 'translation_missing_ids' and len(requested) == 1
        # The workflow's explicit acknowledgement is exercised by checkpoint
        # tests; here verify the provider never resends the successful ID.
        current_cloud_results.set(CloudResultCache(tmp_path,cache.directory,allow_invalid=True))
        assert await model.translate_regions([('R001','one'),('R002','two')],source_language='ja',target_language='zh',context={}) == {'R001':'first','R002':'second'}
        assert requested == [{'R001','R002'},{'R002'}]
    finally:
        current_cloud_results.reset(token)
        budgets.checkpoint_requests.reset(checkpoint)


def transport(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    return Translator({"base_url": "https://response.invalid/v1", "api_key": "do-not-store-key", "requests_per_minute": 6000,
                       "max_tokens": 1234, "retries": 2})


async def translate(provider):
    return await provider.translate_regions([("R001", "原文")], source_language="ja", target_language="zh", context={})


@pytest.mark.asyncio
@pytest.mark.parametrize("configured,expected", [(None, 2048), (8192, 8192)])
async def test_default_output_limit_is_2048_without_overriding_saved_limits(monkeypatch, configured, expected):
    def handler(request):
        assert json.loads(request.content)["max_tokens"] == expected
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"R001":"译文"}'}}]})

    provider = transport(monkeypatch, handler)
    if configured is None:
        provider.config.pop("max_tokens")
    else:
        provider.config["max_tokens"] = configured
    assert await translate(provider) == {"R001": "译文"}


@pytest.mark.asyncio
async def test_received_response_can_be_locally_reparsed_after_a_parser_fix_without_network(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        assert json.loads(request.content)["max_tokens"] == 1234
        return httpx.Response(200, json={"debug_headers": {"Authorization": "do-not-store-key"},
            "choices": [{"message": {"content": '{"R001":"saved response"}'}}]})

    provider = transport(monkeypatch, handler)
    cache = CloudResultCache(tmp_path, tmp_path / ".requests")
    token, checkpoint = current_cloud_results.set(cache), budgets.checkpoint_requests.set(True)
    parser = provider._parse
    monkeypatch.setattr(provider, "_parse", lambda content: (_ for _ in ()).throw(ProviderError("old parser failure")))
    try:
        with pytest.raises(ProviderError):
            await translate(provider)
        assert len(calls) == 1
        stored = "".join(path.read_text() for path in cache.directory.glob("*.json"))
        assert "saved response" in stored and "do-not-store-key" not in stored and "debug_headers" not in stored
        monkeypatch.setattr(provider, "_parse", parser)
        assert await translate(provider) == {"R001": "saved response"}
        assert len(calls) == 1
    finally:
        current_cloud_results.reset(token)
        budgets.checkpoint_requests.reset(checkpoint)


@pytest.mark.asyncio
async def test_invalid_received_response_is_not_reissued_without_acknowledgement(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": None if len(calls) == 1 else '{"R001":"new"}'}}]})

    provider = transport(monkeypatch, handler)
    cache = CloudResultCache(tmp_path, tmp_path / ".requests")
    token, checkpoint = current_cloud_results.set(cache), budgets.checkpoint_requests.set(True)
    try:
        for _ in range(2):
            with pytest.raises(ProviderError) as error:
                await translate(provider)
            assert error.value.error_kind == "translation_response_empty"
        assert calls == [1]
        current_cloud_results.set(CloudResultCache(tmp_path, cache.directory, allow_invalid=True))
        assert await translate(provider) == {"R001": "new"}
        assert len(calls) == 2 and len(list(cache.directory.glob("*.received"))) == 1
    finally:
        current_cloud_results.reset(token)
        budgets.checkpoint_requests.reset(checkpoint)


@pytest.mark.parametrize("text", ['{}', '{"R001":""}', '{"R001":" \\n　"}', '{"R001":"\\u200b\\ufeff"}', '{"wrong":"text"}'])
@pytest.mark.asyncio
async def test_acknowledged_semantically_invalid_response_is_replaced_not_replayed_forever(tmp_path, monkeypatch, text):
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": text if len(calls) == 1 else '{"R001":"new"}'}}]})
    provider = transport(monkeypatch, handler)
    cache = CloudResultCache(tmp_path, tmp_path / ".requests")
    token, checkpoint = current_cloud_results.set(cache), budgets.checkpoint_requests.set(True)
    try:
        for _ in range(2):
            with pytest.raises(ProviderError):
                await translate(provider)
        assert calls == [1]  # No implicit paid retry, even for an empty object.
        current_cloud_results.set(CloudResultCache(tmp_path, cache.directory, allow_invalid=True))
        assert await translate(provider) == {"R001": "new"}
        assert calls == [1, 1]
        assert len(list(cache.directory.glob("*.received"))) == 1
    finally:
        current_cloud_results.reset(token)
        budgets.checkpoint_requests.reset(checkpoint)


@pytest.mark.parametrize("text", ["", " \n　", "\u200b\ufeff", "\u200d", None])
@pytest.mark.asyncio
async def test_blank_source_is_not_sent_to_the_translation_endpoint(monkeypatch, text):
    calls = []
    provider = transport(monkeypatch, lambda request: calls.append(request))
    with pytest.raises(ProviderError) as error:
        await provider.translate_regions([("R001", text)], source_language="ja", target_language="zh", context={})
    assert error.value.error_kind == "translation_empty_source"
    assert not calls


@pytest.mark.asyncio
async def test_failed_chunk_drains_paid_sibling_and_stops_dispatching_more_chunks(tmp_path, monkeypatch):
    calls = []
    started = asyncio.Event()
    async def handler(request):
        instructions = json.loads(json.loads(request.content)["messages"][1]["content"].split("\n\n")[0])
        identifiers = set(instructions["required_output"])
        calls.append(identifiers)
        if len(calls) == 2:
            started.set()
        await asyncio.wait_for(started.wait(), timeout=2)
        if "R000" in identifiers:
            content = "{}"
        else:
            await asyncio.sleep(.03)  # First chunk fails while this request is in flight.
            content = json.dumps({key: "saved" for key in identifiers})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    provider = transport(monkeypatch, handler)
    provider.config.update(concurrency_mode="fixed", max_concurrency=2)
    cache = CloudResultCache(tmp_path, tmp_path / ".requests")
    token, checkpoint = current_cloud_results.set(cache), budgets.checkpoint_requests.set(True)
    try:
        with pytest.raises(ProviderError) as error:
            await provider.translate_regions([(f"R{index:03}", "source") for index in range(65)],
                source_language="ja", target_language="zh", context={})
        assert error.value.error_kind == "translation_missing_ids"
        assert len(calls) == 2 and all("R064" not in identifiers for identifiers in calls)
        # Replaying the successful, already paid second chunk needs no network.
        assert await provider.translate_regions([(f"R{index:03}", "source") for index in range(32, 64)],
            source_language="ja", target_language="zh", context={}) == {f"R{index:03}": "saved" for index in range(32, 64)}
        assert len(calls) == 2
    finally:
        current_cloud_results.reset(token)
        budgets.checkpoint_requests.reset(checkpoint)


@pytest.mark.asyncio
async def test_acknowledged_old_invalid_completed_cache_retains_valid_entries(tmp_path, monkeypatch):
    calls = []
    def handler(request):
        instructions = json.loads(json.loads(request.content)["messages"][1]["content"].split("\n\n")[0])
        calls.append(set(instructions["required_output"]))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"R002":"new"}'}}]})
    provider = transport(monkeypatch, handler)
    regions = [("R001", "one"), ("R002", "two")]
    cache = CloudResultCache(tmp_path, tmp_path / ".requests")
    key = fingerprint({"format": 1, "kind": "translation", "config": provider.config, "regions": regions,
                       "source_language": "ja", "target_language": "zh", "context": {}})
    cache.put(key, {"R001": "saved", "R002": "\u200b"})
    token, checkpoint = current_cloud_results.set(cache), budgets.checkpoint_requests.set(True)
    try:
        with pytest.raises(ProviderError):
            await provider.translate_regions(regions, source_language="ja", target_language="zh", context={})
        assert not calls
        current_cloud_results.set(CloudResultCache(tmp_path, cache.directory, allow_invalid=True))
        assert await provider.translate_regions(regions, source_language="ja", target_language="zh", context={}) == {"R001": "saved", "R002": "new"}
        assert calls == [{"R002"}] and len(list(cache.directory.glob("*.received"))) == 1
    finally:
        current_cloud_results.reset(token)
        budgets.checkpoint_requests.reset(checkpoint)
