"""Applications on keepup to audit, built from the code of this tree (keepup-94).

A profile is one way an application is put together: with or without plugins,
with OpenID Connect, with an identity provider, stripped, behind TLS. Each is a
real ``create_app()`` started with its lifespan, so plugins load from a plugins
directory and routes are what a deployment would register. A new profile is one
entry in PROFILES; the checks are parametrised over the registry and need no
edit.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import pytest
import uvicorn
from fastapi.testclient import TestClient

from keepup.factory import create_app
from keepup.plugins.base import PluginManager
from keepup.schema import init_db
from keepup.settings import KeepupSettings, OidcSettings

SERVICE_KEY = "audit-homegrown-service-key"
SERVICE_KEY_ENV = "HOMEGROWN_AS_KEY"
IDENTITY_SOURCE = "homegrown-as"

#: The plugin every plugin profile loads: one route of each kind of protection
#: the runtime offers, so the sweep sees every kind at least once.
AUDIT_PLUGIN = '''
from fastapi import HTTPException

from keepup.auth.dependencies import get_optional_user
from keepup.plugins.base import BasePlugin


class AuditPlugin(BasePlugin):
    def __init__(self, config=None):
        super().__init__("audit", "Audit", config)
        self.calls = []

    async def initialize(self):
        return True

    def get_handlers(self):
        return {"calls": lambda: self.calls}

    def get_api_routes(self):
        return [
            {"path": "/api/audit/signed", "methods": ["GET"], "handler": self.signed},
            {"path": "/api/audit/write", "methods": ["POST"], "handler": self.write},
            {"path": "/api/audit/items/{item_id}", "methods": ["GET"],
             "handler": self.item, "permission": "audit.read"},
            {"path": "/api/audit/upload", "methods": ["POST"], "handler": self.upload,
             "is_upload": True},
            {"path": "/api/audit/public", "methods": ["GET"], "handler": self.public,
             "require_auth": False},
            {"path": "/api/audit/raw", "methods": ["POST"], "handler": self.raw,
             "raw_request": True},
        ]

    def get_websocket_routes(self):
        return [
            {"path": "/ws/audit/signed", "handler": self.signed_socket, "require_auth": True},
            {"path": "/ws/audit/guest", "handler": self.guest_socket},
        ]

    async def signed(self, current_user: dict = None):
        self.calls.append("signed")
        return {"user": current_user["username"]}

    async def write(self, request: dict = None, current_user: dict = None):
        self.calls.append("write")
        return {"written": True}

    async def item(self, item_id: str = None, current_user: dict = None):
        self.calls.append(f"item:{item_id}")
        return {"item": item_id}

    async def upload(self, request=None, current_user: dict = None):
        self.calls.append("upload")
        return {"uploaded": True}

    async def public(self):
        return {"public": True}

    async def raw(self, request):
        # A raw route signs its caller in itself; the framework does not.
        scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
        user = await get_optional_user(token if scheme.lower() == "bearer" else None)
        if user is None:
            raise HTTPException(status_code=401, detail="Could not validate credentials")
        self.calls.append("raw")
        return {"user": user["username"]}

    async def signed_socket(self, websocket, current_user=None):
        await websocket.accept()
        await websocket.send_json({"user": current_user["username"]})
        await websocket.close()

    async def guest_socket(self, websocket):
        await websocket.accept()
        await websocket.send_json({"guest": True})
        await websocket.close()
'''

#: Paths of the audit plugin, for checks that address it by name.
PLUGIN_SIGNED = "/api/audit/signed"
PLUGIN_WRITE = "/api/audit/write"
PLUGIN_ITEM = "/api/audit/items/{item_id}"
PLUGIN_PUBLIC = "/api/audit/public"
PLUGIN_RAW = "/api/audit/raw"
SOCKET_SIGNED = "/ws/audit/signed"
SOCKET_GUEST = "/ws/audit/guest"


@dataclass(frozen=True)
class Profile:
    name: str
    #: Whether the application serves the API at all.
    http: bool = True
    plugins: bool = False
    oidc: bool = False
    identity: bool = False
    stripped: bool = False
    https: bool = False

    def __str__(self):
        return self.name


PROFILES = (
    Profile("bare"),
    Profile("plugins", plugins=True),
    Profile("oidc", plugins=True, oidc=True),
    Profile("identity", plugins=True, identity=True),
    Profile("stripped", http=False, stripped=True),
    Profile("https", plugins=True, https=True),
)

HTTP_PROFILES = tuple(p for p in PROFILES if p.http)


def by_name(name: str) -> Profile:
    return next(p for p in PROFILES if p.name == name)


# --- the other system ------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@dataclass
class OtherSystem:
    base_url: str
    state: object


@contextmanager
def other_system() -> Iterator[OtherSystem]:
    """The homegrown system of keepup/tests/homegrown_as on a real port."""
    from keepup.tests.homegrown_as.service import build_service

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
    try:
        yield OtherSystem(f"http://127.0.0.1:{port}", service.state.as_state)
    finally:
        server.should_exit = True
        thread.join(timeout=5)


# --- building and running a profile ---------------------------------------------------

@dataclass
class Running:
    profile: Profile
    app: object
    client: TestClient
    other: Optional[OtherSystem] = None
    #: The identity runtime this application configured, or None.
    identity: object = None

    def plugin_calls(self):
        manager = self.app.state.plugin_manager if hasattr(self.app, "state") else None
        plugin = manager.get_plugin("audit") if manager else None
        return plugin.calls if plugin else []


def _write_files(profile: Profile, workdir: Path, other: Optional[OtherSystem]):
    auth_yaml = workdir / "auth.yaml"
    lines = ["auth:", "  provider: local"]
    if profile.identity:
        section = {
            "name": IDENTITY_SOURCE,
            "plugin": "keepup.tests.homegrown_as.provider:HomegrownProvider",
            "settings": {"base_url": other.base_url, "service_key": "${%s}" % SERVICE_KEY_ENV},
            "accept_tokens": True,
            "password_sign_in": True,
            "authorization": "provider",
            "new_accounts": "create",
            "role_mapping": {"AS_ADMINISTRATORS": "ADMIN", "AS_OPERATORS": "CLIENT"},
            "token_cache_seconds": 1,
            "timeout_seconds": 2,
        }
        lines.append("identity_provider:")
        lines += [f"  {key}: {json.dumps(value)}" for key, value in section.items()]
    auth_yaml.write_text("\n".join(lines) + "\n", encoding="utf-8")

    plugins = workdir / "plugins"
    plugins.mkdir(exist_ok=True)
    (plugins / "audit.py").write_text(AUDIT_PLUGIN, encoding="utf-8")
    modules = workdir / "modules.json"
    modules.write_text(json.dumps({"plugins": [
        {"id": "audit", "name": "Audit", "enabled": True, "priority": 1, "config": {}}]}),
        encoding="utf-8")
    return auth_yaml, plugins, modules


def settings_for(profile: Profile, plugins: Path, modules: Path) -> KeepupSettings:
    options = dict(title=f"security-audit-{profile.name}", static_mounts=(),
                   disable_http_server=profile.stripped)
    if profile.plugins:
        options.update(plugin_manager=PluginManager(plugins_dir=str(plugins)),
                       plugins_config_path=str(modules))
    if profile.oidc:
        options["oidc"] = OidcSettings(issuer="https://idp.audit.invalid",
                                       client_id="audit", client_secret="audit-client",
                                       redirect_uri="https://testserver/api/auth/oidc/callback")
    return KeepupSettings(**options)


@contextmanager
def running(profile: Profile, workdir: Path, other: Optional[OtherSystem] = None
            ) -> Iterator[Running]:
    """The profile's application, started, with a client to call it."""
    init_db()
    with pytest.MonkeyPatch.context() as patch:
        auth_yaml, plugins, modules = _write_files(profile, workdir, other)
        patch.setenv("AUTH_CONFIG_PATH", str(auth_yaml))
        patch.setenv(SERVICE_KEY_ENV, SERVICE_KEY)
        patch.setenv("SSL_ENABLED", "true" if profile.https else "false")
        app = create_app(settings_for(profile, plugins, modules))
        base = "https://testserver" if profile.https else "http://testserver"
        from keepup.auth.identity import runtime as identity_runtime
        configured = identity_runtime.current()
        with TestClient(app, base_url=base, raise_server_exceptions=False) as client:
            yield Running(profile, app, client, other, configured)
        identity_runtime.install(None)
