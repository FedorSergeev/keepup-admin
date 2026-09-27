"""The event log sweeps itself (keepup-47).

It used to be emptied only by a manual route, so it grew for as long as the
deployment ran. Against the session's throwaway SQLite database.

    python3 -m pytest keepup/tests/events_retention_tests.py -v
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from keepup import events, retention
from keepup.db import DatabaseManagerV2
from keepup.schema import init_db

PACKAGE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()
    events.event_manager.init_table()


@pytest.fixture(autouse=True)
def empty_log():
    DatabaseManagerV2.execute_commit("DELETE FROM app_events")
    yield
    DatabaseManagerV2.execute_commit("DELETE FROM app_events")


def event_aged(days: int, text: str):
    DatabaseManagerV2.execute_commit(
        "INSERT INTO app_events (event_type, event_text, instance_id, created_at) "
        "VALUES ('check', :text, 'i', :at)",
        {"text": text, "at": datetime.utcnow() - timedelta(days=days)})


def texts():
    return sorted(r["event_text"] for r in DatabaseManagerV2.execute(
        "SELECT event_text FROM app_events"))


def test_events_older_than_the_retention_period_go_and_newer_stay():
    event_aged(120, "old")
    event_aged(89, "recent")
    event_aged(0, "today")
    assert events.purge_old_events() == 1
    assert texts() == ["recent", "today"]


def test_the_period_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("EVENTS_RETENTION_DAYS", "7")
    assert events.events_retention_days() == 7
    event_aged(10, "ten days")
    event_aged(3, "three days")
    events.purge_old_events()
    assert texts() == ["three days"]


@pytest.mark.parametrize("raw", ["", "soon", "0", "-5"])
def test_rubbish_in_the_variable_means_the_default(monkeypatch, raw):
    monkeypatch.setenv("EVENTS_RETENTION_DAYS", raw)
    assert events.events_retention_days() == events.DEFAULT_EVENTS_RETENTION_DAYS


def test_a_backlog_is_cleared_in_short_passes(monkeypatch):
    monkeypatch.setattr(events, "EVENTS_DELETE_CHUNK_ROWS", 2)
    monkeypatch.setattr(events, "EVENTS_MAX_CHUNKS_PER_PASS", 2)
    for n in range(7):
        event_aged(200, f"old {n}")
    assert events.purge_old_events() == 4          # one pass: two chunks of two
    assert events.purge_old_events() == 3
    assert texts() == []


async def test_the_sweep_runs_under_its_lock_and_stops_when_cancelled(monkeypatch):
    event_aged(200, "old")
    swept = asyncio.Event()
    real = events.purge_old_events

    def purge():
        removed = real()
        swept.set()
        return removed
    monkeypatch.setattr(events, "purge_old_events", purge)

    task = asyncio.create_task(events.events_retention_background())
    await asyncio.wait_for(swept.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert texts() == []
    assert DatabaseManagerV2.execute(
        "SELECT * FROM distributed_locks WHERE lock_name = :n",
        {"n": events.EVENTS_RETENTION_LOCK}) == []


def test_the_application_starts_and_stops_the_sweep():
    source = (PACKAGE / "factory.py").read_text(encoding="utf-8")
    assert "asyncio.create_task(events_retention_background())" in source
    assert "events_retention_task.cancel()" in source


def test_the_audit_and_the_event_log_share_one_sweep():
    """One chunked delete, not two copies drifting apart."""
    audit = (PACKAGE / "audit.py").read_text(encoding="utf-8")
    assert "retention.purge_older_than(" in audit
    assert "DELETE FROM incoming_requests" not in audit
    assert retention.purge_older_than.__module__ == "keepup.retention"
