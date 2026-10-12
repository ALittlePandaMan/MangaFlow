from __future__ import annotations

import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
from app.services import cloud_budget as budgets
from app.services.base import OCRResult, ProviderError
from app.services.ocr.concurrency import OCRJob, recognize_regions


@pytest.fixture(autouse=True)
def isolated_budgets():
    budgets._budgets.clear()
    yield
    budgets._budgets.clear()


def test_budget_starts_small_ramps_and_backs_off_without_extra_requests(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(budgets.time, "monotonic", lambda: clock[0])
    budget = budgets.RequestBudget()
    for _ in range(4):
        assert budget.try_acquire()
        clock[0] += 10
        budget.finish(10)
    assert budget.limit == 3
    # At three workers, a slower service yields no throughput gain.
    for _ in range(6):
        assert budget.try_acquire()
        clock[0] += 20
        budget.finish(20)
    assert budget.limit == 2
    assert budget.try_acquire()
    budget.finish(1, ProviderError("限流", retryable=True, status_code=429, retry_after=40))
    assert budget.limit == 1
    assert "退避 40s" in budget.status()
    assert not budget.try_acquire()
    clock[0] += 41
    assert budget.try_acquire()
    budget.finish(1)
    assert budget.limit == 1


def test_fixed_mode_and_pacing_obey_global_cooldown(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(budgets.time, "monotonic", lambda: clock[0])
    budget = budgets.RequestBudget()
    config = {"concurrency_mode": "fixed", "max_concurrency": 4, "requests_per_minute": 60}
    budget.configure(config)
    assert budget.try_acquire()
    assert not budget.try_acquire()
    clock[0] += 1
    assert budget.try_acquire()
    budget.finish(1, ProviderError("忙碌", retryable=True, status_code=503, retry_after=10))
    budget.finish(1)
    assert budget.limit == 4
    budget.configure({**config, "max_concurrency": 3})
    assert not budget.try_acquire()  # Changing configuration must not bypass Retry-After.
    clock[0] += 11
    assert budget.try_acquire()
    budget.finish(1)


def test_connection_shares_ocr_and_translation_budget_but_not_other_keys_or_models():
    config = {"base_url": "https://API.example/v1", "api_key": "synthetic-key", "model": "vision"}
    first = budgets.request_budget(config)
    assert first is budgets.request_budget({**config, "base_url": "https://api.example:443/v1/chat/completions"})
    assert first is not budgets.request_budget({**config, "api_key": "different"})
    assert first is not budgets.request_budget({**config, "model": "other"})
    assert "synthetic-key" not in repr(budgets._budgets)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 401, 429])
async def test_translation_observes_same_budget_and_releases_on_success_or_error(monkeypatch, status):
    from app.services.translation.providers import OpenAICompatibleTranslator
    config = {"base_url": "https://mock.example/v1", "model": "vision", "api_key": "synthetic", "retries": 0}
    budget = budgets.request_budget(config)
    budget.configure({**config, "concurrency_mode": "fixed", "max_concurrency": 3})
    calls = []
    def handler(request):
        assert budget.active == 1
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "3"}, json={"choices": [{"message": {"content": '{"R001":"你好"}'}}]})
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    provider = OpenAICompatibleTranslator(config)
    if status == 200:
        assert await provider.translate_regions([("R001", "こんにちは")], source_language="ja", target_language="zh", context={}) == {"R001": "你好"}
    else:
        with pytest.raises(ProviderError):
            await provider.translate_regions([("R001", "こんにちは")], source_language="ja", target_language="zh", context={})
    assert len(calls) == 1
    assert budget.active == 0
    if status == 429:
        assert budget.cooldown_until > time.monotonic()


