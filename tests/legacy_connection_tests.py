"""The legacy DatabaseManager's batch write closes its connection (keepup-42).

executemany_commit opened a connection and never closed it, on success and on
failure alike; on PostgreSQL each call kept one more server connection until
the server's limit ran out.

    python3 -m pytest keepup/tests/legacy_connection_tests.py -v
"""

import sqlite3

import pytest

from keepup.db import DatabaseManager


class Recorded:
    """A real SQLite connection that remembers whether it was closed."""

    def __init__(self, fail=False):
        self.inner = sqlite3.connect(":memory:")
        self.inner.execute("CREATE TABLE t (v INTEGER)")
        self.fail = fail
        self.closed = False
        self.rolled_back = False

    def cursor(self):
        cursor = self.inner.cursor()
        if self.fail:
            class Broken:
                def executemany(self, *a):
                    raise sqlite3.OperationalError("disk I/O error")

                def close(self):
                    pass
            return Broken()
        return cursor

    def commit(self):
        self.inner.commit()

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.mark.parametrize("fail", [False, True])
def test_the_connection_is_closed_on_success_and_on_failure(monkeypatch, fail):
    connection = Recorded(fail=fail)
    monkeypatch.setattr(DatabaseManager, "get_connection", staticmethod(lambda: connection))

    result = DatabaseManager.executemany_commit("INSERT INTO t (v) VALUES (?)", [(1,), (2,)])

    assert result is (not fail)
    assert connection.closed, "every call left one connection open"
    if fail:
        assert connection.rolled_back
    else:
        assert connection.inner.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2
