"""The surface keepup-91 opened: somebody else's system behind the framework.

The identity provider takes tokens the framework did not sign, passwords it does
not hold and decisions it does not make. Each is a way in, so each is knocked on
here the way an attacker would: take over a local account by name or address,
keep a token alive past its end, get a 200 out of a provider that is down or
misconfigured, carry a foreign token in a cookie past CSRF, start a deployment
that quietly lost its secret.

All of it runs against the homegrown system on a real port, through the
provider plugin a deployment would name in its configmap.

    python3 -m pytest keepup/tests/security_audit_tests/identity_surface_tests.py -v
"""

import time

import httpx
import pytest

from keepup.auth import dependencies, login_throttle, panel_session, user_roles
from keepup.db import DatabaseManagerV2
from keepup.roles import ROLE_ADMIN

import audit_actors
from audit_profiles import (
    IDENTITY_SOURCE,
    PLUGIN_ITEM,
    PLUGIN_SIGNED,
    PLUGIN_WRITE,
    SERVICE_KEY,
)

pytestmark = pytest.mark.area("identity")

CACHE_SECONDS = 1.1


@pytest.fixture
def identity(start):
    running = start("identity")
    running.other.state.down = False
    running.other.state.service_key = SERVICE_KEY
    yield running
    running.other.state.down = False
    running.other.state.service_key = SERVICE_KEY