@pytest.mark.parametrize("provider_name", ["cloud", "generic"])
def test_batch_attempt_exposes_rate_limit_without_hidden_retries(monkeypatch, tmp_path, provider_name):
    from app.services.ocr.cloud import CloudMultimodalOCRProvider
    from app.services.ocr.generic import GenericCloudOCRProvider
    from PIL import Image
    provider_type = CloudMultimodalOCRProvider if provider_name == "cloud" else GenericCloudOCRProvider
    path = tmp_path / "sample.png"
    Image.new("RGB", (100, 100), "white").save(path)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "15"})
    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    provider = provider_type({"base_url": "https://mock.example/v1", "model": "vision", "api_key": "synthetic", "retries": 5})
    with pytest.raises(ProviderError) as caught:
        provider.recognize_once(path, [10, 10, 30, 30], "vertical")
    assert len(calls) == 1
    assert caught.value.status_code == 429
    assert caught.value.retry_after == 15


@pytest.mark.asyncio
async def test_translation_can_be_cancelled_during_shared_cooldown(monkeypatch):
    from app.services.translation.providers import OpenAICompatibleTranslator
    config = {"base_url": "https://mock.example/v1", "model": "vision", "api_key": "synthetic"}
    budget = budgets.request_budget(config)
    budget.cooldown_until = time.monotonic() + 60
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: pytest.fail("Must not send during cooldown"))
    checks = 0
    def cancel():
        nonlocal checks
        checks += 1
        if checks >= 3:
            raise RuntimeError("cancelled")
    with pytest.raises(RuntimeError, match="cancelled"):
        await OpenAICompatibleTranslator(config).translate_regions([("R001", "text")], source_language="ja", target_language="zh", context={}, check_interruption=cancel)
    assert budget.active == 0


@pytest.mark.parametrize("config", [
    {"concurrency_mode": "unlimited"}, {"concurrency_mode": []}, {"max_concurrency": 0}, {"max_concurrency": 17},
    {"max_concurrency": True}, {"max_concurrency": 1.5}, {"requests_per_minute": 0},
    {"requests_per_minute": float("nan")}, {"requests_per_minute": "fast"},
])
def test_invalid_concurrency_rejected(config):
    with pytest.raises(ProviderError):
        budgets.validate_concurrency(config)


def test_retry_after_parses_seconds_and_http_date():
    assert budgets.retry_after_seconds("12") == 12
    assert budgets.retry_after_seconds("nan") is None
    assert budgets.retry_after_seconds("not a date") is None
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30))
    assert 28 <= budgets.retry_after_seconds(future) <= 30


class FakeOCR:
    def __init__(self, mode="fixed", maximum=3):
        self.config = {"base_url": "https://mock.example/ocr", "api_key": "test", "concurrency_mode": mode,
                       "max_concurrency": maximum, "requests_per_minute": 6000, "retries": 1}
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.calls = Counter()
        self.worker_threads = set()

    def recognize_once(self, source, bbox, orientation, padding):
        key = int(bbox[0])
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls[key] += 1
            self.worker_threads.add(threading.get_ident())
        try:
            time.sleep(.08 if key % 2 == 0 else .02)
            return OCRResult(str(key), 0, orientation)
        finally:
            with self.lock:
                self.active -= 1


def jobs(count=12):
    return [OCRJob(str(i), (i, 0, 20, 20), "vertical") for i in range(count)]


@pytest.mark.parametrize(("mode", "minimum", "maximum"), [("serial", 1, 1), ("fixed", 2, 3), ("adaptive", 2, 3)])
def test_bounded_parallel_results_keep_ids_and_callbacks_on_owner_thread(mode, minimum, maximum):
    provider = FakeOCR(mode)
    main_thread = threading.get_ident()
    statuses = []
    def check():
        assert threading.get_ident() == main_thread
    result = dict(recognize_regions(provider, Path("unused"), jobs(), 4, check, statuses.append))
    assert {key: value.text for key, value in result.items()} == {str(i): str(i) for i in range(12)}
    assert minimum <= provider.peak <= maximum
    assert provider.active == 0
    assert all(count == 1 for count in provider.calls.values())
    assert main_thread not in provider.worker_threads
    assert statuses


