"""One way into the database: the legacy DatabaseManager is gone (0.2.0).

It opened a connection of its own on every call, outside the pool, and took
positional ``?`` parameters that it rewrote for PostgreSQL. Everything in the
framework goes through the pooled DatabaseManagerV2 now; code written against
a cursor -- the application's table hook, the first-start accounts -- takes a
driver connection out of the same pool and hands it back.

    python3 -m pytest keepup/tests/one_database_manager_tests.py -v
"""

import re
from pathlib import Path

import pytest

import keepup.db
from keepup.tests.repository import is_not_the_package
from keepup.db import DatabaseManagerV2
from keepup.schema import init_db

PACKAGE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


def test_the_legacy_manager_is_gone_and_nothing_names_it():
    assert not hasattr(keepup.db, "DatabaseManager")
    assert "DatabaseManager" not in keepup.db.__all__
    word = re.compile(r"\bDatabaseManager\b(?!V2)")
    naming = [str(path.relative_to(PACKAGE)) for path in PACKAGE.rglob("*.py")
              if "tests" not in path.parts and not is_not_the_package(path)
              and word.search(path.read_text(encoding="utf-8"))
              and path.name != "db.py"]
    assert naming == []


def test_a_raw_connection_comes_from_the_pool_and_goes_back():
    pool = DatabaseManagerV2._engine.pool
    before = pool.checkedout()
    conn = DatabaseManagerV2.raw_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        assert cursor.fetchone()[0] >= 1          # the first-start accounts
        cursor.close()
        assert pool.checkedout() == before + 1
    finally:
        conn.close()
    assert pool.checkedout() == before


def test_the_start_up_leaves_no_connection_checked_out():
    before = DatabaseManagerV2._engine.pool.checkedout()
    init_db()
    assert DatabaseManagerV2._engine.pool.checkedout() == before


def test_the_health_check_answers_without_raising(monkeypatch):
    answer = DatabaseManagerV2.test_connection()
    assert answer["success"] is True and answer["database_type"] == "SQLite"
    assert answer["version"] not in (None, "", "Unknown")

    def broken(*args, **kwargs):
        raise RuntimeError("database is down")
    monkeypatch.setattr(DatabaseManagerV2, "execute_one", broken)
    answer = DatabaseManagerV2.test_connection()
    assert answer["success"] is False and "down" in answer["error"]
