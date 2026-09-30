"""A read does not commit a transaction (keepup-87).

The helpers of the pooled manager opened a transaction around every statement
and committed it, reads included: a BEGIN and a COMMIT -- a round trip -- for a
query that wrote nothing. On the load stand (keepup-53) committing was a sixth
of what a replica spent. A plain SELECT now runs in autocommit; anything else,
and anything inside shared_session(), keeps its transaction.

    python3 -m pytest keepup/tests/reads_without_commit_tests.py -v
"""

import pytest
from sqlalchemy import event

from keepup.db import DatabaseManagerV2, _reads_only
from keepup.schema import init_db


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()
    DatabaseManagerV2.execute_commit(
        "CREATE TABLE IF NOT EXISTS read_probe (id INTEGER PRIMARY KEY, note TEXT)")


@pytest.fixture
def commits():
    engine = DatabaseManagerV2._engine
    counted = []
    listener = lambda connection: counted.append(1)
    event.listen(engine, "commit", listener)
    yield counted
    event.remove(engine, "commit", listener)


def test_a_select_commits_nothing(commits):
    DatabaseManagerV2.execute("SELECT 1 AS one")
    DatabaseManagerV2.execute_one("SELECT COUNT(*) AS n FROM read_probe")
    assert commits == []


def test_a_write_still_commits(commits):
    DatabaseManagerV2.execute_commit("INSERT INTO read_probe (note) VALUES ('a')")
    assert len(commits) == 1


def test_a_write_through_a_read_helper_still_commits(commits):
    """execute_one() is also called with INSERT ... RETURNING."""
    DatabaseManagerV2.execute_one("INSERT INTO read_probe (note) VALUES ('b') RETURNING id")
    assert len(commits) == 1
    assert DatabaseManagerV2.execute_one(
        "SELECT COUNT(*) AS n FROM read_probe WHERE note = 'b'")["n"] == 1


def test_a_shared_block_commits_once_at_its_end(commits):
    with DatabaseManagerV2.shared_session():
        DatabaseManagerV2.execute("SELECT 1 AS one")
        DatabaseManagerV2.execute_commit("INSERT INTO read_probe (note) VALUES ('c')")
    assert len(commits) == 1


@pytest.mark.parametrize("query, reads", [
    ("SELECT 1", True), ("  select * from t", True), ("(SELECT 1)", True),
    ("SELECT * FROM t FOR UPDATE", False), ("SELECT * FROM t FOR SHARE", False),
    ("INSERT INTO t VALUES (1) RETURNING id", False), ("WITH x AS (DELETE FROM t) SELECT 1", False),
    ("UPDATE t SET a = 1", False),
])
def test_what_counts_as_a_plain_read(query, reads):
    assert _reads_only(query) is reads
