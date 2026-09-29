"""Signing in through a provider and starting up keep the database off the loop
(keepup-54).

The end of a sign-in through an external provider -- finding or creating the
account, bringing its roles in step, opening the session -- and the table set-up
at start-up ran their queries on the event loop, holding every other request of
the replica while they waited. Each blocking step is watched here: it must run
where no event loop is running, i.e. on a worker thread.

    python3 -m pytest keepup/tests/sign_in_off_loop_tests.py -v
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from keepup import factory
from keepup.auth import oidc_policy, oidc_routes

from oidc_flow_tests import begin, build, forget, return_from_provider, tables  # noqa: F401
from oidc_tests import ISSUER, FakeProvider


def on_the_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def watched(calls, name, original):
    def wrapper(*args, **kwargs):
        calls.append((name, on_the_loop()))
        return original(*args, **kwargs)
    return wrapper


@pytest.fixture
def provider():
    return FakeProvider()


def test_every_blocking_step_of_a_provider_sign_in_runs_off_the_loop(provider, monkeypatch):
    calls = []
    for name in ("find_account", "create_account", "sync_roles", "issue_session_token"):
        monkeypatch.setattr(oidc_routes, name, watched(calls, name, getattr(oidc_routes, name)))
    forget(ISSUER, "off-loop-subject")

    app = build(provider, policy=oidc_policy.create_account())
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as client:
        state, nonce, cookie = begin(client)
        answer = return_from_provider(client, provider, state, nonce, cookie,
                                      claims={"sub": "off-loop-subject"})

    assert answer.status_code == 303, answer.text
    assert {name for name, _ in calls} >= {"find_account", "create_account",
                                           "sync_roles", "issue_session_token"}
    assert [name for name, loop in calls if loop] == []


def test_a_refusal_from_the_worker_is_still_a_refusal(provider):
    forget(ISSUER, "refused-subject")
    app = build(provider, policy=None)
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as client:
        state, nonce, cookie = begin(client)
        answer = return_from_provider(client, provider, state, nonce, cookie,
                                      claims={"sub": "refused-subject"})
    assert answer.status_code == 401


def test_start_up_sets_its_tables_up_off_the_loop(provider, monkeypatch):
    calls = []
    for name in ("init_incoming_requests_table", "init_event_manager"):
        monkeypatch.setattr(factory, name, watched(calls, name, getattr(factory, name)))
    with TestClient(build(provider)):
        pass
    assert {name for name, _ in calls} == {"init_incoming_requests_table", "init_event_manager"}
    assert [name for name, loop in calls if loop] == []
