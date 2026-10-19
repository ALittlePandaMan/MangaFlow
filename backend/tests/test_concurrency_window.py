import time

import pytest
from app.services.concurrency_window import ConcurrencyWindow


@pytest.fixture
def clock(monkeypatch):
    clock = [100.]
    monkeypatch.setattr(time,"monotonic",lambda: clock[0])
    return clock


def sample(window,clock,count,seconds,elapsed,limit,kind="mask",maximum=4):
    for _ in range(count):
        clock[0] += seconds
        limit,_ = window.observe(elapsed,1,kind,limit,maximum)
    return limit


def test_both_thirty_seconds_and_eight_samples_and_queued_demand_are_required(clock):
    window = ConcurrencyWindow()
    window.blocked()
    assert sample(window,clock,7,5,5,2) == 2
    assert sample(window,clock,1,5,5,2) == 3
    window.reset()
    window.blocked()
    assert sample(window,clock,20,.1,.1,2) == 2
    window.reset()
    assert sample(window,clock,8,5,5,2) == 2


@pytest.mark.parametrize("pace,latency,expected",[(4.5,4.5,3),(4.6,4.6,2),(4,6.1,2),(4,6,3)])
def test_probe_requires_ten_percent_gain_and_at_most_twenty_percent_p95_growth(clock,pace,latency,expected):
    window = ConcurrencyWindow()
    window.blocked()
    assert sample(window,clock,8,5,5,2) == 3
    # Align first completion so every sample belongs to the new generation.
    clock[0] += latency
    limit,_ = window.observe(latency,1,"mask",3,4)
    assert sample(window,clock,7,pace,latency,limit) == expected
    assert window.after > clock[0]
    # A short workload immediately following a probe cannot cause another rise.
    window.blocked()
    assert sample(window,clock,8,1,1,expected) == expected


def test_other_workloads_cannot_validate_or_replace_a_probe(clock):
    window = ConcurrencyWindow()
    window.blocked()
    assert sample(window,clock,8,5,5,2,"mask") == 3
    assert sample(window,clock,8,5,5,3,"rendering") == 3
    assert window.baseline[0] == "mask"


def test_completions_from_before_reconfiguration_are_ignored(clock):
    window = ConcurrencyWindow()
    window.blocked()
    assert sample(window,clock,8,5,50,2) == 2
    assert not window.windows