def test_only_failed_region_retries_and_no_nested_retry_loop():
    provider = FakeOCR()
    recognize = provider.recognize_once
    failed = False
    def once(*args):
        nonlocal failed
        result = recognize(*args)
        if result.text == "1" and not failed:
            failed = True
            raise ProviderError("malformed response", retryable=True)
        return result
    provider.recognize_once = once
    results = dict(recognize_regions(provider, Path("unused"), jobs(4), 4, lambda: None, lambda _: None))
    assert len(results) == 4
    assert provider.calls == {0: 1, 1: 2, 2: 1, 3: 1}


def test_fatal_auth_error_stops_dispatch_and_is_not_retried():
    provider = FakeOCR("serial")
    def unauthorized(*args):
        provider.calls[0] += 1
        raise ProviderError("HTTP 401", status_code=401)
    provider.recognize_once = unauthorized
    with pytest.raises(ProviderError, match="401"):
        list(recognize_regions(provider, Path("unused"), jobs(20), 4, lambda: None, lambda _: None))
    assert sum(provider.calls.values()) == 1
    assert budgets.request_budget(provider.config).active == 0


@pytest.mark.parametrize("interrupt", ["cancel", "pause"])
def test_interruption_stops_new_requests_and_drains_inflight(interrupt):
    provider = FakeOCR()
    def check():
        if provider.calls:
            raise RuntimeError(interrupt)
    with pytest.raises(RuntimeError, match=interrupt):
        list(recognize_regions(provider, Path("unused"), jobs(30), 4, check, lambda _: None))
    assert sum(provider.calls.values()) < 30
    assert provider.active == budgets.request_budget(provider.config).active == 0


def test_consumer_failure_also_drains_workers():
    from contextlib import closing
    provider = FakeOCR()
    with pytest.raises(RuntimeError, match="database"):
        with closing(recognize_regions(provider, Path("unused"), jobs(30), 4, lambda: None, lambda _: None)) as results:
            next(results)
            raise RuntimeError("database failure")
    assert provider.active == budgets.request_budget(provider.config).active == 0


@pytest.mark.parametrize("change", [{"source_text": "manual edit"}, {"locked": True}, {"bbox": [90, 100, 70, 90]}])
def test_pipeline_discards_late_result_after_manual_edit(client, monkeypatch, change):
    from app.services.ocr.cloud import CloudMultimodalOCRProvider
    from test_pipeline import manga_page, wait_for_task
    started = threading.Event()
    release = threading.Event()
    def recognize(*args):
        started.set()
        assert release.wait(5)
        return OCRResult("stale cloud text", 0, "vertical")
    monkeypatch.setattr(CloudMultimodalOCRProvider, "recognize_once", recognize)
    config = client.post("/api/models/config", json={"kind": "ocr", "provider": "cloud-multimodal", "name": "stale-test", "enabled": True, "is_default": False,
        "api_key": "synthetic", "config": {"base_url": "https://mock.example/v1", "model": "test"}}).json()
    project = client.post("/api/projects", json={"name": "stale result test"}).json()
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("page.png", manga_page(), "image/png")}).json()[0]
    region = client.post(f"/api/images/{page['id']}/regions", json={"bbox": [100, 100, 80, 120]}).json()
    try:
        task = client.post(f"/api/images/{page['id']}/process", json={"start_stage": "ocr", "end_stage": "ocr", "provider": "stale-test", "force": True}).json()
        assert started.wait(5)
        assert client.patch(f"/api/regions/{region['id']}", json=change).status_code == 200
    finally:
        release.set()
    assert wait_for_task(client, task["id"])["status"] == "COMPLETED"
    updated = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert updated["source_text"] == change.get("source_text", "")
    for key, value in change.items():
        assert updated[key] == value
    client.delete(f"/api/models/config/{config['id']}")
