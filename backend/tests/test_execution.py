from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from app.services.execution import AdaptiveLimiter, ExecutionPolicy, ExecutionRuntime, MiB


class Probe:
    def __init__(self, **patch):
        self.value = {"known": True, "available_bytes": 32 * 1024 * MiB, "cpu_percent": 10, "cpu_cores": 8, **patch}

    def sample(self):
        return self.value


def test_limiter_resizes_without_interrupting_active_workers():
    limiter = AdaptiveLimiter("cpu", 4, "fixed", MiB, Probe())
    for _ in range(4):
        assert limiter.try_acquire()
    limiter.configure(1, "serial", MiB)
    assert not limiter.try_acquire()
    for _ in range(3):
        limiter.release(1)
        assert not limiter.try_acquire()
    limiter.release(1)
    assert limiter.try_acquire()
    limiter.release(1)
    assert limiter.active == 0


def test_auto_ramps_and_backs_off_without_probe_requests(monkeypatch):
    clock = [100.]
    monkeypatch.setattr(time,"monotonic",lambda: clock[0])
    limiter = AdaptiveLimiter("cpu", 4, "auto", MiB, Probe())
    for _ in range(4):
        assert limiter.try_acquire()
        assert limiter.try_acquire()
        assert not limiter.try_acquire()
        clock[0] += 8
        limiter.release(8)
        limiter.release(8)
    assert limiter.limit == 3
    for _ in range(3):
        for _ in range(3):
            assert limiter.try_acquire()
        assert not limiter.try_acquire()
        clock[0] += 20
        for _ in range(3):
            limiter.release(20)
    assert limiter.limit == 2
    limiter.probe.value["available_bytes"] = 0
    assert limiter.try_acquire()
    assert not limiter.try_acquire()
    assert limiter.limit == 1
    limiter.release(1)


def test_nonresource_failures_do_not_reduce_local_capacity():
    limiter = AdaptiveLimiter("pages",4,"auto",MiB,Probe())
    assert limiter.try_acquire()
    limiter.release(1,failed=True)
    assert limiter.limit == 2  # A cloud/validation failure is not CPU pressure.
    assert limiter.try_acquire()
    limiter.release(1,failed=True,pressure=True)
    assert limiter.limit == 1


def test_failed_concurrency_probe_rolls_back_to_the_last_verified_limit(monkeypatch):
    clock = [100.]
    monkeypatch.setattr(time,"monotonic",lambda: clock[0])
    limiter = AdaptiveLimiter("pages",4,"auto",MiB,Probe())
    for _ in range(4):
        assert limiter.try_acquire() and limiter.try_acquire()
        assert not limiter.try_acquire()
        clock[0] += 8
        limiter.release(8)
        limiter.release(8)
    assert limiter.limit == 3
    assert limiter.try_acquire()
    limiter.release(1,failed=True)
    assert limiter.limit == 2


def test_unknown_resources_and_serial_remain_bounded():
    limiter = AdaptiveLimiter("pages", 4, "fixed", MiB, Probe(known=False))
    assert limiter.try_acquire()
    assert not limiter.try_acquire()
    limiter.release(1)


def test_inference_diagnostics_do_not_wait_for_model_or_expose_configs():
    from types import SimpleNamespace

    runtime = ExecutionRuntime()
    locked, release = threading.Event(), threading.Event()

    def occupy_model():
        with runtime.model_slot():
            locked.set()
            release.wait(5)

    try:
        runtime.report_provider(SimpleNamespace(
            capabilities=SimpleNamespace(name="manga-ocr", provider_type="ocr"),
            state="ready", _device="cpu", _fallback_reason="accelerator_inference_failed",
            config={"device":"cuda", "api_key":"must-not-appear"}))
        with ThreadPoolExecutor(2) as pool:
            inference = pool.submit(occupy_model)
            assert locked.wait(3)
            status = pool.submit(runtime.snapshot).result(2)
            assert status["inference"] == [{"kind":"ocr", "name":"manga-ocr", "backend":"torch",
                "device":"cpu", "state":"ready", "fallback_reason":"accelerator_inference_failed"}]
            status["inference"][0]["device"] = "tampered"
            assert runtime.snapshot()["inference"][0]["device"] == "cpu"
            assert "must-not-appear" not in str(status)
            release.set()
            inference.result(2)
    finally:
        release.set()
        runtime.cpu_pool.shutdown()


