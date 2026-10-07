"""A right, and who decides it (keepup-91).

A plugin route declares ``permission``, an application route depends on
``require_permission``, and the old decorator asks too; all three end in one
check. Here each mode is pinned through real registered routes: ``local`` (and
no provider at all), and ``provider``, where the other system decides and its
silence closes the door.

    python3 -m pytest keepup/tests/identity_access_tests.py -v
"""

import asyncio
import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from keepup.auth import dependencies, user_roles
from keepup.auth.identity import require_permission
from keepup.auth.identity import runtime as identity_runtime
from keepup.auth.identity.config import IdentityProviderConfig
from keepup.auth.identity.contract import (
    ExternalIdentity,
    IdentityProvider,
    IdentityRejected,
    ProviderUnavailable,
)
from keepup.auth.identity.runtime import IdentityRuntime
from keepup.auth.permissions import require_permission as old_decorator
from keepup.db import DatabaseManagerV2
from keepup.plugins.routes import register_plugin_routes
from keepup.roles import ROLE_ADMIN, ROLE_CLIENT
from keepup.schema import init_db

SOURCE = "deciding-as"


class DecidingProvider(IdentityProvider):
    """Vouches for its tokens and decides by a table of (subject, right)."""

    def __init__(self, settings=None):
        super().__init__(settings or {})
        self.tokens = {}
        self.allowed = set()
        self.asked = []
        self.state = None

    async def verify_token(self, token):
        if token not in self.tokens:
            raise IdentityRejected("unknown")
        return self.tokens[token]

    async def decide(self, identity, user, request):
        self.asked.append((identity, dict(user), request))
        if self.state == "down":
            raise ProviderUnavailable("down")
        if self.state == "slow":
            await asyncio.sleep(5)
        who = identity.subject if identity else f"local:{user['username']}"
        return (who, request.permission) in self.allowed


class Things:
    """A plugin's routes, as a plugin returns them."""

    def __init__(self):
        self.calls = []

    async def read(self, thing_id: str = None, current_user: dict = None):
        self.calls.append(thing_id)
        return {"thing": thing_id}

    def routes(self):
        return [{"path": "/api/things/{thing_id}", "methods": ["GET"], "handler": self.read,
                 "permission": "things.read"}]


class Manager:
    def __init__(self, routes):
        self._routes = routes

    def get_all_api_routes(self):
        return self._routes

    def get_all_websocket_routes(self):
        return []


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


@pytest.fixture(autouse=True)
def no_provider_left_behind():
    yield
    identity_runtime.install(None)


@pytest.fixture
def provider():
    return DecidingProvider()


@pytest.fixture
def things():
    return Things()


@pytest.fixture
def client(things):
    app = FastAPI()
    asyncio.run(register_plugin_routes(app, Manager(things.routes())))

    @app.get("/api/reports")
    async def reports(user: dict = Depends(require_permission("reports.read"))):
        return {"user": user["username"]}

    @app.get("/api/legacy")
    async def legacy(current_user: dict = Depends(dependencies.get_current_user)):
        @old_decorator("legacy.read")
        async def inner(current_user=None):
            return {"ok": True}
        return await inner(current_user=current_user)

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def install(provider, **overrides):
    options = dict(name=SOURCE, plugin=provider, new_accounts="create",
                   authorization="provider",
                   role_mapping={"as-admins": ROLE_ADMIN}, timeout_seconds=0.2)
    options.update(overrides)
    runtime = IdentityRuntime(IdentityProviderConfig(**options), provider)
    identity_runtime.install(runtime)
    return runtime


def foreign(provider, **identity):
    identity.setdefault("subject", f"s-{uuid.uuid4().hex[:8]}")
    token = f"foreign-{uuid.uuid4().hex}"
    provider.tokens[token] = ExternalIdentity(**identity)
    return {"Authorization": f"Bearer {token}"}, identity["subject"]


def local(roles=(ROLE_CLIENT,)):
    """A local account with a live session, and its bearer header."""
    name = f"local-{uuid.uuid4().hex[:8]}"
    uid = dependencies.save_user_to_db(name, "a-long-password-91")
    dependencies.update_user(uid, status="active")
    user_roles.set_roles(uid, list(roles), checked=False)
    issued = dependencies.issue_session_token(uid, name)
    return {"Authorization": f"Bearer {issued['access_token']}"}, uid, name


# --- provider mode ---------------------------------------------------------------------

def test_provider_mode_allows(client, provider, things):
    install(provider)
    headers, sub = foreign(provider)
    provider.allowed.add((sub, "things.read"))
    answer = client.get("/api/things/7", headers=headers)
    assert answer.status_code == 200, answer.text
    assert things.calls == ["7"]


def test_provider_mode_refuses_with_403(client, provider, things):
    install(provider)
    headers, _ = foreign(provider)
    answer = client.get("/api/things/7", headers=headers)
    assert answer.status_code == 403
    assert answer.json()["detail"] == "Permission 'things.read' required"
    assert things.calls == []


