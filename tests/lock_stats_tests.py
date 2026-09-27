"""The administrator's lock statistics answer on both dialects (keepup-40).

The age of a lock was computed in SQL with JULIANDAY, which only SQLite has: on
PostgreSQL -- every real deployment -- /api/admin/locks/stats failed. The age is
now worked out in Python from the stored time, whatever form the driver returns.

    python3 -m pytest keepup/tests/lock_stats_tests.py -v
"""

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from keepup import locks, schema
from keepup.db import DatabaseManagerV2, db_config

LOCKS = Path(__file__).resolve().parents[1] / "locks.py"


@pytest.fixture
def fresh_database(tmp_path, monkeypatch):
    DatabaseManagerV2.dispose()
    monkeypatch.setattr(db_config, "db_path", str(tmp_path / "locks.db"), raising=False)
    monkeypatch.setattr(db_config, "db_type", "sqlite", raising=False)
    schema.init_db()
    yield
    DatabaseManagerV2.dispose()


def test_no_sqlite_only_function_is_left_in_the_lock_queries():
    source = LOCKS.read_text(encoding="utf-8")
    for function in ("JULIANDAY", "strftime(", "datetime('now'", "INSERT OR"):
        assert function not in source, f"locks.py still uses {function}, which PostgreSQL lacks"


def test_the_statistics_name_the_locks_and_how_long_they_are_held(fresh_database):
    now = datetime(2026, 9, 27, 12, 0, 0)
    for name, instance, acquired in (("sweep", "a-1", datetime(2026, 9, 27, 11, 59, 30)),
                                     ("billing", "b-2", datetime(2026, 9, 27, 11, 50, 0)),
                                     ("audit", "a-1", datetime(2026, 9, 27, 11, 59, 59))):
        DatabaseManagerV2.execute_commit(
            "INSERT INTO distributed_locks (lock_name, acquired_at, instance_id) "
            "VALUES (:n, :t, :i)", {"n": name, "t": acquired, "i": instance})

    stats = locks.lock_stats(now)

    assert stats["total_active_locks"] == 3
    assert stats["locks_by_instance"][0] == {"instance_id": "a-1", "lock_count": 2}
    assert [(l["lock_name"], l["seconds_held"]) for l in stats["oldest_locks"]] == [
        ("billing", 600.0), ("sweep", 30.0), ("audit", 1.0)]


@pytest.mark.parametrize("stored", [
    datetime(2026, 9, 27, 11, 59, 0),
    "2026-09-27 11:59:00",
    "2026-09-27T11:59:00.000000",
    datetime(2026, 9, 27, 11, 59, 0, tzinfo=timezone.utc),
])
def test_the_age_is_read_from_whatever_the_driver_returns(stored):
    assert locks._seconds_since(stored, datetime(2026, 9, 27, 12, 0, 0)) == 60.0
