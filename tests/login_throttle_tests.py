"""The failed-login count loses nothing when failures arrive at once (keepup-48).

The count used to be read, incremented in Python and written back: failures
landing together -- on several replicas, or several threads of one -- read the
same number and each wrote it plus one, and two first failures both tried to
insert. The lockout then came later than LOGIN_MAX_ATTEMPTS said. Against the
session's throwaway SQLite database, with threads standing in for replicas.

    python3 -m pytest keepup/tests/login_throttle_tests.py -v
"""

import threading
from datetime import datetime, timedelta

import pytest

from keepup.auth import login_throttle
from keepup.db import DatabaseManagerV2
from keepup.schema import init_db

AT_ONCE = 20


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


@pytest.fixture(autouse=True)
def no_attempts():
    DatabaseManagerV2.execute_commit("DELETE FROM login_attempts")
    yield
    DatabaseManagerV2.execute_commit("DELETE FROM login_attempts")


def failures(name):
    row = DatabaseManagerV2.execute_one(
        "SELECT failures FROM login_attempts WHERE username = :n", {"n": name})
    return row["failures"] if row else 0


def fail_at_once(name, count, now=None):
    start = threading.Barrier(count)

    def attempt():
        start.wait()
        login_throttle.record_failure(name, now=now)

    threads = [threading.Thread(target=attempt) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def test_failures_at_once_on_a_new_name_are_all_counted():
    fail_at_once("guessed", AT_ONCE)
    assert failures("guessed") == AT_ONCE


def test_failures_at_once_on_a_known_name_are_all_counted():
    login_throttle.record_failure("known")
    fail_at_once("known", AT_ONCE)
    assert failures("known") == AT_ONCE + 1


def test_the_lockout_comes_exactly_at_the_limit(monkeypatch):
    monkeypatch.setenv(login_throttle.MAX_ATTEMPTS_ENV, str(AT_ONCE))
    fail_at_once("limit", AT_ONCE - 1)
    assert login_throttle.locked_until("limit") is None
    login_throttle.record_failure("limit")
    assert login_throttle.locked_until("limit") is not None


def test_a_failure_after_the_window_starts_the_count_again():
    long_ago = datetime.utcnow() - login_throttle.lockout_window() - timedelta(minutes=1)
    fail_at_once("returning", 5, now=long_ago)
    login_throttle.record_failure("returning")
    assert failures("returning") == 1


def test_the_count_is_one_statement():
    """Kept by the database, not read and written back."""
    source = login_throttle.record_failure.__code__.co_names
    assert "_row" not in source
    assert "ON CONFLICT (username) DO UPDATE" in login_throttle._RECORD_FAILURE
