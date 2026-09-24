"""Collecting the load must not stop the process it measures.

`psutil.cpu_percent(interval=1)` does not measure for a second, it sleeps for
one, and a coroutine that sleeps like that takes the event loop with it. A pass
asked four times over, so every collection interval the whole process stood
still for about four seconds: requests unanswered, sockets unserved, and in an
application with a game in it every room's step loop stopped -- found as
four-second gaps in rooms that know nothing of one another.

So the load is read without an interval (psutil compares against its own
previous reading) and read once a pass, and the test that keeps it that way is
the one that would have caught it: a clock ticking beside the collection.

    python3 -m pytest keepup/tests/metrics_loop_tests.py -v
"""

import asyncio
import time

import pytest

from keepup import metrics as M

#: A tick of the clock beside the pass, and how late a tick may be before the
#: page in somebody's browser would notice. Ten ticks fit in one blocking read.
TICK_SECONDS = 0.02
LATE_LIMIT_SECONDS = 0.15


@pytest.fixture
def collector(monkeypatch):
    made = M.SystemMetricsCollector()
    monkeypatch.setattr(made, "save_metrics_to_db", lambda metrics: None)
    return made


async def worst_tick_lateness(during, ticks: int = 40) -> float:
    """How late the latest tick of a steady clock was while `during` ran."""
    worst = 0.0

    async def clock():
        nonlocal worst
        expected = time.monotonic()
        for _ in range(ticks):
            expected += TICK_SECONDS
            await asyncio.sleep(max(0.0, expected - time.monotonic()))
            worst = max(worst, time.monotonic() - expected)

    ticking = asyncio.create_task(clock())
    await during()
    ticking.cancel()
    try:
        await ticking
    except asyncio.CancelledError:
        pass
    return worst


def test_a_pass_of_collection_does_not_stop_the_event_loop(collector):
    """Scenario: the load is collected while the application is serving."""

    async def pass_once():
        for _ in range(3):
            metrics = await collector.collect_metrics()
            collector.save_metrics_to_db(metrics)
            collector.update_prometheus_metrics()
            collector.forget_cpu_load()
            await asyncio.sleep(0)

    late = asyncio.run(worst_tick_lateness(pass_once))
    assert late < LATE_LIMIT_SECONDS, f"the loop stood still for {late:.2f} s while metrics were collected"


def test_the_load_is_read_without_an_interval(collector, monkeypatch):
    """Scenario: nothing in a pass asks psutil to sleep."""
    asked = []

    def reading(interval=None, percpu=False):
        asked.append(interval)
        return [1.0, 2.0] if percpu else 1.5

    monkeypatch.setattr(M.psutil, "cpu_percent", reading)
    asyncio.run(collector.collect_metrics())
    collector.update_prometheus_metrics()
    assert asked, "the pass read no load at all"
    assert all(not interval for interval in asked), f"a reading asked psutil to sleep: {asked}"


def test_the_load_is_read_once_a_pass(collector, monkeypatch):
    """Two readings a microsecond apart compare against each other and measure noise."""
    readings = []

    def reading(interval=None, percpu=False):
        readings.append(percpu)
        return [3.0, 5.0] if percpu else 4.0

    monkeypatch.setattr(M.psutil, "cpu_percent", reading)
    metrics = asyncio.run(collector.collect_metrics())
    collector.update_prometheus_metrics()
    assert readings.count(False) == 1 and readings.count(True) == 1
    assert metrics["cpu.percent.total"] == 4.0
    assert metrics["cpu.percent.avg_per_core"] == 4.0
    # The next pass reads again, or the panel would show one number for ever.
    collector.forget_cpu_load()
    asyncio.run(collector.collect_metrics())
    assert readings.count(False) == 2
