"""Tokens and passwords of another system, taken through the provider (keepup-91).

Against the session's throwaway SQLite database, through the real routes of the
framework, with a provider that answers from memory. The provider of a real
system is exercised end to end in identity_provider_integration_tests.py; here
each rule is pinned on its own: which tokens reach the provider at all, what is
cached and for how long, which account a vouched-for identity becomes, and what
answer each failure gives.

    python3 -m pytest keepup/tests/identity_tokens_tests.py -v
"""

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from keepup.auth import dependencies, login_throttle, user_roles
from keepup.auth.dependencies import get_current_user, get_optional_user
from keepup.auth.identity import runtime as identity_runtime
from keepup.auth.identity.config import IdentityProviderConfig
from keepup.auth.identity.contract import (
    ExternalIdentity,
    IdentityProvider,
    IdentityRejected,
    ProviderUnavailable,
)
from keepup.auth.identity.runtime import IdentityRuntime, _Cache, fingerprint
from keepup.auth.routes import register_auth_routes
from keepup.auth.signing_key import resolve_signing_key
from keepup.db import DatabaseManagerV2
from keepup.roles import ROLE_ADMIN, ROLE_CLIENT
from keepup.schema import init_db

SOURCE = "memory-as"
PASSWORD = "a-long-password-91"


class MemoryProvider(IdentityProvider):
    """A provider that knows its tokens and passwords by heart, and counts the questions."""

    def __init__(self, settings=None):
        super().__init__(settings or {})
        self.tokens = {}
        self.passwords = {}
        self.asked = []
        #: None, "down", "slow" or "broken".
        self.state = None

    async def _answer(self, what, key, table):
        self.asked.append((what, key))
        if self.state == "down":
            raise ProviderUnavailable("connection refused")
        if self.state == "slow":
            await asyncio.sleep(5)
        if self.state == "broken":
            raise KeyError("a token in the text: " + str(key))
        if key not in table:
            raise IdentityRejected("unknown")
        return table[key]

    async def verify_token(self, token):
        return await self._answer("token", token, self.tokens)

    async def verify_password(self, username, password):
        return await self._answer("password", (username, password), self.passwords)


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


@pytest.fixture
def provider():
    return MemoryProvider()


def install(provider, **overrides):
    options = dict(name=SOURCE, plugin=provider, new_accounts="create",
                   role_mapping={"as-admins": ROLE_ADMIN, "as-users": ROLE_CLIENT},
                   timeout_seconds=0.2)
    options.update(overrides)
    runtime = IdentityRuntime(IdentityProviderConfig(**options), provider)
    identity_runtime.install(runtime)
    return runtime


@pytest.fixture(autouse=True)
def no_provider_left_behind(monkeypatch):
    monkeypatch.setenv(login_throttle.MAX_ATTEMPTS_ENV, "5")
    DatabaseManagerV2.execute_commit("DELETE FROM login_attempts")
    yield
    identity_runtime.install(None)


@pytest.fixture
def client():
    app = FastAPI()
    register_auth_routes(app, SimpleNamespace(plugins={}))

    @app.get("/whoami")
    async def whoami(user: dict = Depends(get_current_user)):
        return {"id": user["id"], "username": user["username"], "roles": user["roles"],
                "authenticated_by": user.get("authenticated_by"),
                "session_id": user.get("session_id")}

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def subject():
    return f"subject-{uuid.uuid4().hex[:10]}"


def issue(provider, identity):
    token = f"foreign-{uuid.uuid4().hex}"
    provider.tokens[token] = identity
    return token


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def account_of(sub):
    return DatabaseManagerV2.execute_one(
        "SELECT id, username, status FROM users WHERE auth_source = :s AND external_id = :x",
        {"s": SOURCE, "x": sub})


def local_account(name, status="active"):
    if not dependencies.get_user_by_username(name):
        uid = dependencies.save_user_to_db(name, PASSWORD)
        dependencies.update_user(uid, status=status)
    return dependencies.get_user_by_username(name)


# --- which tokens reach the provider ------------------------------------------------

def test_without_a_provider_a_foreign_token_is_401(client):
    assert client.get("/whoami", headers=bearer("foreign-anything")).status_code == 401


def test_a_token_of_the_provider_signs_the_caller_in(client, provider):
    install(provider)
    sub = subject()
    token = issue(provider, ExternalIdentity(subject=sub, username="ivan", roles=("as-users",)))

    answer = client.get("/whoami", headers=bearer(token))

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["id"] == account_of(sub)["id"]
    assert body["authenticated_by"] == SOURCE
    assert body["roles"] == [ROLE_CLIENT]
    # No session of this framework stands behind it.
    assert body["session_id"] is None


