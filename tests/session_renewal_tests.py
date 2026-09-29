"""A session cannot be carried on past its renewal window by any door (keepup-64).

/api/auth/refresh refused a session that had run its course, but the exchange of
a token for the cookie (/api/auth/session) renewed it without the check, so a
stolen token could be kept alive for ever. The token itself is also held to what
this server issues: an expiry, a subject, and a session -- when it names one --
belonging to that account.

    python3 -m pytest keepup/tests/session_renewal_tests.py -v
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup.auth import dependencies, panel_session
from keepup.auth.providers.base import ALGORITHM
from keepup.auth.signing_key import resolve_signing_key
from keepup.auth.routes import register_auth_routes
from keepup.schema import init_db


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    register_auth_routes(app, SimpleNamespace(plugins={}))
    return TestClient(app)


def account(name):
    user = dependencies.get_user_by_username(name)
    if not user:
        uid = dependencies.save_user_to_db(name, "a-long-password-64")
        dependencies.update_user(uid, status="active")
        user = dependencies.get_user_by_username(name)
    return user


def token_started(user, started):
    """A token of a live session that began at ``started``."""
    sid = panel_session.open_session(user["id"], timedelta(hours=1))
    return dependencies.create_access_token(
        {"sub": user["username"], "sid": sid}, expires_delta=timedelta(hours=1),
        session_started_at=started)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_an_old_session_is_renewed_by_neither_door(client):
    user = account("renewal-old")
    token = token_started(user, datetime.utcnow() - timedelta(days=365))
    assert client.post("/api/auth/refresh", headers=bearer(token)).status_code == 401
    assert client.post("/api/auth/session", headers=bearer(token)).status_code == 401


def test_a_fresh_session_is_renewed_by_both(client):
    user = account("renewal-fresh")
    token = token_started(user, datetime.utcnow())
    assert client.post("/api/auth/refresh", headers=bearer(token)).status_code == 200
    assert client.post("/api/auth/session", headers=bearer(token)).status_code == 200


def signed(claims):
    return jwt.encode(claims, resolve_signing_key(), algorithm=ALGORITHM)


@pytest.mark.parametrize("drop", ["exp", "sub", "sid"])
def test_a_token_without_an_expiry_a_subject_or_a_session_is_refused(client, drop):
    user = account("renewal-claims")
    sid = panel_session.open_session(user["id"], timedelta(hours=1))
    claims = {"sub": user["username"], "sid": sid,
              "exp": datetime.utcnow() + timedelta(hours=1)}
    claims.pop(drop)
    assert client.get("/api/auth/me", headers=bearer(signed(claims))).status_code == 401


def test_a_session_of_another_account_is_refused(client):
    owner, other = account("renewal-owner"), account("renewal-other")
    sid = panel_session.open_session(owner["id"], timedelta(hours=1))
    token = signed({"sub": other["username"], "sid": sid,
                    "exp": datetime.utcnow() + timedelta(hours=1)})
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401
