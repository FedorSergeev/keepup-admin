"""How long metric snapshots live -- against a real database.

Real (SQLite built from the production DDL), because what has to be checked
here is exactly what a database does: the boundaries in time, the batches of
deletions, and what is left afterwards. Both halves are checked -- what the
sweep is for (the table stops growing) and what makes it dangerous (the
history the panel shows, and another module's rows in the same table, have to
survive it).

    python3 -m pytest keepup/tests/metrics_retention_tests.py -v
"""

import asyncio
import sqlite3
from datetime import datetime, timedelta

import pytest

from keepup import metrics, metrics_retention as retention, schema
from keepup.db import DatabaseManagerV2, db_config

NOW = datetime(2026, 9, 18, 12, 0, 0)
#: The collector's names, taken from its own list: a test must not keep a
#: second copy of them.
CPU = "cpu.percent.total"
RAM = "memory.percent"


@pytest.fixture
def fresh_database(tmp_path, monkeypatch):
    """A database of its own: the shared engine is cached on the class and
    would outlive the test."""
    path = tmp_path / "metrics.db"
    DatabaseManagerV2.dispose()
    monkeypatch.setattr(db_config, "db_path", str(path), raising=False)
    monkeypatch.setattr(db_config, "db_type", "sqlite", raising=False)
    schema.init_db()
    yield path
    DatabaseManagerV2.dispose()


@pytest.fixture
def policy():
    return retention.RetentionPolicy(detailed_hours=48, retention_days=30,
                                     thinned_interval_seconds=60,
                                     sweep_interval_seconds=3600)


def write(name, moment, instance="main", value=1.0):
    DatabaseManagerV2.execute_commit(
        "INSERT INTO system_metrics (metric_name, metric_value, timestamp, app_instance) "
        "VALUES (:name, :value, :moment, :instance)",
        {"name": name, "value": value, "moment": moment, "instance": instance})


def series(name, first, count, step_seconds=15, instance="main"):
    """A run of snapshots, written the way the collector writes them."""
    for index in range(count):
        write(name, first + timedelta(seconds=index * step_seconds), instance=instance)


def rows():
    return DatabaseManagerV2.execute(
        "SELECT id, metric_name, app_instance, timestamp FROM system_metrics "
        "ORDER BY timestamp, id")


def count(**where):
    clause = " AND ".join(f"{key} = :{key}" for key in where)
    query = "SELECT COUNT(*) AS n FROM system_metrics"
    if clause:
        query += f" WHERE {clause}"
    return int(DatabaseManagerV2.execute_one(query, where)["n"])


# ============================== the retention rule ==========================

def test_the_collector_writes_exactly_the_names_the_sweep_knows():
    """One list: a name the sweep does not know would accumulate forever."""
    collector = metrics.SystemMetricsCollector()
    declared = {name for names in collector.metric_groups.values() for name in names}

    assert declared == metrics.SNAPSHOT_METRIC_NAMES


def test_excluding_a_group_in_one_collector_does_not_reach_the_shared_list():
    collector = metrics.SystemMetricsCollector()
    collector.metric_groups["cpu"].clear()

    assert metrics.METRIC_GROUPS["cpu"], "an instance edited the module's list"


def test_the_detailed_window_is_wider_than_what_the_panel_shows():
    """Two quantities compared, not two numbers: editing one would part them."""
    assert retention.DEFAULT_DETAILED_HOURS > metrics.PANEL_HISTORY_HOURS


def test_a_window_narrower_than_the_panel_is_raised_not_obeyed():
    narrow = retention.RetentionPolicy(detailed_hours=1)

    assert narrow.detailed_hours == metrics.PANEL_HISTORY_HOURS


@pytest.mark.parametrize("given", ["", "not a number", "0", "-5"])
def test_a_broken_setting_falls_back_instead_of_switching_the_sweep_off(monkeypatch, given):
    monkeypatch.setenv("METRICS_RETENTION_DAYS", given)

    assert retention.RetentionPolicy.from_env().retention_days == \
        retention.DEFAULT_RETENTION_DAYS