def test_tokens_off_means_the_provider_is_not_asked(client, provider):
    install(provider, accept_tokens=False, password_sign_in=True)
    token = issue(provider, ExternalIdentity(subject=subject()))
    assert client.get("/whoami", headers=bearer(token)).status_code == 401
    assert provider.asked == []


def test_an_expired_own_token_never_reaches_the_provider(client, provider):
    install(provider)
    ours = jwt.encode({"sub": "somebody", "sid": "x",
                       "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
                      resolve_signing_key(), algorithm="HS256")
    assert client.get("/whoami", headers=bearer(ours)).status_code == 401
    assert provider.asked == []


def test_an_own_token_without_a_session_never_reaches_the_provider(client, provider):
    install(provider)
    ours = jwt.encode({"sub": "somebody",
                       "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
                      resolve_signing_key(), algorithm="HS256")
    assert client.get("/whoami", headers=bearer(ours)).status_code == 401
    assert provider.asked == []


def test_a_jwt_signed_by_somebody_else_does_reach_the_provider(client, provider):
    install(provider)
    theirs = jwt.encode({"sub": "x", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
                        "a-key-this-framework-does-not-have", algorithm="HS256")
    provider.tokens[theirs] = ExternalIdentity(subject=subject())
    assert client.get("/whoami", headers=bearer(theirs)).status_code == 200


def test_an_identity_without_a_subject_is_refused(client, provider):
    install(provider)
    token = issue(provider, ExternalIdentity(subject=""))
    assert client.get("/whoami", headers=bearer(token)).status_code == 401

# --- the account behind the identity ------------------------------------------------

def test_create_policy_makes_a_shadow_account(client, provider):
    install(provider)
    sub = subject()
    token = issue(provider, ExternalIdentity(subject=sub, username="Petr.Ivanov",
                                             email="p@corp.example", roles=("as-admins",)))
    assert client.get("/whoami", headers=bearer(token)).status_code == 200

    row = DatabaseManagerV2.execute_one(
        "SELECT username, email, password_hash, status FROM users "
        "WHERE auth_source = :s AND external_id = :x", {"s": SOURCE, "x": sub})
    assert row["username"].startswith("petr.ivanov")
    assert row["email"] == "p@corp.example"
    assert row["status"] == "active"
    # No password here: nothing typed into the sign-in form matches it.
    assert not row["password_hash"].startswith("$2")
    assert user_roles.roles_of(account_of(sub)["id"]) == [ROLE_ADMIN]


def test_refuse_policy_answers_401_for_an_unknown_subject(client, provider):
    install(provider, new_accounts="refuse")
    sub = subject()
    token = issue(provider, ExternalIdentity(subject=sub))
    assert client.get("/whoami", headers=bearer(token)).status_code == 401
    assert account_of(sub) is None


def test_refuse_policy_admits_a_subject_that_has_an_account(client, provider):
    install(provider)
    sub = subject()
    client.get("/whoami", headers=bearer(issue(provider, ExternalIdentity(subject=sub))))
    install(provider, new_accounts="refuse")
    assert client.get("/whoami", headers=bearer(
        issue(provider, ExternalIdentity(subject=sub)))).status_code == 200


def test_the_account_is_found_by_source_and_subject_not_by_name(client, provider):
    install(provider)
    local = local_account("shared-name-91")
    sub = subject()
    token = issue(provider, ExternalIdentity(subject=sub, username="shared-name-91"))

    body = client.get("/whoami", headers=bearer(token)).json()

    assert body["id"] != local["id"]
    assert body["username"] != "shared-name-91"
    assert body["id"] == account_of(sub)["id"]


def test_a_blocked_account_is_403_after_the_provider_confirms(client, provider):
    install(provider)
    sub = subject()
    token = issue(provider, ExternalIdentity(subject=sub))
    assert client.get("/whoami", headers=bearer(token)).status_code == 200
    dependencies.update_user(account_of(sub)["id"], status="blocked")
    assert client.get("/whoami", headers=bearer(token)).status_code == 403


def test_roles_follow_the_identity_on_every_request(client, provider):
    install(provider, token_cache_seconds=0)
    sub = subject()
    admin = issue(provider, ExternalIdentity(subject=sub, roles=("as-admins", "as-users")))
    assert client.get("/whoami", headers=bearer(admin)).json()["roles"] == [ROLE_ADMIN, ROLE_CLIENT]

    plain = issue(provider, ExternalIdentity(subject=sub, roles=("as-users",)))
    assert client.get("/whoami", headers=bearer(plain)).json()["roles"] == [ROLE_CLIENT]
    assert user_roles.roles_of(account_of(sub)["id"]) == [ROLE_CLIENT]

    nothing = issue(provider, ExternalIdentity(subject=sub, roles=("unmapped",)))
    assert client.get("/whoami", headers=bearer(nothing)).json()["roles"] == [ROLE_CLIENT]


def test_roles_are_written_only_when_they_change(client, provider, monkeypatch):
    install(provider)
    sub = subject()
    token = issue(provider, ExternalIdentity(subject=sub, roles=("as-admins",)))
    client.get("/whoami", headers=bearer(token))

    writes = []
    original = user_roles.set_roles
    monkeypatch.setattr(user_roles, "set_roles",
                        lambda *a, **k: writes.append(a) or original(*a, **k))
    for _ in range(3):
        assert client.get("/whoami", headers=bearer(token)).status_code == 200
    assert writes == []

    client.get("/whoami", headers=bearer(issue(provider, ExternalIdentity(subject=sub))))
    assert len(writes) == 1

# --- the cache ------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def runtime_with_clock(provider, **overrides):
    clock = Clock()
    options = dict(name=SOURCE, plugin=provider, timeout_seconds=0.2)
    options.update(overrides)
    return IdentityRuntime(IdentityProviderConfig(**options), provider, clock=clock), clock


def test_a_confirmed_token_is_asked_once_within_the_cache(provider):
    runtime, clock = runtime_with_clock(provider, token_cache_seconds=60)
    token = issue(provider, ExternalIdentity(subject="s"))
    for _ in range(3):
        asyncio.run(runtime.identity_for_token(token))
    assert len(provider.asked) == 1

    clock.now += 61
    asyncio.run(runtime.identity_for_token(token))
    assert len(provider.asked) == 2


def test_no_cache_asks_every_time(provider):
    runtime, _ = runtime_with_clock(provider, token_cache_seconds=0)
    token = issue(provider, ExternalIdentity(subject="s"))
    for _ in range(3):
        asyncio.run(runtime.identity_for_token(token))
    assert len(provider.asked) == 3


def test_the_cache_never_outlives_the_identity(provider):
    runtime, clock = runtime_with_clock(provider, token_cache_seconds=600)
    token = issue(provider, ExternalIdentity(
        subject="s", expires_at=datetime.now(timezone.utc) + timedelta(seconds=30)))
    asyncio.run(runtime.identity_for_token(token))
    deadline, _ = runtime.tokens.get(fingerprint(token))
    assert deadline <= clock.now + 30


def test_an_identity_already_expired_is_refused(provider):
    runtime, _ = runtime_with_clock(provider)
    token = issue(provider, ExternalIdentity(
        subject="s", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    with pytest.raises(IdentityRejected):
        asyncio.run(runtime.identity_for_token(token))


def test_a_refusal_is_cached_briefly(provider):
    runtime, clock = runtime_with_clock(provider, token_cache_seconds=600)
    for _ in range(3):
        with pytest.raises(IdentityRejected):
            asyncio.run(runtime.identity_for_token("garbage"))
    assert len(provider.asked) == 1

    clock.now += 6
    with pytest.raises(IdentityRejected):
        asyncio.run(runtime.identity_for_token("garbage"))
    assert len(provider.asked) == 2


def test_an_unavailable_answer_is_not_cached(provider):
    runtime, _ = runtime_with_clock(provider, token_cache_seconds=600)
    provider.state = "down"
    for _ in range(2):
        with pytest.raises(ProviderUnavailable):
            asyncio.run(runtime.identity_for_token("t"))
    assert len(provider.asked) == 2


def test_the_cache_is_bounded():
    clock = Clock()
    cache = _Cache(clock, size=3)
    for key in "abcd":
        cache.put(key, key, 60)
    assert len(cache) == 3
    assert cache.get("a") is None
    assert cache.get("d") is not None


def test_the_cache_keeps_no_token(provider):
    runtime, _ = runtime_with_clock(provider, token_cache_seconds=60)
    token = issue(provider, ExternalIdentity(subject="s"))
    asyncio.run(runtime.identity_for_token(token))
    assert runtime.tokens.keys() == [hashlib.sha256(token.encode()).hexdigest()]
    assert token not in repr(runtime.tokens._entries)

# --- failures -------------------------------------------------------------------------

def test_the_provider_down_is_503(client, provider):
    install(provider)
    provider.state = "down"
    answer = client.get("/whoami", headers=bearer("foreign-x"))
    assert answer.status_code == 503
    assert answer.json()["detail"] == "The identity provider is unavailable"


def test_a_timeout_is_503(client, provider):
    install(provider, timeout_seconds=0.05)
    provider.state = "slow"
    assert client.get("/whoami", headers=bearer("foreign-x")).status_code == 503


def test_an_unexpected_error_of_the_plugin_is_503_and_its_text_goes_nowhere(client, provider,
                                                                               caplog):
    install(provider)
    provider.state = "broken"
    token = "foreign-secret-in-an-exception"
    answer = client.get("/whoami", headers=bearer(token))
    assert answer.status_code == 503
    assert token not in answer.text
    assert token not in caplog.text


def test_every_refusal_answers_the_same(client, provider):
    install(provider, new_accounts="refuse")
    unknown_token = client.get("/whoami", headers=bearer("foreign-never-issued"))
    unknown_subject = client.get("/whoami", headers=bearer(
        issue(provider, ExternalIdentity(subject=subject()))))
    no_subject = client.get("/whoami", headers=bearer(
        issue(provider, ExternalIdentity(subject=" "))))
    answers = {(a.status_code, a.text) for a in (unknown_token, unknown_subject, no_subject)}
    assert len(answers) == 1
    assert answers.pop()[0] == 401


def test_the_optional_user_is_none_when_the_provider_is_down(provider):
    install(provider)
    provider.state = "down"
    assert asyncio.run(get_optional_user("foreign-x")) is None


def test_the_optional_user_of_a_good_token(provider):
    install(provider)
    token = issue(provider, ExternalIdentity(subject=subject()))
    user = asyncio.run(get_optional_user(token))
    assert user["authenticated_by"] == SOURCE

# --- no session of ours for a token of theirs -----------------------------------------

def test_refresh_is_refused_for_a_foreign_token(client, provider):
    install(provider)
    token = issue(provider, ExternalIdentity(subject=subject()))
    answer = client.post("/api/auth/refresh", headers=bearer(token))
    assert answer.status_code == 400
    assert SOURCE in answer.json()["detail"]
    assert "access_token" not in answer.text


def test_session_exchange_is_refused_for_a_foreign_token(client, provider):
    install(provider)
    token = issue(provider, ExternalIdentity(subject=subject()))
    answer = client.post("/api/auth/session", headers=bearer(token))
    assert answer.status_code == 400
    assert "set-cookie" not in answer.headers


# --- signing in with the other system's password --------------------------------------

def sign_in(client, name, password):
    return client.post("/api/auth/login", json={"username": name, "password": password})


def test_the_other_systems_password_opens_a_session_of_ours(client, provider):
    install(provider, password_sign_in=True)
    sub = subject()
    provider.passwords[("olga", "her-as-password")] = ExternalIdentity(subject=sub,
                                                                       username="olga")
    answer = sign_in(client, "olga", "her-as-password")
    assert answer.status_code == 200, answer.text

    # A session of this framework, like any other sign-in -- with its own token.
    token = answer.json()["access_token"]
    body = client.get("/whoami", headers=bearer(token)).json()
    assert body["id"] == account_of(sub)["id"]
    assert body["session_id"]
    assert body["authenticated_by"] is None


def test_a_wrong_password_is_the_same_401(client, provider):
    install(provider, password_sign_in=True)
    local_account("local-91")
    via_provider = sign_in(client, "somebody-there", "wrong")
    local = sign_in(client, "local-91", "wrong")
    assert via_provider.status_code == local.status_code == 401
    assert via_provider.json() == local.json()


def test_a_local_account_signs_in_without_asking_the_provider(client, provider):
    install(provider, password_sign_in=True)
    local_account("local-admin-91")
    provider.state = "down"
    assert sign_in(client, "local-admin-91", PASSWORD).status_code == 200
    assert provider.asked == []


def test_password_sign_in_off_never_asks_the_provider(client, provider):
    install(provider)
    assert sign_in(client, "somebody-there", "anything").status_code == 401
    assert provider.asked == []


def test_password_sign_in_with_the_provider_down_is_503(client, provider):
    install(provider, password_sign_in=True)
    provider.state = "down"
    assert sign_in(client, "somebody-there", "anything").status_code == 503


def test_the_throttle_covers_the_provider_path(client, provider):
    install(provider, password_sign_in=True)
    codes = [sign_in(client, "guessed-there", "wrong").status_code for _ in range(8)]
    assert codes.count(401) == 5
    assert codes.count(429) == 3
    assert len(provider.asked) == 5


def test_an_account_of_an_outside_identity_has_no_local_password(client, provider):
    """Its password column is not a hash: the form must answer no, not fail."""
    install(provider)
    sub = subject()
    client.get("/whoami", headers=bearer(issue(provider, ExternalIdentity(subject=sub))))
    identity_runtime.install(None)
    assert sign_in(client, account_of(sub)["username"], "!external").status_code == 401
