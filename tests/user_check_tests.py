"""The user check every signed-in request makes (keepup-43).

One read of the account per request, the session check and that read in one
hop to a worker thread, and the loop free while they run. It used to read the
account twice, each time on a new event loop in a thread the request waited for
with a blocking join -- holding the loop every request needs.

Against the session's throwaway SQLite database:

    python3 -m pytest keepup/tests/user_check_tests.py -v
"""

import asyncio
import threading
import time

import pytest
from fastapi import HTTPException

from keepup.auth import dependencies, panel_session
from keepup.auth.providers import local
from keepup.auth.providers.base import AuthProvider
from keepup.db import DatabaseManagerV2
from keepup.schema import init_db


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


def make_user(username, status="active"):
    uid = dependencies.save_user_to_db(username, "a-long-password-43")
    dependencies.update_user(uid, status=status)
    return uid


@pytest.fixture
def reads(monkeypatch):
    """Counts the account reads the local provider makes."""
    counted = []
    real = local.get_user_by_username

    def counting(username):
        counted.append(username)
        return real(username)
    monkeypatch.setattr(local, "get_user_by_username", counting)
    return counted


def token_for(uid, username):
    return dependencies.issue_session_token(uid, username)["access_token"]


# --- what the client sees does not change ------------------------------------------

async def test_an_active_user_is_read_once(reads):
    uid = make_user("check-active")
    user = await dependencies.get_current_user(token_for(uid, "check-active"))
    assert user["id"] == uid and user["session_id"]
    assert reads == ["check-active"]


async def test_a_revoked_session_is_refused_without_reading_the_account(reads):
    uid = make_user("check-revoked")
    issued = dependencies.issue_session_token(uid, "check-revoked")
    panel_session.revoke(issued["session_id"])
    with pytest.raises(HTTPException) as refused:
        await dependencies.get_current_user(issued["access_token"])
    assert refused.value.status_code == 401
    assert reads == []


async def test_a_blocked_user_is_refused_with_403(reads):
    uid = make_user("check-blocked", status="blocked")
    with pytest.raises(HTTPException) as refused:
        await dependencies.get_current_user(token_for(uid, "check-blocked"))
    assert refused.value.status_code == 403
    assert len(reads) == 1


async def test_a_removed_account_and_a_bad_token_are_refused_with_401():
    uid = make_user("check-removed")
    token = token_for(uid, "check-removed")
    DatabaseManagerV2.execute_commit("DELETE FROM users WHERE id = :id", {"id": uid})
    for bad in (token, "not-a-token"):
        with pytest.raises(HTTPException) as refused:
            await dependencies.get_current_user(bad)
        assert refused.value.status_code == 401


# --- the loop ------------------------------------------------------------------------

async def test_the_loop_serves_others_while_the_database_is_slow(monkeypatch):
    uid = make_user("check-slow")
    token = token_for(uid, "check-slow")
    real = local.get_user_by_username

    def slow(username):
        time.sleep(0.3)
        return real(username)
    monkeypatch.setattr(local, "get_user_by_username", slow)

    ticks = []

    async def other_request():
        for _ in range(5):
            ticks.append(time.monotonic())
            await asyncio.sleep(0.02)

    started = time.monotonic()
    await asyncio.gather(dependencies.get_current_user(token), other_request())
    # The other task ran all its steps while the read was still sleeping.
    assert ticks[-1] - started < 0.25


async def test_a_synchronous_lookup_from_a_running_loop_starts_no_thread_or_loop(monkeypatch):
    make_user("check-sync")
    started = []
    monkeypatch.setattr(threading.Thread, "start",
                        lambda self: started.append(self) or (_ for _ in ()).throw(
                            AssertionError("a thread was started")))
    monkeypatch.setattr(asyncio, "new_event_loop",
                        lambda: (_ for _ in ()).throw(AssertionError("a loop was created")))
    assert dependencies.get_user_by_username("check-sync")["username"] == "check-sync"
    assert started == []


# --- a provider of the application's own keeps working --------------------------------

class OnlyAsync(AuthProvider):
    """A provider that gives only the asynchronous read."""

    async def authenticate(self, username, password):
        return None

    async def get_user_info(self, username):
        return {"username": username}

    async def get_user_permissions(self, username):
        return {}

    async def create_user(self, user_data):
        return False


def test_a_provider_without_a_synchronous_read_is_still_answered_outside_a_loop():
    assert OnlyAsync().lookup_user("x") == {"username": "x"}


async def test_a_provider_without_a_synchronous_read_is_still_answered_inside_a_loop():
    assert OnlyAsync().lookup_user("y") == {"username": "y"}
