"""The second factor: a code accepted once, and a session only after it (keepup-174).

RFC 6238 with the parameters authenticator apps assume, a secret in the
account's own table, recovery codes shown once, and a login that stops at
``mfa_required`` until the code arrives.

    python -m pytest tests/mfa_tests.py -v
"""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from framework_schema import create_framework_tables

from keepup.settings import KeepupSettings
from keepup_http.factory import create_app
from keepup_db import DatabaseManagerV2, db_config
from keepup_auth import dependencies, mfa
from keepup_auth.dependencies import get_current_admin

PACKAGE = Path(__file__).resolve().parents[1]
STATIC = PACKAGE / "packages" / "keepup-http" / "keepup_http" / "static"

#: RFC 6238, SHA-1, the 20-byte seed the appendix uses.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.fixture
def database(tmp_path, monkeypatch):
    DatabaseManagerV2.dispose()
    monkeypatch.setattr(db_config, "db_path", str(tmp_path / "mfa.db"), raising=False)
    monkeypatch.setattr(db_config, "db_type", "sqlite", raising=False)
    create_framework_tables()
    DatabaseManagerV2.execute_commit("DELETE FROM auth_rate_limit")
    DatabaseManagerV2.execute_commit("DELETE FROM auth_totp")
    DatabaseManagerV2.execute_commit("DELETE FROM users WHERE username = 'mfa-user'")
    yield
    DatabaseManagerV2.dispose()


def test_the_code_is_the_rfc_one():
    """Time 59 is step 1; the appendix's 94287082 ends in 287082."""
    assert mfa.code_for(RFC_SECRET, step=1) == "287082"


def test_enrolment_is_confirmed_once_and_the_code_cannot_be_reused(database):
    begun = mfa.begin(424242)
    assert mfa.needs_second_factor(424242) is False

    current = mfa.step_for()
    codes = mfa.confirm(424242, mfa.code_for(begun["secret"], current))
    assert codes and len(codes) == mfa.RECOVERY_CODES
    assert mfa.needs_second_factor(424242) is True

    next_code = mfa.code_for(begun["secret"], current + 1)
    assert mfa.verify_for_user(424242, code=next_code) is True
    assert mfa.verify_for_user(424242, code=next_code) is False, "a code is one-use"


def test_a_recovery_code_is_one_use(database):
    begun = mfa.begin(424243)
    codes = mfa.confirm(424243, mfa.code_for(begun["secret"], mfa.step_for()))
    assert mfa.verify_for_user(424243, recovery=codes[0]) is True
    assert mfa.verify_for_user(424243, recovery=codes[0]) is False


def test_a_challenge_is_not_a_session(database):
    token = mfa.challenge_token("ada")
    assert mfa.read_challenge(token) == "ada"
    assert mfa.read_challenge(token + "x") is None


def test_the_login_stops_at_the_code_and_a_code_finishes_it(database):
    async def prepare():
        await dependencies.create_user("mfa-user", "correct horse battery", agree_terms=True)
        user = dependencies.get_user_by_username("mfa-user")
        DatabaseManagerV2.execute_commit(
            "UPDATE users SET status = 'active' WHERE id = :id", {"id": user["id"]})
        begun = mfa.begin(user["id"])
        codes = mfa.confirm(user["id"], mfa.code_for(begun["secret"], mfa.step_for()))
        return user["id"], begun["secret"], codes

    user_id, secret, _ = asyncio.run(prepare())
    app = create_app(KeepupSettings(title="MFA", project_name="mfa",
                                    static_dir=str(STATIC), static_mounts=()))
    client = TestClient(app)

    first = client.post("/api/auth/login",
                        json={"username": "mfa-user", "password": "correct horse battery"})
    assert first.status_code == 200
    body = first.json()
    assert body["mfa_required"] is True
    assert "access_token" not in body

    code = mfa.code_for(secret, mfa.step_for() + 1)
    second = client.post("/api/auth/mfa/verify",
                         json={"mfa_token": body["mfa_token"], "code": code})
    assert second.status_code == 200, second.text
    token = second.json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "mfa-user"

    replay = client.post("/api/auth/mfa/verify",
                         json={"mfa_token": body["mfa_token"], "code": code})
    assert replay.status_code == 401


def test_a_deployment_can_require_the_second_factor_of_administrators(monkeypatch):
    monkeypatch.setenv("MFA_REQUIRED_FOR_ADMINS", "true")

    async def refused():
        return await get_current_admin({"id": 1, "username": "root",
                                        "roles": ["ADMIN"], "amr": ("pwd",)})

    async def allowed():
        return await get_current_admin({"id": 1, "username": "root",
                                        "roles": ["ADMIN"], "amr": ("pwd", "otp")})

    with pytest.raises(Exception) as refusal:
        asyncio.run(refused())
    assert getattr(refusal.value, "status_code", None) == 403
    assert asyncio.run(allowed())["username"] == "root"
