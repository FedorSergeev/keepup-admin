"""keepup inside somebody else's system, end to end (keepup-91).

The homegrown system (tests/homegrown_as/service.py) runs as a real HTTP server
on a free port. Its provider plugin is named in an auth.yaml the way a configmap
would name it, with the service key taken from the environment. The application
is assembled by create_app() and started with its lifespan, with a real plugin
loaded from a plugins directory -- one route guarded by a right, one socket that
signs in. From there every request goes the way it would on a stand: a token of
the other system, over HTTP, to the application, which asks the other system over
HTTP again.

    python3 -m pytest keepup/tests/identity_provider_integration_tests.py -v
"""

import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from keepup.auth import login_throttle
from keepup.db import DatabaseManagerV2
from keepup.factory import create_app
from keepup.plugins.base import PluginManager
from keepup.schema import init_db
from keepup.settings import KeepupSettings
from keepup.tests.homegrown_as.service import build_service

SERVICE_KEY = "homegrown-service-key-for-tests"
SOURCE = "homegrown-as"

PROBE_PLUGIN = '''
from keepup.plugins.base import BasePlugin


class ProbePlugin(BasePlugin):
    def __init__(self, config=None):
        super().__init__("probe", "Probe", config)

    async def initialize(self):
        return True

    def get_handlers(self):
        return {}

    def get_api_routes(self):
        return [
            {"path": "/api/probe/things/{thing_id}", "methods": ["GET"],
             "handler": self.thing, "permission": "things.read"},
            {"path": "/api/probe/me", "methods": ["GET"], "handler": self.me},
        ]

    def get_websocket_routes(self):
        return [{"path": "/ws/probe", "handler": self.socket, "require_auth": True}]

    async def thing(self, thing_id: str = None, current_user: dict = None):
        return {"thing": thing_id, "by": current_user["username"]}

    async def me(self, current_user: dict = None):
        return {"username": current_user["username"], "roles": current_user["roles"],
                "source": current_user.get("authenticated_by"),
                "subject": (current_user.get("identity") or {}).get("subject")}

    async def socket(self, websocket, current_user=None):
        await websocket.accept()
        await websocket.send_json({"user": current_user["username"],
                                   "source": current_user.get("authenticated_by")})
        await websocket.close()
'''


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="module")
def other_system():
    """The homegrown system on a real port, for the whole module."""
    service = build_service(SERVICE_KEY)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(service, host="127.0.0.1", port=port,
                                           log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the homegrown system did not start")
        time.sleep(0.02)
    base = f"http://127.0.0.1:{port}"
    yield base, service.state.as_state
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


def deployment(tmp_path, base_url, **section):
    """The files a stand would carry: auth.yaml (the configmap), modules.json, a plugin."""
    options = {
        "name": SOURCE,
        "plugin": "keepup.tests.homegrown_as.provider:HomegrownProvider",
        "settings": {"base_url": base_url, "service_key": "${HOMEGROWN_AS_KEY}"},
        "accept_tokens": True,
        "password_sign_in": True,
        "authorization": "provider",
        "new_accounts": "create",
        "role_mapping": {"AS_ADMINISTRATORS": "ADMIN", "AS_OPERATORS": "CLIENT"},
        "token_cache_seconds": 1,
        "timeout_seconds": 2,
    }
    options.update(section)
    lines = ["identity_provider:"] + [f"  {key}: {json.dumps(value)}"
                                      for key, value in options.items()]
    (tmp_path / "auth.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    plugins = tmp_path / "plugins"
    plugins.mkdir(exist_ok=True)
    (plugins / "probe.py").write_text(PROBE_PLUGIN, encoding="utf-8")
    modules = tmp_path / "modules.json"
    modules.write_text(json.dumps({"plugins": [
        {"id": "probe", "name": "Probe", "enabled": True, "priority": 1, "config": {}}]}),
        encoding="utf-8")
    return tmp_path / "auth.yaml", plugins, modules


@pytest.fixture
def stand(tmp_path, monkeypatch, other_system):
    """The application, started, against the other system."""
    base, state = other_system
    auth_yaml, plugins, modules = deployment(tmp_path, base)
    monkeypatch.setenv("AUTH_CONFIG_PATH", str(auth_yaml))
    monkeypatch.setenv("HOMEGROWN_AS_KEY", SERVICE_KEY)
    monkeypatch.setenv(login_throttle.MAX_ATTEMPTS_ENV, "20")
    DatabaseManagerV2.execute_commit("DELETE FROM login_attempts")
    state.down = False

    app = create_app(KeepupSettings(
        title="Inside another system", static_mounts=(),
        plugin_manager=PluginManager(plugins_dir=str(plugins)),
        plugins_config_path=str(modules)))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, base, state
    state.down = False


def log_in_there(base, login, password):
    """What a person does in the other system: open a session there."""
    answer = httpx.post(f"{base}/api/sessions", json={"login": login, "secret": password},
                        headers={"X-AS-Key": SERVICE_KEY})
    assert answer.status_code == 200, answer.text
    return answer.json()["session"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# --- the other system on its own -----------------------------------------------------

def test_the_homegrown_system_keeps_its_own_protocol(other_system):
    base, _ = other_system
    assert httpx.post(f"{base}/api/sessions", json={"login": "anna", "secret": "x"},
                      headers={"X-AS-Key": SERVICE_KEY}).status_code == 401
    assert httpx.get(f"{base}/api/sessions/current",
                     headers={"X-AS-Key": "wrong"}).status_code == 403
    token = log_in_there(base, "anna", "anna-as-pass")
    assert token.startswith("hg_")
    who = httpx.get(f"{base}/api/sessions/current",
                    headers={"X-AS-Key": SERVICE_KEY, "X-AS-Session": token}).json()
    assert who["principal"]["uid"] == "u-100"


# --- tokens of the other system ----------------------------------------------------------

def test_the_application_comes_up_with_the_provider_from_the_file(stand):
    client, _, _ = stand
    assert client.get("/api/probe/me").status_code == 401


def test_a_token_of_the_foreign_system_signs_the_caller_in(stand):
    client, base, _ = stand
    token = log_in_there(base, "anna", "anna-as-pass")

    me = client.get("/api/probe/me", headers=bearer(token))
    assert me.status_code == 200, me.text
    assert me.json()["source"] == SOURCE
    assert me.json()["subject"] == "u-100"
    assert me.json()["roles"] == ["CLIENT"]

    # The framework's own routes take it the same way.
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 200


def test_an_administrator_there_is_an_administrator_here(stand):
    client, base, _ = stand
    boris = log_in_there(base, "boris", "boris-as-pass")
    anna = log_in_there(base, "anna", "anna-as-pass")
    assert client.get("/api/admin/plugins", headers=bearer(boris)).status_code == 200
    assert client.get("/api/admin/plugins", headers=bearer(anna)).status_code == 403


def test_a_role_taken_away_there_is_taken_away_here(stand):
    client, base, _ = stand
    token = log_in_there(base, "boris", "boris-as-pass")
    assert client.get("/api/admin/plugins", headers=bearer(token)).status_code == 200

    httpx.put(f"{base}/control/people/boris/groups", json=["AS_OPERATORS"])
    try:
        time.sleep(1.1)  # the configured token cache
        assert client.get("/api/admin/plugins", headers=bearer(token)).status_code == 403
    finally:
        httpx.put(f"{base}/control/people/boris/groups",
                  json=["AS_ADMINISTRATORS", "AS_OPERATORS"])


def test_a_revoked_token_stops_working_after_the_cache(stand):
    client, base, state = stand
    token = log_in_there(base, "anna", "anna-as-pass")
    before = state.introspections
    for _ in range(3):
        assert client.get("/api/probe/me", headers=bearer(token)).status_code == 200
    assert state.introspections - before == 1

    httpx.delete(f"{base}/control/sessions/{token}")
    time.sleep(1.1)
    assert client.get("/api/probe/me", headers=bearer(token)).status_code == 401


def test_the_foreign_system_down_is_503_not_401(stand):
    client, base, state = stand
    token = log_in_there(base, "anna", "anna-as-pass")
    time.sleep(1.1)
    state.down = True
    answer = client.get("/api/probe/me", headers=bearer(token))
    assert answer.status_code == 503
    assert answer.json()["detail"] == "The identity provider is unavailable"


def test_a_token_it_never_issued_is_401_without_asking_it(stand):
    client, _, state = stand
    before = state.introspections
    assert client.get("/api/probe/me", headers=bearer("not-a-session-there")).status_code == 401
    assert state.introspections == before


def test_refresh_with_a_foreign_token_is_refused(stand):
    client, base, _ = stand
    token = log_in_there(base, "anna", "anna-as-pass")
    answer = client.post("/api/auth/refresh", headers=bearer(token))
    assert answer.status_code == 400
    assert "renew it there" in answer.json()["detail"]


def test_a_socket_signs_in_with_a_foreign_token(stand):
    client, base, _ = stand
    token = log_in_there(base, "anna", "anna-as-pass")
    with client.websocket_connect(f"/ws/probe?token={token}") as websocket:
        greeting = websocket.receive_json()
    assert greeting["source"] == SOURCE


# --- rights the other system decides -------------------------------------------------------

def test_a_plugin_route_asks_the_foreign_system(stand):
    client, base, state = stand
    anna = log_in_there(base, "anna", "anna-as-pass")      # things.read on everything
    boris = log_in_there(base, "boris", "boris-as-pass")   # things.read on 7 only

    before = state.decisions
    assert client.get("/api/probe/things/3", headers=bearer(anna)).status_code == 200
    assert client.get("/api/probe/things/7", headers=bearer(boris)).status_code == 200
    refused = client.get("/api/probe/things/3", headers=bearer(boris))
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Permission 'things.read' required"
    assert state.decisions - before == 3


# --- the panel with the other system's password ---------------------------------------------

def test_the_panel_signs_in_with_the_password_of_the_foreign_system(stand):
    client, _, _ = stand
    answer = client.post("/api/auth/login", json={"username": "anna",
                                                  "password": "anna-as-pass"})
    assert answer.status_code == 200, answer.text
    # The panel lives on the cookie; the session behind it is this framework's.
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"].startswith("anna")
    renewed = client.post("/api/auth/refresh",
                          headers={"X-CSRF-Token": client.cookies.get("ss_csrf")})
    assert renewed.status_code == 200, renewed.text


def test_a_wrong_password_is_the_same_401(stand):
    client, _, _ = stand
    wrong = client.post("/api/auth/login", json={"username": "anna", "password": "nope"})
    nobody = client.post("/api/auth/login", json={"username": "nobody-at-all",
                                                  "password": "nope"})
    assert wrong.status_code == nobody.status_code == 401
    assert wrong.json() == nobody.json()


def test_a_stand_without_the_secret_does_not_come_up(tmp_path, monkeypatch, other_system):
    base, _ = other_system
    auth_yaml, plugins, modules = deployment(tmp_path, base)
    monkeypatch.setenv("AUTH_CONFIG_PATH", str(auth_yaml))
    monkeypatch.delenv("HOMEGROWN_AS_KEY", raising=False)
    from keepup.auth.identity import IdentityProviderMisconfigured
    with pytest.raises(IdentityProviderMisconfigured, match="HOMEGROWN_AS_KEY"):
        create_app(KeepupSettings(static_mounts=(),
                                  plugin_manager=PluginManager(plugins_dir=str(plugins)),
                                  plugins_config_path=str(modules)))