def test_provider_diagnostics_use_actual_device_and_clear_unloaded_device():
    from types import SimpleNamespace

    runtime = ExecutionRuntime()
    provider = SimpleNamespace(capabilities=SimpleNamespace(name="lama",provider_type="inpainting"),
                               state="ready",_device="cuda:0",config={"device":"auto"})
    try:
        runtime.report_provider(provider)
        assert runtime.snapshot()["inference"][0]["device"] == "cuda:0"
        provider._device = "cpu"
        runtime.report_provider(provider)
        assert runtime.snapshot()["inference"][0]["fallback_reason"] == "accelerator_unavailable"
        provider.state = "unloaded"
        runtime.report_provider(provider)
        assert runtime.snapshot()["inference"][0]["device"] is None
    finally:
        runtime.cpu_pool.shutdown()


def test_old_policy_loads_gpu_defaults_and_unverified_parallelism_stays_single():
    policy = ExecutionPolicy.model_validate({"mode":"fixed","max_pages":4,"cpu_workers":3,"memory_reserve_mb":1024})
    assert policy.gpu_max_concurrency==1 and policy.gpu_memory_reserve_mb==2048
    runtime = ExecutionRuntime()
    try:
        runtime.update(policy.model_copy(update={"gpu_max_concurrency":2}),persist=False)
        assert runtime.snapshot()["models"]["limit"]==1
        assert runtime.snapshot()["models"]["maximum"]==2
    finally:
        runtime.cpu_pool.shutdown()


def test_cpu_pool_is_parallel_and_returns_results_by_original_index(tmp_path, monkeypatch):
    runtime = ExecutionRuntime()
    runtime.probe = Probe()
    runtime.cpu.probe = runtime.probe
    runtime.update(ExecutionPolicy(mode="fixed", cpu_workers=2), persist=False)
    barrier = threading.Barrier(2)

    def work(value):
        barrier.wait(timeout=3)
        return value, threading.current_thread().name

    try:
        with closing(
            runtime.map_cpu([lambda: work(10), lambda: work(20)], check=lambda: None, estimate=MiB)
        ) as results:
            rows = dict(results)
        assert [rows[i][0] for i in range(2)] == [10, 20]
        assert all(row[1].startswith("mangaflow-cpu") for row in rows.values())
        assert runtime.cpu.peak == 2 and runtime.cpu.active == 0
    finally:
        runtime.cpu_pool.shutdown()


def test_cancel_drains_workers_before_returning():
    runtime = ExecutionRuntime()
    runtime.cpu.probe = Probe()
    runtime.update(ExecutionPolicy(mode="fixed", cpu_workers=2), persist=False)
    started, release, drained = threading.Event(), threading.Event(), threading.Event()
    cancelled = False

    def work():
        started.set()
        release.wait(3)
        drained.set()

    def check():
        if cancelled:
            raise RuntimeError("cancelled")

    def run():
        with closing(runtime.map_cpu([work], check=check, estimate=MiB)) as results:
            return list(results)

    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(run)
            assert started.wait(3)
            cancelled = True
            time.sleep(0.1)
            assert not future.done()
            release.set()
            with pytest.raises(RuntimeError, match="cancelled"):
                future.result(3)
        assert drained.is_set() and runtime.cpu.active == 0
    finally:
        release.set()
        runtime.cpu_pool.shutdown()


def test_policy_endpoint_validates_and_persists(client):
    from app.services.execution import execution_runtime

    runtime = execution_runtime()
    original = runtime.policy
    try:
        assert client.put("/api/execution", json={"mode": "fixed", "cpu_workers": 0}).status_code == 422
        result = client.put(
            "/api/execution", json={"mode": "serial", "max_pages": 2, "cpu_workers": 3, "memory_reserve_mb": 512}
        )
        assert result.status_code == 200
        assert result.json()["pages"]["limit"] == result.json()["cpu"]["limit"] == 1
        reloaded = ExecutionRuntime()
        assert reloaded.policy.mode == "serial"
        reloaded.cpu_pool.shutdown()
    finally:
        runtime.update(original)