def test_provider_silence_is_503_and_the_handler_is_not_called(client, provider, things):
    install(provider, timeout_seconds=0.05)
    headers, sub = foreign(provider)
    provider.allowed.add((sub, "things.read"))
    provider.state = "slow"
    assert client.get("/api/things/7", headers=headers).status_code == 503
    provider.state = "down"
    assert client.get("/api/things/7", headers=headers).status_code == 503
    assert things.calls == []


def test_the_decision_receives_the_action(client, provider):
    install(provider)
    headers, sub = foreign(provider, permissions=("x",))
    client.get("/api/things/42", headers=headers)
    identity, user, request = provider.asked[-1]
    assert identity.subject == sub
    assert identity.permissions == ("x",)
    assert user["authenticated_by"] == SOURCE
    assert (request.permission, request.method, request.path, dict(request.path_params)) == \
        ("things.read", "GET", "/api/things/{thing_id}", {"thing_id": "42"})


def test_a_local_account_is_decided_by_the_provider_too(client, provider):
    install(provider)
    headers, _, name = local()
    assert client.get("/api/things/1", headers=headers).status_code == 403
    identity, user, _ = provider.asked[-1]
    assert identity is None and user["username"] == name
    provider.allowed.add((f"local:{name}", "things.read"))
    assert client.get("/api/things/1", headers=headers).status_code == 200


def test_decisions_are_not_cached_by_default(client, provider):
    install(provider)
    headers, sub = foreign(provider)
    provider.allowed.add((sub, "things.read"))
    for _ in range(3):
        client.get("/api/things/1", headers=headers)
    assert len(provider.asked) == 3


def test_decisions_are_cached_when_asked(client, provider):
    install(provider, decision_cache_seconds=60)
    headers, sub = foreign(provider)
    provider.allowed.add((sub, "things.read"))
    for _ in range(3):
        assert client.get("/api/things/1", headers=headers).status_code == 200
    assert len(provider.asked) == 1


# --- local mode ------------------------------------------------------------------------

def test_local_mode_admin(client, provider):
    install(provider, authorization="local")
    headers, _ = foreign(provider, roles=("as-admins",))
    assert client.get("/api/things/1", headers=headers).status_code == 200
    assert provider.asked == []


def test_local_mode_granted_row(client):
    headers, uid, _ = local()
    assert client.get("/api/things/1", headers=headers).status_code == 403
    DatabaseManagerV2.execute_commit(
        "INSERT INTO user_permissions (user_id, permission_name, granted) "
        "VALUES (:u, 'things.read', :g)", {"u": uid, "g": True})
    assert client.get("/api/things/1", headers=headers).status_code == 200


def test_local_mode_identity_permission(client, provider):
    install(provider, authorization="local")
    headers, _ = foreign(provider, permissions=("things.read",))
    assert client.get("/api/things/1", headers=headers).status_code == 200


def test_local_mode_nothing_is_403(client, provider, things):
    install(provider, authorization="local")
    headers, _ = foreign(provider, permissions=("other.right",))
    assert client.get("/api/things/1", headers=headers).status_code == 403
    assert things.calls == []


def test_without_a_provider_an_administrator_holds_every_right(client):
    headers, _, _ = local(roles=(ROLE_ADMIN,))
    assert client.get("/api/things/1", headers=headers).status_code == 200


def test_a_right_needs_somebody_signed_in(client):
    assert client.get("/api/things/1").status_code == 401


# --- registration ------------------------------------------------------------------------

async def _handler(current_user: dict = None):
    return {}


@pytest.mark.parametrize("route", [
    {"path": "/api/open", "methods": ["GET"], "handler": _handler,
     "require_auth": False, "permission": "x"},
    {"path": "/api/raw", "methods": ["POST"], "handler": _handler,
     "raw_request": True, "permission": "x"},
    {"path": "/api/blank", "methods": ["GET"], "handler": _handler, "permission": " "},
])
def test_a_permission_the_framework_cannot_check_stops_registration(route):
    with pytest.raises(ValueError, match="permission"):
        asyncio.run(register_plugin_routes(FastAPI(), Manager([route])))


# --- the dependency and the old decorator --------------------------------------------------

def test_require_permission_dependency(client, provider):
    install(provider)
    headers, sub = foreign(provider)
    assert client.get("/api/reports", headers=headers).status_code == 403
    provider.allowed.add((sub, "reports.read"))
    assert client.get("/api/reports", headers=headers).status_code == 200
    assert provider.asked[-1][2].path == "/api/reports"


def test_the_old_decorator_uses_the_same_rule(client):
    """It read a permissions field nothing filled in, and refused administrators too."""
    admin, _, _ = local(roles=(ROLE_ADMIN,))
    assert client.get("/api/legacy", headers=admin).status_code == 200
    plain, _, _ = local()
    assert client.get("/api/legacy", headers=plain).status_code == 403