def test_the_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("METRICS_DETAILED_HOURS", "72")
    monkeypatch.setenv("METRICS_RETENTION_DAYS", "10")
    monkeypatch.setenv("METRICS_THINNED_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("METRICS_RETENTION_INTERVAL_SECONDS", "600")

    settings = retention.RetentionPolicy.from_env()

    assert (settings.detailed_hours, settings.retention_days,
            settings.thinned_interval_seconds, settings.sweep_interval_seconds) == \
        (72, 10, 300, 600)


# ============================== the sweep ==============================

def test_fresh_snapshots_are_left_alone(fresh_database, policy):
    series(CPU, NOW - timedelta(hours=1), 40)

    retention.run_sweep(policy, now=NOW)

    assert count() == 40


def test_snapshots_past_the_retention_horizon_are_gone(fresh_database, policy):
    series(CPU, NOW - timedelta(days=40), 10)
    series(CPU, NOW - timedelta(hours=2), 10)

    removed = retention.purge_expired(policy, now=NOW)

    assert removed == 10
    assert count() == 10


def test_old_snapshots_are_thinned_to_one_point_per_interval(fresh_database, policy):
    # An hour of snapshots every fifteen seconds past the detailed window:
    # 240 rows, sixty minutes -- so sixty points.
    series(CPU, NOW - timedelta(days=3), 240)

    retention.run_sweep(policy, now=NOW)

    assert count() == 60


def test_thinning_keeps_one_point_per_metric_and_per_instance(fresh_database, policy):
    """A bucket is metric, instance and slice of time -- not a slice alone."""
    start = NOW - timedelta(days=3)
    series(CPU, start, 4)
    series(RAM, start, 4)
    series(CPU, start, 4, instance="second")

    retention.run_sweep(policy, now=NOW)

    assert count(metric_name=CPU, app_instance="main") == 1
    assert count(metric_name=RAM, app_instance="main") == 1
    assert count(metric_name=CPU, app_instance="second") == 1


def test_the_kept_point_is_the_earliest_of_its_interval(fresh_database, policy):
    start = NOW - timedelta(days=3)
    series(CPU, start, 4)

    retention.run_sweep(policy, now=NOW)

    left = rows()
    assert len(left) == 1
    assert retention._as_datetime(left[0]["timestamp"]) == start


def test_a_second_sweep_changes_nothing(fresh_database, policy):
    series(CPU, NOW - timedelta(days=3), 240)
    series(CPU, NOW - timedelta(days=40), 10)
    series(CPU, NOW - timedelta(hours=1), 20)

    retention.run_sweep(policy, now=NOW)
    after_first = [dict(row) for row in rows()]

    swept = retention.run_sweep(policy, now=NOW)

    assert swept == {"expired": 0, "thinned": 0}
    assert [dict(row) for row in rows()] == after_first


def test_a_name_the_collector_does_not_write_is_never_touched(fresh_database, policy):
    """The same table holds occasional administrative events.

    The rule "one point a minute", sensible for a measurement, would erase the
    record of who did what.
    """
    for _ in range(3):
        write("lock_force_released", NOW - timedelta(days=90))
    write("admin_password_change", NOW - timedelta(days=90))

    retention.run_sweep(policy, now=NOW)

    assert count(metric_name="lock_force_released") == 3
    assert count(metric_name="admin_password_change") == 1


def test_deletion_goes_in_chunks_not_in_one_statement(fresh_database, policy, monkeypatch):
    """Deleting millions of rows in one statement would hold up the next write."""
    statements = []
    original = DatabaseManagerV2.execute_commit

    def counting(query, params=None):
        if query.strip().upper().startswith("DELETE"):
            statements.append(query)
        return original(query, params)

    monkeypatch.setattr(DatabaseManagerV2, "execute_commit", staticmethod(counting))
    series(CPU, NOW - timedelta(days=40), retention.DELETE_CHUNK_ROWS * 2 + 5)

    removed = retention.purge_expired(policy, now=NOW)

    assert removed == retention.DELETE_CHUNK_ROWS * 2 + 5
    assert len(statements) == 3


def test_one_pass_is_bounded_and_the_backlog_takes_several(fresh_database, policy, monkeypatch):
    monkeypatch.setattr(retention, "CANDIDATE_BATCH_ROWS", 10)
    monkeypatch.setattr(retention, "MAX_BATCHES_PER_PASS", 2)
    series(CPU, NOW - timedelta(days=40), 100)

    first = retention.purge_expired(policy, now=NOW)

    assert first == 20, "a pass has to be bounded"
    assert count() == 80

    while retention.purge_expired(policy, now=NOW):
        pass
    assert count() == 0, "a backlog is cleared over several passes"


def test_a_database_failure_does_not_reach_the_caller(fresh_database, policy, monkeypatch):
    def fails(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(DatabaseManagerV2, "execute", staticmethod(fails))

    assert retention.run_sweep(policy, now=NOW) == {"expired": 0, "thinned": 0}


# ============================== the panel's history =========================

def test_the_history_the_panel_shows_survives_the_sweep(fresh_database, policy):
    """Real time here, not a fixed NOW.

    The panel selects history by hours from the current moment, and a run laid
    out from a fixed date falls out of its window the very next day: the test
    passed on the day it was written and failed from the day after.
    """
    moment = datetime.utcnow()
    series(CPU, moment - timedelta(hours=20), 240)
    series(CPU, moment - timedelta(days=3), 240)

    before = metrics.get_historical_metrics(CPU, metrics.PANEL_HISTORY_HOURS)
    retention.run_sweep(policy, now=moment)
    after = metrics.get_historical_metrics(CPU, metrics.PANEL_HISTORY_HOURS)

    assert before and after == before


def test_the_sweep_does_shrink_the_table(fresh_database, policy):
    """Otherwise everything above would check the care of a sweep that sweeps
    nothing."""
    series(CPU, NOW - timedelta(days=3), 240)
    series(CPU, NOW - timedelta(days=40), 40)

    before = count()
    retention.run_sweep(policy, now=NOW)

    assert count() < before


# ============================== the background loop ========================

class FakeLock:
    """The distributed lock, exactly as much of it as the loop takes."""

    def __init__(self):
        self.taken = []
        self.held = False
        self.swept_while_held = False

    def __call__(self, name, timeout=None, max_lock_time=None):
        self.taken.append(name)
        lock = self

        class _Held:
            async def __aenter__(self):
                lock.held = True
                return lock

            async def __aexit__(self, *exc):
                lock.held = False
                return False

        return _Held()


async def test_the_sweep_runs_inside_the_distributed_lock(monkeypatch):
    """Several replicas, one shared job: no point deleting the same rows thrice."""
    from keepup import locks

    lock = FakeLock()
    monkeypatch.setattr(locks, "distributed_lock", lock)

    def sweeping(policy=None, now=None):
        lock.swept_while_held = lock.held
        raise KeyboardInterrupt  # leave the endless loop after one pass

    monkeypatch.setattr(retention, "run_sweep", sweeping)

    with pytest.raises(KeyboardInterrupt):
        await retention.metrics_retention_background(
            retention.RetentionPolicy(sweep_interval_seconds=1))

    assert lock.taken == [retention.LOCK_NAME]
    assert lock.swept_while_held


async def test_stopping_the_application_is_not_an_error(monkeypatch):
    """Without this branch, cancelling the task would log an error on every stop."""
    from keepup import locks

    monkeypatch.setattr(locks, "distributed_lock", FakeLock())
    monkeypatch.setattr(retention, "run_sweep", lambda policy=None, now=None: {
        "expired": 0, "thinned": 0})

    task = asyncio.create_task(retention.metrics_retention_background(
        retention.RetentionPolicy(sweep_interval_seconds=3600)))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_failing_sweep_does_not_stop_the_loop(monkeypatch):
    from keepup import locks

    monkeypatch.setattr(locks, "distributed_lock", FakeLock())
    monkeypatch.setattr(retention, "ERROR_BACKOFF_SECONDS", 0)
    attempts = []

    def sweeping(policy=None, now=None):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("database is unreachable")
        raise KeyboardInterrupt

    monkeypatch.setattr(retention, "run_sweep", sweeping)

    with pytest.raises(KeyboardInterrupt):
        await retention.metrics_retention_background(
            retention.RetentionPolicy(sweep_interval_seconds=0))

    assert len(attempts) == 3


async def test_the_application_starts_the_sweep_at_startup(monkeypatch, tmp_path):
    """A loop nobody starts is a sweep that does not exist."""
    from fastapi.testclient import TestClient

    from keepup import factory
    from keepup.settings import KeepupSettings

    started = []

    async def marker(policy=None):
        started.append(policy)
        await asyncio.Event().wait()

    monkeypatch.setattr(factory, "metrics_retention_background", marker)
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    monkeypatch.setenv("DB_TYPE", "sqlite")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "startup.db"))
    DatabaseManagerV2.dispose()

    app = factory.create_app(KeepupSettings(
        title="Framework Only", static_mounts=(), plugin_manager=None))
    with TestClient(app):
        pass
    DatabaseManagerV2.dispose()

    assert started, "the retention sweep is not started at start-up"
