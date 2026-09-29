"""The CSRF value belongs to the session and changes with every sign-in (keepup-72).

It was a random value kept in a cookie readable by the page and trusted from
that cookie: whoever could plant a cookie -- a sibling subdomain, a plain-HTTP
hop -- chose the value the double-submit check would accept, and the value
survived every sign-in. It is now derived from the session.

    python3 -m pytest keepup/tests/csrf_binding_tests.py -v
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup.auth import dependencies, panel_session
from keepup.auth.routes import register_auth_routes
from keepup.db import DatabaseManagerV2
from keepup.schema import init_db

PASSWORD = "a-long-password-72"


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


@pytest.fixture(autouse=True)
def no_throttle():
    DatabaseManagerV2.execute_commit("DELETE FROM login_attempts")


def app():
    application = FastAPI()
    register_auth_routes(application, SimpleNamespace(plugins={}))
    # Looked up rather than named, so the same tests run against the code
    # from before it existed.
    refresh = getattr(panel_session, "CsrfCookieRefresh", None)
    if refresh is not None:
        application.add_middleware(refresh)
    return application


def account(name):
    if not dependencies.get_user_by_username(name):
        uid = dependencies.save_user_to_db(name, PASSWORD)
        dependencies.update_user(uid, status="active")
    return name


def signed_in(name):
    client = TestClient(app())
    assert client.post("/api/auth/login",
                       json={"username": name, "password": PASSWORD}).status_code == 200
    return client


def csrf(client):
    return client.cookies.get(panel_session.CSRF_COOKIE)


def plant(client, value):
    """Replace the CSRF cookie the way somebody able to set cookies would."""
    domain = next(c.domain for c in client.cookies.jar if c.name == panel_session.CSRF_COOKIE)
    client.cookies.delete(panel_session.CSRF_COOKIE)
    client.cookies.set(panel_session.CSRF_COOKIE, value, domain=domain)


def test_a_planted_value_is_not_accepted():
    client = signed_in(account("csrf-victim"))
    plant(client, "chosen-by-the-attacker")
    answer = client.post("/api/auth/refresh",
                         headers={panel_session.CSRF_HEADER: "chosen-by-the-attacker"})
    assert answer.status_code == 403


def test_the_session_s_value_is_accepted():
    client = signed_in(account("csrf-owner"))
    answer = client.post("/api/auth/refresh", headers={panel_session.CSRF_HEADER: csrf(client)})
    assert answer.status_code == 200


def test_signing_in_again_in_the_same_browser_gets_a_new_value():
    """The old value was carried over from the cookie into every new sign-in."""
    name = account("csrf-twice")
    client = signed_in(name)
    before = csrf(client)
    assert client.post("/api/auth/login",
                       json={"username": name, "password": PASSWORD}).status_code == 200
    assert csrf(client) != before


def test_a_renewal_keeps_the_value():
    client = signed_in(account("csrf-renewed"))
    before = csrf(client)
    client.post("/api/auth/refresh", headers={panel_session.CSRF_HEADER: before})
    assert csrf(client) == before


def test_a_session_from_before_gets_its_value_on_the_next_read():
    """What an open panel holds on the release day: a random value."""
    client = signed_in(account("csrf-old-tab"))
    bound = csrf(client)
    plant(client, "random-from-before")
    client.get("/api/auth/me")
    assert csrf(client) == bound
    assert client.post("/api/auth/refresh",
                       headers={panel_session.CSRF_HEADER: csrf(client)}).status_code == 200


def test_the_value_is_not_the_session_id():
    client = signed_in(account("csrf-opaque"))
    token = client.cookies.get(panel_session.SESSION_COOKIE)
    sid = dependencies.jwt.decode(token, options={"verify_signature": False})["sid"]
    assert sid not in csrf(client)


def test_a_token_without_a_session_is_refused_whatever_the_cookie_says():
    """No session, no value to check against -- and no token accepted (keepup-81)."""
    name = account("csrf-legacy")
    legacy = dependencies.create_access_token({"sub": name}, expires_delta=timedelta(minutes=5))
    client = TestClient(app())
    client.cookies.set(panel_session.SESSION_COOKIE, legacy)
    client.cookies.set(panel_session.CSRF_COOKIE, "legacy-value")
    answer = client.post("/api/auth/session", headers={panel_session.CSRF_HEADER: "legacy-value"})
    assert answer.status_code in (401, 403)
