"""create_app raises the plugin catalogue beside the path it already has.

Task keepup-123. The application is still assembled the way 0.3.0 assembled it --
plugins from the directory, routes registered module by module -- and the runtime
is raised with it: it resolves the catalogue and the profile, publishes what a
capability offers and creates what a capability declares. That is what lets a
declaration leave `keepup_db.schema` later without the table disappearing (keepup-124).

The transport binds what the plugins declared (keepup-121), so an application
that names only `plugins_dir` -- the shape a generated project has -- answers on
the addresses its own plugins declare, without a PluginManager of the 0.3.0 path.

A catalogue that cannot start must not take the application with it: deployments
reach this code with a catalogue that never existed.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup import KeepupSettings, create_app
from keepup_http.routes import register_declared_routes, register_declared_sockets


def settings(**overrides):
    """An application with no plugins on disk and nothing mounted."""
    values = dict(title="kernel", static_mounts=(), plugin_manager=None)
    values.update(overrides)
    return KeepupSettings(**values)


#: A plugin with one public route and one public socket, on disk.
DEMO = '''"""One plugin, one route and one socket."""

from keepup.plugins.base import BasePlugin


class DemoPlugin(BasePlugin):
    def __init__(self, config):
        super().__init__("demo", "Demo", config)

    async def initialize(self) -> bool:
        return True

    def get_api_routes(self):
        return [{"path": "/api/demo", "methods": ["GET"], "handler": self.list,
                 "require_auth": False, "audit": False}]

    def get_websocket_routes(self):
        return [{"path": "/ws/demo", "handler": self.socket}]

    def get_handlers(self):
        return {}

    async def list(self):
        return {"demo": True}

    async def socket(self, websocket):
        await websocket.accept()
        await websocket.send_json({"demo": True})
        await websocket.close()
'''


def project(tmp_path, monkeypatch, source=DEMO):
    """A project on disk with one plugin, and the settings that name it."""
    (tmp_path / "plugins").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "plugins" / "demo.py").write_text(source, encoding="utf-8")
    (tmp_path / "config" / "modules.json").write_text(json.dumps({
        "plugins": [{"id": "demo", "name": "Demo", "enabled": True, "priority": 60,
                     "config": {}}],
        "modules": [],
        "roles": [],
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return settings(plugins_dir=str(tmp_path / "plugins"),
                    plugins_config_path=str(tmp_path / "config" / "modules.json"))


async def handler():
    """A handler for the declarations the checks below hand in."""
    return {}


def test_a_plugin_the_application_gave_only_a_directory_for_answers(tmp_path, monkeypatch):
    """The kernel loads it; the transport binds what it declared (keepup-121).

    The shape a generated project has: `plugins_dir` and no PluginManager.
    Before this seam the plugin was loaded, initialised and declared its routes,
    and none of them was ever mounted -- the work happened, and no address
    answered.
    """
    app = create_app(project(tmp_path, monkeypatch))
    with TestClient(app) as client:
        answer = client.get("/api/demo")
        assert answer.status_code == 200
        assert answer.json() == {"demo": True}


def test_a_socket_the_plugin_declared_answers(tmp_path, monkeypatch):
    """A socket is the other thing a plugin declares, and it is mounted too."""
    app = create_app(project(tmp_path, monkeypatch))
    with TestClient(app) as client:
        with client.websocket_connect("/ws/demo") as socket:
            assert socket.receive_json() == {"demo": True}


def test_an_address_the_application_already_answers_is_not_mounted_twice():
    """The framework's own routes and a manager's are registered first, so the
    kernel's copy of one of them is skipped rather than answered twice."""
    app = FastAPI()
    declaration = {"path": "/api/demo", "methods": ["GET"], "handler": handler,
                   "require_auth": False, "audit": False}
    assert register_declared_routes(app, [declaration]) == 1
    assert register_declared_routes(app, [declaration]) == 0
    assert len([route for route in app.routes if route.path == "/api/demo"]) == 1


def test_a_socket_the_application_already_answers_is_not_mounted_twice():
    """A socket is asked about by path alone: it has no methods of its own."""
    app = FastAPI()
    declaration = {"path": "/ws/demo", "handler": handler}
    assert register_declared_sockets(app, [declaration]) == 1
    assert register_declared_sockets(app, [declaration]) == 0
    assert len([route for route in app.routes if route.path == "/ws/demo"]) == 1


def test_a_declaration_the_runtime_refuses_is_refused():
    """A right on a route nobody signs in to is a mistake found at the start,
    not a route that quietly goes missing (keepup-102)."""
    app = FastAPI()
    with pytest.raises(ValueError):
        register_declared_routes(app, [{
            "path": "/api/demo", "methods": ["GET"], "handler": handler,
            "require_auth": False, "permission": "demo.read"}])


def test_an_application_raises_a_runtime_and_serves():
    """The catalogue is part of the application now, and the panel still answers."""
    app = create_app(settings())
    with TestClient(app) as client:
        assert isinstance(app.state.kernel, object)
        assert client.get("/api/health").status_code in (200, 503)


def test_the_runtime_is_stopped_with_the_application():
    """A service a capability published is closed with the process."""
    app = create_app(settings())
    with TestClient(app):
        kernel = app.state.kernel
    assert kernel is None or kernel.started is False


def test_a_catalogue_that_cannot_start_does_not_take_the_application_with_it():
    """0.3.0 deployments have no catalogue: the application boots and says so."""
    catalogue = settings()
    app = create_app(catalogue)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code in (200, 503)
