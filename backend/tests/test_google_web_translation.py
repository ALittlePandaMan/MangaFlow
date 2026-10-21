from urllib.parse import parse_qs

import httpx
import pytest
from app.services.base import ProviderError
from app.services.cloud_results import CloudResultCache, current_cloud_results
from app.services.translation import google_web
from app.services.translation.google_web import GoogleWebTranslator, google_language, text_chunks


class Budget:
    def __init__(self):
        self.policy, self.finished = None, []

    def configure(self, config):
        self.policy = config

    def try_acquire(self):
        return True

    def finish(self, elapsed, error, **kwargs):
        self.finished.append(error)


@pytest.fixture
def transport(monkeypatch):
    budget, requests = Budget(), []
    client_type = httpx.AsyncClient
    def handler(request):
        return httpx.Response(200, json={"sentences": [{"trans": "你好"}]})

    def install(callback=None):
        def respond(request):
            requests.append(request)
            return (callback or handler)(request)
        monkeypatch.setattr(google_web.httpx, "AsyncClient", lambda **kwargs: client_type(
            transport=httpx.MockTransport(respond), **kwargs))
        monkeypatch.setattr(google_web, "request_budget", lambda config: budget)
        return requests, budget
    return install


async def translate(provider=None, regions=None, **kwargs):
    return await (provider or GoogleWebTranslator()).translate_regions(
        regions if regions is not None else [("R001", "こんにちは")],
        source_language="ja", target_language="zh-CN", context={"private": "never upload", "_page_image_data_url": "private-image"},
        **kwargs)


@pytest.mark.asyncio
async def test_keyless_translation_sends_only_text_and_preserves_ids(transport):
    requests, budget = transport()
    provider = GoogleWebTranslator({"api_key": "old-secret", "model": "old-model", "base_url": "https://untrusted.example",
                                    "max_concurrency": 99, "include_page_image": True})
    assert await translate(provider, [("R002", "こんにちは"), ("R001", "ありがとう")]) == {"R002": "你好", "R001": "你好"}
    assert len(requests) == 2
    for request in requests:
        assert request.method == "POST"
        assert request.url.host == "translate.googleapis.com"
        assert "q" not in request.url.params
        assert "authorization" not in request.headers and "x-api-key" not in request.headers
        assert set(parse_qs(request.content.decode())) == {"q"}
        assert all(secret not in str(request.url) + request.content.decode() for secret in ("old-secret", "old-model", "never upload", "private-image"))
    assert budget.policy["concurrency_mode"] == "serial"
    assert budget.policy["max_concurrency"] == 1
    assert budget.policy["requests_per_minute"] == 30
    assert budget.finished == [None, None]


@pytest.mark.asyncio
@pytest.mark.parametrize("regions", [[("R001", " ")], [("R001", "a"), ("R001", "b")]])
async def test_invalid_regions_never_send_requests(transport, regions):
    requests, _ = transport()
    with pytest.raises(ProviderError):
        await translate(regions=regions)
    assert requests == []


@pytest.mark.asyncio
async def test_empty_region_list_never_sends_requests(transport):
    requests, _ = transport()
    assert await translate(regions=[]) == {}
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"sentences": []}, {"sentences": [{"trans": " "}]},
                                  {"sentences": [{"trans": 12}]}, {"sentences": "bad"}, {"sentences": [None]}])
async def test_invalid_or_empty_results_are_errors_not_fake_translations(transport, body):
    requests, budget = transport(lambda request: httpx.Response(200, json=body))
    with pytest.raises(ProviderError) as caught:
        await translate()
    assert caught.value.error_kind in {"translation_response_invalid", "translation_empty_values"}
    assert len(requests) == 1
    assert budget.finished == [caught.value]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_unavailable_or_rate_limited_services_stop_without_bypasses_or_retry_loops(transport, status):
    requests, budget = transport(lambda request: httpx.Response(status, text="challenge", headers={"retry-after": "90"}))
    with pytest.raises(ProviderError) as caught:
        await translate()
    assert caught.value.status_code == status
    assert not caught.value.retryable
    assert len(requests) == 1
    if status == 429:
        assert caught.value.retry_after == 90
    assert budget.finished == [caught.value]


@pytest.mark.asyncio
async def test_non_json_and_timeout_errors_do_not_expose_source_text(transport):
    requests, _ = transport(lambda request: httpx.Response(200, text="<html>captcha</html>"))
    with pytest.raises(ProviderError, match="无效响应"):
        await translate()
    assert len(requests) == 1
    def timeout(request):
        raise httpx.ReadTimeout("private request details", request=request)
    transport(timeout)
    with pytest.raises(ProviderError) as caught:
        await translate()
    assert caught.value.error_kind == "translation_transport_error"
    assert "private request details" not in str(caught.value)


@pytest.mark.asyncio
async def test_successful_regions_are_reused_after_a_later_failure(transport, tmp_path):
    count = 0
    def response(request):
        nonlocal count
        count += 1
        return httpx.Response(503) if count == 2 else httpx.Response(200, json={"sentences": [{"trans": "有效译文"}]})
    requests, _ = transport(response)
    token = current_cloud_results.set(CloudResultCache(tmp_path, tmp_path / "results"))
    try:
        with pytest.raises(ProviderError):
            await translate(regions=[("R1", "one"), ("R2", "two")])
        assert await translate(regions=[("R1", "one"), ("R2", "two")]) == {"R1": "有效译文", "R2": "有效译文"}
        assert [parse_qs(request.content.decode())["q"][0] for request in requests] == ["one", "two", "two"]
    finally:
        current_cloud_results.reset(token)


@pytest.mark.asyncio
async def test_cancellation_stops_further_regions_but_keeps_the_received_result(transport, tmp_path):
    requests, _ = transport()
    token = current_cloud_results.set(CloudResultCache(tmp_path, tmp_path / "results"))
    def stop_after_first():
        if requests:
            raise RuntimeError("cancelled")
    try:
        with pytest.raises(RuntimeError, match="cancelled"):
            await translate(regions=[("R1", "one"), ("R2", "two")], check_interruption=stop_after_first)
        assert len(requests) == 1
        assert await translate(regions=[("R1", "one"), ("R2", "two")]) == {"R1": "你好", "R2": "你好"}
        assert len(requests) == 2
    finally:
        current_cloud_results.reset(token)


def test_chunks_keep_all_original_characters_and_language_aliases():
    text = "一段话。" * 2100 + " end"
    chunks = text_chunks(text)
    assert "".join(chunks) == text
    assert all(0 < len(chunk) <= 4000 for chunk in chunks)
    assert google_language("zh_Hans") == "zh-CN"
    assert google_language("zh-Hant") == "zh-TW"
    assert google_language("ja-JP") == "ja"
    with pytest.raises(ProviderError):
        google_language("auto", target=True)


@pytest.mark.parametrize("timeout", [True, "30", float("nan"), 0, 121])
def test_timeout_validation(timeout):
    with pytest.raises(ProviderError):
        GoogleWebTranslator({"timeout": timeout}).load()
