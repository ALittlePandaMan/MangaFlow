from unittest.mock import Mock

import pytest
from app.services.execution import ExecutionRuntime
from app.services.gpu_metrics import GpuProbe, parse_gpu_csv


def test_gpu_metrics_validate_and_handle_unsupported_utilization():
    device = parse_gpu_csv('0, Tesla V100, 16384, 1024, N/A')[0]
    assert device['free_bytes'] == 15360 * 1024**2
    assert device['utilization_percent'] is None
    for invalid in ('bad', '0,x,10,20,0', '0,x,N/A,20,0'):
        with pytest.raises(ValueError):
            parse_gpu_csv(invalid)


def test_gpu_probe_is_bounded_cached_and_fail_open(monkeypatch):
    monkeypatch.setattr('app.services.gpu_metrics.shutil.which', lambda _: '/nvidia-smi')
    run=Mock(return_value=Mock(stdout='0,V100,16000,1000,10'))
    monkeypatch.setattr('app.services.gpu_metrics.subprocess.run',run)
    probe=GpuProbe()
    assert probe.sample()['known']
    probe.sample()
    assert run.call_count == 1
    assert run.call_args.kwargs['timeout'] == 1.5
    monkeypatch.setattr('app.services.gpu_metrics.shutil.which', lambda _: None)
    assert GpuProbe().sample()['known'] is False


def test_stage_and_model_metrics_do_not_enable_unsafe_inference(monkeypatch):
    runtime=ExecutionRuntime()
    monkeypatch.setattr(runtime.gpu_probe, 'sample', lambda: {'known':False,'devices':[]})
    runtime.record_stage('a','ocr',1)
    runtime.record_stage('b','ocr',3)
    with runtime.model_slot(), runtime.model_slot():
        pass
    status=runtime.snapshot()
    assert status['models']['limit'] == 1
    assert status['models']['calls'] == 1  # Nested locks must not double-count.
    assert status['stage_timings'] == [{'stage':'ocr','samples':2,'mean_seconds':2.0,'p95_seconds':3}]
    runtime.cpu_pool.shutdown()
