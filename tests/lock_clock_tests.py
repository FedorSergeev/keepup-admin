"""A lock's time is written and judged by one clock (keepup-70).

The holder's time was written by the database's CURRENT_TIMESTAMP and compared
with the application's datetime.utcnow(). With a database zone other than UTC
a crashed holder's lock hung for hours longer, or a live one looked stale at
once and was taken over. The time is now the application's, like the
comparison.

    python3 -m pytest keepup/tests/lock_clock_tests.py -v
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from keepup import locks, schema
from keepup.db import DatabaseManagerV2, db_config


@pytest.fixture
def fresh_database(tmp_path, monkeypatch):
    DatabaseManagerV2.dispose()
    monkeypatch.setattr(db_config, "db_path", str(tmp_path / "locks.db"), raising=False)
    monkeypatch.setattr(db_config, "db_type", "sqlite", raising=False)
    schema.init_db()
    yield
    DatabaseManagerV2.dispose()


def frozen_at(moment):
    class Frozen(datetime):
        @classmethod
        def utcnow(cls):
            return moment
    return Frozen


def held_since(name):
    row = DatabaseManagerV2.execute_one(
        "SELECT acquired_at FROM distributed_locks WHERE lock_name = :n", {"n": name})
    value = row["acquired_at"]
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def test_the_holder_s_time_is_the_application_s(fresh_database, monkeypatch):
    """Whatever the database's clock says, the row carries the time the
    application will compare against."""
    moment = datetime(2031, 1, 1, 3, 0, 0)
    monkeypatch.setattr(locks, "datetime", frozen_at(moment))
    assert asyncio.run(locks.DatabaseLock("clock", timeout=5).acquire()) is True
    assert held_since("clock") == moment


def test_a_live_lock_is_not_taken_over_and_a_dead_one_is(fresh_database, monkeypatch):
    start = datetime(2031, 1, 1, 3, 0, 0)
    monkeypatch.setattr(locks, "datetime", frozen_at(start))
    assert asyncio.run(locks.DatabaseLock("job", timeout=5, max_lock_time=60).acquire())

    monkeypatch.setattr(locks, "datetime", frozen_at(start + timedelta(seconds=30)))
    assert asyncio.run(locks.DatabaseLock("job", timeout=5, max_lock_time=60).acquire()) is False

    monkeypatch.setattr(locks, "datetime", frozen_at(start + timedelta(seconds=61)))
    assert asyncio.run(locks.DatabaseLock("job", timeout=5, max_lock_time=60).acquire()) is True


def test_no_statement_writes_the_database_s_clock():
    source = Path(locks.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
    assert "CURRENT_TIMESTAMP" not in code and "NOW()" not in code.upper().replace("UTCNOW()", "")
