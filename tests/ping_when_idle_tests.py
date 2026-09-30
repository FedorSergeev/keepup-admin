"""A connection is pinged at checkout only after lying idle (keepup-86).

The pool pinged the database on every checkout (pool_pre_ping); on the load
stand (keepup-53) that was a fifth of what a replica spent. A connection handed
back a moment ago is handed out again unpinged; one that has lain longer than
the threshold is pinged, and one failing the ping is replaced.

    python3 -m pytest keepup/tests/ping_when_idle_tests.py -v
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

from keepup import db


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(db.time, "monotonic", lambda: now["t"])
    return now


def engine_with(idle_seconds, tmp_path, pings, fail=None):
    engine = create_engine(f"sqlite:///{tmp_path / 'ping.db'}", poolclass=QueuePool,
                           pool_size=1, max_overflow=0)
    original = engine.dialect.do_ping

    def counted(dbapi_connection):
        pings.append(1)
        if fail and fail[0]:
            fail[0] -= 1
            raise RuntimeError("connection closed by the server")
        return original(dbapi_connection)

    engine.dialect.do_ping = counted
    db._ping_when_idle(engine, idle_seconds)
    return engine


def query(engine):
    with engine.connect() as connection:
        return connection.execute(text("SELECT 1")).scalar()


def test_a_connection_just_handed_back_is_not_pinged(tmp_path, clock):
    pings = []
    engine = engine_with(10, tmp_path, pings)
    for _ in range(5):
        query(engine)
        clock["t"] += 1
    assert pings == []


def test_a_connection_idle_past_the_threshold_is_pinged(tmp_path, clock):
    pings = []
    engine = engine_with(10, tmp_path, pings)
    query(engine)
    clock["t"] += 11
    query(engine)
    assert pings == [1]


def test_a_connection_failing_its_ping_is_replaced(tmp_path, clock):
    pings, fail = [], [1]
    engine = engine_with(10, tmp_path, pings, fail)
    query(engine)
    clock["t"] += 11
    assert query(engine) == 1           # a fresh connection answered
    assert pings == [1]


def test_zero_pings_on_every_checkout(tmp_path, clock):
    pings = []
    engine = engine_with(0, tmp_path, pings)
    query(engine)
    query(engine)
    assert len(pings) == 2


def test_postgres_no_longer_asks_for_the_builtin_ping(monkeypatch):
    for name, value in {"DB_TYPE": "postgres", "DB_HOST": "db.invalid",
                        "DB_POOL_PING_AFTER_IDLE": "7"}.items():
        monkeypatch.setenv(name, value)
    config = db.DatabaseConfig()
    assert config.get_sqlalchemy_engine_params()["pool_pre_ping"] is False
    assert config.pool_ping_after_idle == 7
