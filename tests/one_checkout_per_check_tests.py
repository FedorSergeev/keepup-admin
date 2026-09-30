"""The session check of a request takes one connection, not three (keepup-85).

Every request with a sign-in checks its session, reads the account and its
roles, and each of the three reads took a connection from the pool on its own
-- a checkout, a ping, the query, a commit. On the load stand (keepup-53) that
was half of what a replica spent. The reads now share one session.

    python3 -m pytest keepup/tests/one_checkout_per_check_tests.py -v
"""

import threading
from datetime import timedelta

import pytest
from sqlalchemy import event

from keepup.auth import dependencies, panel_session
from keepup.db import DatabaseManagerV2
from keepup.schema import init_db


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


@pytest.fixture
def checkouts():
    DatabaseManagerV2.execute("SELECT 1")          # the engine exists
    engine = DatabaseManagerV2._engine
    counted = []
    listener = lambda *args: counted.append(threading.get_ident())
    event.listen(engine, "checkout", listener)
    yield counted
    event.remove(engine, "checkout", listener)


def signed_in(name):
    user = dependencies.get_user_by_username(name)
    if not user:
        uid = dependencies.save_user_to_db(name, "a-long-password-85")
        dependencies.update_user(uid, status="active")
        user = dependencies.get_user_by_username(name)
    sid = panel_session.open_session(user["id"], timedelta(hours=1))
    return {"sub": name, panel_session.SESSION_CLAIM: sid}, user


def test_a_check_takes_one_connection(checkouts):
    payload, user = signed_in("one-checkout")
    checkouts.clear()                               # the setup above is not the check
    live, found = dependencies._session_and_user(payload, "one-checkout")
    assert live and found["id"] == user["id"] and found["roles"]
    assert len(checkouts) == 1


def test_a_revoked_session_is_still_refused(checkouts):
    payload, _ = signed_in("one-checkout-revoked")
    panel_session.revoke(payload[panel_session.SESSION_CLAIM])
    assert dependencies._session_and_user(payload, "one-checkout-revoked") == (False, None)


def test_a_failing_read_refuses_rather_than_raises(monkeypatch):
    payload, _ = signed_in("one-checkout-broken")
    def broken(username):
        DatabaseManagerV2.execute("SELECT * FROM no_such_table")
    monkeypatch.setattr(dependencies.auth_provider, "lookup_user", broken)
    assert dependencies._session_and_user(payload, "one-checkout-broken") == (False, None)


def test_queries_outside_a_shared_block_each_take_their_own(checkouts):
    DatabaseManagerV2.execute("SELECT 1")
    DatabaseManagerV2.execute("SELECT 1")
    assert len(checkouts) == 2


def test_another_thread_inside_the_block_gets_a_session_of_its_own():
    seen = {}
    with DatabaseManagerV2.shared_session() as outer:
        with DatabaseManagerV2.get_session() as same:
            seen["same"] = same is outer
        def elsewhere():
            with DatabaseManagerV2.get_session() as other:
                seen["other"] = other is outer
        worker = threading.Thread(target=elsewhere)
        worker.start()
        worker.join()
    assert seen == {"same": True, "other": False}