def test_pages_overlap_but_same_page_is_serial_and_history_stays_reversible(client, monkeypatch):
    from app.pipeline.processor import PipelineProcessor
    from app.services.base import OCRResult
    from app.services.execution import execution_runtime
    from test_editor_history import apply as apply_history
    from test_pipeline import solid_page, wait_for_task

    runtime = execution_runtime()
    policy = runtime.policy
    runtime.update(ExecutionPolicy(mode="fixed", max_pages=2, cpu_workers=2), persist=False)
    first_started, other_started, release = threading.Event(), threading.Event(), threading.Event()
    calls = []

    class OCR:
        def ensure_loaded(self):
            pass

        def recognize(self, path, box, orientation, padding):
            calls.append(path)
            if len(calls) == 1:
                first_started.set()
                assert release.wait(5)
            else:
                other_started.set()
            return OCRResult(f"recognized-{len(calls)}", 0.99, orientation)

    original = PipelineProcessor._provider
    monkeypatch.setattr(
        PipelineProcessor,
        "_provider",
        lambda self, kind, name: (OCR(), "test") if kind == "ocr" else original(self, kind, name),
    )
    project = client.post("/api/projects", json={"name": "concurrent-history"}).json()
    pages = client.post(
        f"/api/projects/{project['id']}/images",
        files=[("files", (f"{i}.png", solid_page(), "image/png")) for i in range(2)],
    ).json()
    for page in pages:
        client.post(f"/api/images/{page['id']}/regions", json={"bbox": [10, 10, 30, 50]})
    payload = {"start_stage": "ocr", "end_stage": "ocr", "force": True}
    try:
        first = client.post(f"/api/images/{pages[0]['id']}/process", json=payload).json()
        assert first_started.wait(3)
        same = client.post(f"/api/images/{pages[0]['id']}/process", json=payload).json()
        other = client.post(f"/api/images/{pages[1]['id']}/process", json=payload).json()
        assert other_started.wait(3), "Other pages must not wait for this page lock"
        assert client.get(f"/api/tasks/{same['id']}").json()["status"] == "QUEUED"
        assert len(calls) == 2
        release.set()
        for task in (first, same, other):
            assert wait_for_task(client, task["id"])["status"] == "COMPLETED"
        before = {p["id"]: client.get(f"/api/images/{p['id']}/regions").json()[0]["source_text"] for p in pages}
        for _ in range(3):
            apply_history(client, project["id"])
        assert all(client.get(f"/api/images/{p['id']}/regions").json()[0]["source_text"] == "" for p in pages)
        for _ in range(3):
            apply_history(client, project["id"], "redo")
        assert {p["id"]: client.get(f"/api/images/{p['id']}/regions").json()[0]["source_text"] for p in pages} == before
    finally:
        release.set()
        runtime.update(policy, persist=False)


def test_shared_local_model_is_not_entered_concurrently():
    from app.services.base import OCRProvider, OCRResult, ProviderCapabilities

    class LocalOCR(OCRProvider):
        capabilities = ProviderCapabilities(name="manga-ocr", provider_type="ocr", description="test", devices=["cpu"])
        active = 0
        peak = 0

        def recognize(self, *args):
            self.active += 1
            self.peak = max(self.peak, self.active)
            time.sleep(0.03)
            self.active -= 1
            return OCRResult("text", 1, "vertical")

    model = LocalOCR()
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(model.recognize, range(6)))
    assert model.peak == 1


def test_rfdetr_cpu_postprocessing_does_not_hold_gpu_slot(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from app.services.base import OCRProvider, OCRResult, ProviderCapabilities
    from app.services.detection import layout
    from app.services.execution import execution_runtime
    from PIL import Image

    path = tmp_path / "page.png"
    Image.new("RGB", (80, 100), "white").save(path)
    runtime = execution_runtime()
    detector = layout.RFDetrTextDetector({"device": "cpu"})
    detector._text_classifier.ensure_loaded = Mock()

    def predict(*args, **kwargs):
        assert runtime.model_active == 1
        return 80, 100, []

    detector._text_classifier.detect_layout = predict
    entered = threading.Event()

    class OCR(OCRProvider):
        capabilities = ProviderCapabilities("manga-ocr", "ocr", "test")

        def recognize(self, *args):
            entered.set()
            return OCRResult("text", .8, "vertical")

    def group(*args, **kwargs):
        assert runtime.model_active == 0
        thread = threading.Thread(target=OCR().recognize)
        thread.start()
        try:
            assert entered.wait(2), "CPU geometry must not block another model"
        finally:
            thread.join(3)
        return []

    monkeypatch.setattr(layout, "build_layout_regions", group)
    assert detector.detect(path) == []
    detector.unload()


def test_shared_local_microbatch_model_is_not_entered_concurrently():
    from app.services.base import OCRProvider, ProviderCapabilities

    class LocalOCR(OCRProvider):
        capabilities = ProviderCapabilities("manga-ocr", "ocr", "test")
        active = 0
        peak = 0

        def recognize(self, *args):
            return None

        def recognize_batch(self, *args):
            self.active += 1
            self.peak = max(self.peak, self.active)
            time.sleep(.02)
            self.active -= 1

    model = LocalOCR()
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(model.recognize_batch, range(6)))
    assert model.peak == 1


def test_colour_clustering_is_identical_across_worker_order():
    import numpy as np
    from app.services.inpainting.masking import _dominant_colors

    generator = np.random.default_rng(42)
    samples = generator.uniform(0,255,(8,1000,3)).astype(np.float32)
    expected = [_dominant_colors(row) for row in samples]
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(_dominant_colors,list(reversed(samples))*3))
    for index,(centres,counts) in enumerate(results):
        original = expected[7-index%8]
        assert np.array_equal(centres,original[0])
        assert np.array_equal(counts,original[1])