def session_there(running, login, password):
    answer = httpx.post(f"{running.other.base_url}/api/sessions",
                        json={"login": login, "secret": password},
                        headers={"X-AS-Key": SERVICE_KEY})
    assert answer.status_code == 200, answer.text
    return answer.json()["session"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def local_admin_named(name, email=None):
    uid = dependencies.save_user_to_db(name, audit_actors.ACTOR_PASSWORD)
    dependencies.update_user(uid, status="active", email=email)
    user_roles.set_roles(uid, [ROLE_ADMIN], checked=False)
    return uid


# --- whose account a foreign token opens ------------------------------------------------

def test_a_foreign_token_does_not_take_over_a_local_account_by_name(identity):
    """A local administrator called like somebody in the other system stays theirs."""
    existing = dependencies.get_user_by_username("anna")
    victim = existing["id"] if existing and not existing.get("auth_source") \
        else local_admin_named("anna")
    token = session_there(identity, "anna", "anna-as-pass")
    me = identity.client.get("/api/auth/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["id"] != victim
    assert identity.client.get("/api/admin/users", headers=bearer(token)).status_code == 403


def test_a_foreign_token_does_not_take_over_a_local_account_by_address(identity):
    victim = local_admin_named(f"mail-owner-{time.time_ns()}", email="anna@as.example")
    token = session_there(identity, "anna", "anna-as-pass")
    assert identity.client.get("/api/auth/me", headers=bearer(token)).json()["id"] != victim


def test_the_account_of_a_foreign_person_has_no_password_here(identity):
    token = session_there(identity, "anna", "anna-as-pass")
    username = identity.client.get("/api/auth/me", headers=bearer(token)).json()["username"]
    row = DatabaseManagerV2.execute_one(
        "SELECT password_hash, auth_source FROM users WHERE username = :n", {"n": username})
    assert row["auth_source"] == IDENTITY_SOURCE
    assert not row["password_hash"].startswith("$2")


# --- the door closes when the provider cannot be asked -------------------------------------

def test_a_provider_that_is_down_is_a_503_never_a_200(identity):
    token = session_there(identity, "anna", "anna-as-pass")
    time.sleep(CACHE_SECONDS)
    identity.other.state.down = True
    before = list(identity.plugin_calls())
    for method, path in (("GET", "/api/auth/me"), ("GET", PLUGIN_SIGNED),
                         ("GET", PLUGIN_ITEM.replace("{item_id}", "1"))):
        assert identity.client.request(method, path, headers=bearer(token)).status_code == 503
    assert identity.plugin_calls() == before


def test_a_provider_that_refuses_the_service_key_is_a_503(identity):
    """A rotated key is the deployment's problem, not a reason to let anybody in."""
    token = session_there(identity, "anna", "anna-as-pass")
    time.sleep(CACHE_SECONDS)
    identity.other.state.service_key = "rotated-elsewhere"
    assert identity.client.get("/api/auth/me", headers=bearer(token)).status_code == 503


def test_the_provider_decides_the_right_and_its_refusal_holds(identity):
    anna = session_there(identity, "anna", "anna-as-pass")      # things.read only
    before = list(identity.plugin_calls())
    answer = identity.client.get(PLUGIN_ITEM.replace("{item_id}", "1"), headers=bearer(anna))
    assert answer.status_code == 403
    assert identity.plugin_calls() == before


# --- a foreign token stays foreign ---------------------------------------------------------

def test_a_foreign_token_is_not_traded_for_a_session_here(identity):
    token = session_there(identity, "anna", "anna-as-pass")
    for door in ("/api/auth/refresh", "/api/auth/session"):
        answer = identity.client.post(door, headers=bearer(token))
        assert answer.status_code == 400, door
        assert "access_token" not in answer.text
        assert panel_session.SESSION_COOKIE not in answer.headers.get("set-cookie", "")


def test_a_foreign_token_in_the_cookie_does_not_pass_the_csrf_check(identity):
    """Planted as the session cookie, it cannot bring the CSRF value a change needs."""
    token = session_there(identity, "anna", "anna-as-pass")
    headers = {"Cookie": f"{panel_session.SESSION_COOKIE}={token}; "
                         f"{panel_session.CSRF_COOKIE}=anything",
               panel_session.CSRF_HEADER: "anything"}
    answer = identity.client.post(PLUGIN_WRITE, headers=headers, json={})
    assert answer.status_code == 403


def test_a_revoked_session_there_ends_access_here_within_the_cache(identity):
    token = session_there(identity, "anna", "anna-as-pass")
    assert identity.client.get("/api/auth/me", headers=bearer(token)).status_code == 200
    httpx.delete(f"{identity.other.base_url}/control/sessions/{token}")
    time.sleep(CACHE_SECONDS)
    assert identity.client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_a_right_taken_away_there_is_taken_away_here(identity):
    token = session_there(identity, "boris", "boris-as-pass")
    assert identity.client.get("/api/admin/users", headers=bearer(token)).status_code == 200
    httpx.put(f"{identity.other.base_url}/control/people/boris/groups", json=["AS_OPERATORS"])
    try:
        time.sleep(CACHE_SECONDS)
        assert identity.client.get("/api/admin/users", headers=bearer(token)).status_code == 403
    finally:
        httpx.put(f"{identity.other.base_url}/control/people/boris/groups",
                  json=["AS_ADMINISTRATORS", "AS_OPERATORS"])


def test_a_blocked_account_is_refused_though_the_provider_vouches(identity):
    token = session_there(identity, "boris", "boris-as-pass")
    me = identity.client.get("/api/auth/me", headers=bearer(token)).json()
    dependencies.update_user(me["id"], status="blocked")
    try:
        assert identity.client.get("/api/auth/me", headers=bearer(token)).status_code == 403
    finally:
        dependencies.update_user(me["id"], status="active")


# --- the other system's password ------------------------------------------------------------

def test_guessing_the_other_system_s_password_hits_the_same_limit(identity, monkeypatch):
    monkeypatch.setenv(login_throttle.MAX_ATTEMPTS_ENV, "5")
    DatabaseManagerV2.execute_commit("DELETE FROM login_attempts")
    try:
        codes = [identity.client.post("/api/auth/login", json={
            "username": "boris", "password": f"guess-{i}"}).status_code for i in range(8)]
    finally:
        DatabaseManagerV2.execute_commit("DELETE FROM login_attempts")
    assert codes.count(401) == 5 and codes.count(429) == 3, codes


# --- a deployment that lost its secret does not come up ---------------------------------------

def test_a_provider_without_its_secret_stops_the_start(tmp_path, monkeypatch, the_other_system):
    from keepup.auth.identity import IdentityProviderMisconfigured
    from audit_profiles import SERVICE_KEY_ENV, _write_files, by_name, settings_for
    from keepup.factory import create_app

    auth_yaml, plugins, modules = _write_files(by_name("identity"), tmp_path, the_other_system)
    monkeypatch.setenv("AUTH_CONFIG_PATH", str(auth_yaml))
    monkeypatch.delenv(SERVICE_KEY_ENV, raising=False)
    with pytest.raises(IdentityProviderMisconfigured) as refused:
        create_app(settings_for(by_name("identity"), plugins, modules))
    assert SERVICE_KEY_ENV in str(refused.value)


def test_a_plugin_named_wrong_stops_the_start_and_says_nothing_of_the_settings(tmp_path,
                                                                              monkeypatch):
    from keepup.auth.identity import IdentityProviderMisconfigured
    from keepup.factory import create_app
    from keepup.settings import KeepupSettings

    config = tmp_path / "auth.yaml"
    config.write_text(
        "identity_provider:\n  name: x\n  plugin: keepup.settings:KeepupSettings\n"
        "  settings:\n    key: secret-value-94\n", encoding="utf-8")
    monkeypatch.setenv("AUTH_CONFIG_PATH", str(config))
    with pytest.raises(IdentityProviderMisconfigured) as refused:
        create_app(KeepupSettings(static_mounts=(), plugin_manager=None))
    assert "secret-value-94" not in str(refused.value)
