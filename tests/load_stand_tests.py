"""The load stand is a stand, and its report adds up (keepup-53).

The stand itself runs in Docker and is not part of a test run; what is checked
here is what would silently spoil its numbers: the percentiles and the per-second
rates it reports, and the limits that keep it from starving the machine it runs
on.

    python3 -m pytest keepup/tests/load_stand_tests.py -v
"""

import importlib.util
from pathlib import Path

import yaml

LOAD = Path(__file__).resolve().parent / "load"


def driver():
    spec = importlib.util.spec_from_file_location("drive", LOAD / "drive.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_percentiles_are_read_off_the_sorted_values():
    drive = driver()
    values = [float(v) for v in range(1, 101)]
    assert drive.percentile(values, 0.50) == 51.0
    assert drive.percentile(values, 0.95) == 95.0
    assert drive.percentile([], 0.5) is None


def test_the_report_counts_rates_over_the_measured_time():
    drive = driver()
    results = {"me": {"latencies": [0.01] * 300, "errors": 2}}
    report = drive.summarise(results, {}, duration=30, users=10, replicas=2, failures=[])
    assert report["per_second"] == 10.0
    assert report["operations"]["me"] == {"count": 300, "errors": 2, "per_second": 10.0,
                                          "p50_ms": 10.0, "p95_ms": 10.0, "p99_ms": 10.0,
                                          "mean_ms": 10.0}


def test_the_mix_is_what_a_panel_asks_for():
    names = {name for name, *_ in driver().MIX}
    assert names == {"me", "sections", "events", "health", "write_event"}


def test_every_container_of_the_stand_has_a_memory_limit():
    """An uncapped stand is what once drove the machine it ran on into swap."""
    compose = yaml.safe_load((LOAD / "compose.yaml").read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        assert service.get("mem_limit"), f"{name} has no memory limit"


def test_the_stand_s_credentials_come_from_the_environment():
    """A password written into the repository is a password everyone has
    (keepup-90): every credential of the stand is a required variable."""
    compose = yaml.safe_load((LOAD / "compose.yaml").read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        for key, value in (service.get("environment") or {}).items():
            if any(word in key for word in ("PASSWORD", "SECRET")):
                assert str(value).startswith("${") and ":?" in str(value), (
                    f"{name}.{key} is written into the file")
