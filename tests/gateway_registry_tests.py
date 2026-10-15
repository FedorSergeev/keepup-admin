"""The gateway's registry: services that register themselves, and plugins served in process.

A service behind the gateway does not have to be written into the gateway's
config. A keepup application with `keepup-gateway` and an `announce` section
registers its name, prefix and address, signed with the shared secret, and
repeats it while it runs; the record runs out when it stops. A plugin of the same
runtime registers a handler instead of an address, and is answered by a call.
Both are reached through one fallback route that sees only what nobody else
answers.

    python -m pytest tests/gateway_registry_tests.py -v
"""

import asyncio
import json
import time

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from keepup.kernel import security
from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup_gateway.announcer import REGISTER_PATH, Announcer, parse_announce
from keepup_gateway.plugin import GatewayPlugin
from keepup_gateway.proxy import FALLBACK_PATH
from keepup_gateway.registry import (
    ACTION_REGISTER,
    Registry,
    RegistrationError,
    declared_table,
    sign_registration,
    verify_registration,
)
from keepup_gateway.table import TableError
from keepup_http.routes import register_declared_routes

SECRET = "shared-with-the-services"


class Source:
    """The `datasource` service over an in-memory SQLite, with the registry's table."""

    dialect = "sqlite"

    def __init__(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        declared_table().create(self.engine)

    async def execute(self, statement, params=None):
        with self.engine.connect() as connection:
            return [dict(row._mapping) for row in connection.execute(text(statement),
                                                                     dict(params or {}))]

    async def execute_commit(self, statement, params=None):
        with self.engine.begin() as connection:
            return connection.execute(text(statement), dict(params or {})).rowcount


class Services:
    def __init__(self, **provided):
        self.provided = provided

    def has(self, name, minimum=None):
        return name in self.provided

    def require(self, name):
        return self.provided[name]

    def provide(self, name, value, **kwargs):
        self.provided[name] = value


def started(config, transport=None, source=None):
    """A gateway plugin registered and initialised as the runtime would."""
    plugin = GatewayPlugin(config, transport=transport)
    services = Services(**({SERVICE_DATASOURCE: source} if source is not None else {}))
    plugin.register(services)
    plugin.services = services
    return plugin, services


def mounted(plugin, before=()):
    """An application with the gateway's routes mounted after some of its own."""
    app = FastAPI()
    for path, answer in before:
        app.add_api_route(path, (lambda value: (lambda: value))(answer), methods=["GET"])
    register_declared_routes(app, plugin.get_api_routes())
    return app


def signed(action, record, secret=SECRET, now=None):
    body = json.dumps(record).encode()
    return body, {"Content-Type": "application/json",
                  **sign_registration(secret, action, body, now=now)}


@pytest.fixture(autouse=True)
def no_identity():
    security.clear_identity()
    yield
    security.clear_identity()


@pytest.fixture
def registry_gateway():
    """A gateway with the registry on, an upstream stand-in, and its client."""
    seen = []

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={"host": request.url.host, "path": request.url.path})

    plugin, _ = started({"identity_secret": SECRET, "registry": {"enabled": True},
                         "routes": [{"id": "shop", "prefix": "/api/shop",
                                     "upstreams": ["http://shop"]}]},
                        transport=httpx.MockTransport(upstream), source=Source())
    asyncio.run(plugin.initialize())
    client = TestClient(mounted(plugin, before=[("/api/health", {"status": "healthy"})]))
    return plugin, client, seen


def register(client, record, secret=SECRET):
    body, headers = signed(ACTION_REGISTER, record, secret)
    return client.post(REGISTER_PATH, content=body, headers=headers)


ORDERS = {"name": "orders", "prefix": "/api/orders", "address": "http://orders-1:8000"}


# --- registering --------------------------------------------------------------------------

def test_a_registered_service_is_reached_through_the_gateway(registry_gateway):
    plugin, client, seen = registry_gateway
    assert register(client, ORDERS).status_code == 200
    answer = client.get("/api/orders/7")
    assert answer.status_code == 200
    assert answer.json() == {"host": "orders-1", "path": "/api/orders/7"}
    assert answer.headers["x-request-id"]


def test_an_unsigned_or_forged_or_old_registration_is_refused(registry_gateway):
    plugin, client, _ = registry_gateway
    body = json.dumps(ORDERS).encode()
    assert client.post(REGISTER_PATH, content=body).status_code == 401
    assert register(client, ORDERS, secret="another").status_code == 401
    old_body, old = signed(ACTION_REGISTER, ORDERS, now=time.time() - 3600)
    assert client.post(REGISTER_PATH, content=old_body, headers=old).status_code == 401
    assert client.get("/api/orders").status_code == 404


def test_the_signature_covers_the_body():
    body, headers = signed(ACTION_REGISTER, ORDERS)
    verify_registration(SECRET, ACTION_REGISTER, headers, body)
    with pytest.raises(RegistrationError):
        verify_registration(SECRET, ACTION_REGISTER, headers,
                            body.replace(b"orders-1", b"attacker"))
    with pytest.raises(RegistrationError):
        verify_registration(SECRET, "deregister", headers, body)


def test_a_prefix_somebody_holds_is_refused(registry_gateway):
    plugin, client, _ = registry_gateway
    taken = register(client, {**ORDERS, "prefix": "/api/shop"})
    assert taken.status_code == 409 and "shop" in taken.json()["detail"]
    answered = register(client, {**ORDERS, "prefix": "/api/health"})
    assert answered.status_code == 409 and "application itself" in answered.json()["detail"]
    assert register(client, ORDERS).status_code == 200
    other = register(client, {**ORDERS, "name": "billing", "address": "http://billing"})
    assert other.status_code == 409 and "orders" in other.json()["detail"]


def test_a_record_that_cannot_be_served_is_refused(registry_gateway):
    plugin, client, _ = registry_gateway
    assert register(client, {**ORDERS, "prefix": "/"}).status_code == 400
    assert register(client, {**ORDERS, "address": "ftp://x"}).status_code == 400
    assert register(client, {**ORDERS, "ttl": 100000}).status_code == 400
    assert register(client, {"prefix": "/x"}).status_code == 400


def test_two_addresses_of_one_service_are_taken_in_turn(registry_gateway):
    plugin, client, seen = registry_gateway
    register(client, ORDERS)
    register(client, {**ORDERS, "address": "http://orders-2:8000"})
    hosts = [client.get("/api/orders").json()["host"] for _ in range(4)]
    assert sorted(set(hosts)) == ["orders-1", "orders-2"]
    assert hosts[0] != hosts[1]


def test_a_withdrawn_or_expired_record_stops_being_served(registry_gateway):
    plugin, client, _ = registry_gateway
    register(client, {**ORDERS, "ttl": 1})
    assert client.get("/api/orders").status_code == 200
    body, headers = signed("deregister", {"name": "orders", "address": ORDERS["address"]})
    assert client.request("DELETE", REGISTER_PATH, content=body, headers=headers).json() == \
        {"removed": True}
    assert client.get("/api/orders").status_code == 404

    register(client, {**ORDERS, "ttl": 0.5})
    time.sleep(0.6)
    plugin.service.registry._read_at = None
    assert client.get("/api/orders").status_code == 404


def test_a_registered_service_is_an_application_card(registry_gateway):
    plugin, client, _ = registry_gateway
    register(client, ORDERS)
    report = asyncio.run(plugin.service.report())["routes"]
    assert [(route["id"], route["source"]) for route in report] == \
        [("service:orders", "registered"), ("shop", "config")]
    cards = asyncio.run(plugin.applications_report({"id": 1, "username": "root",
                                                    "roles": ["ADMIN"]}))
    assert [item["route"] for item in cards["applications"]] == ["service:orders"]


def test_the_registry_needs_the_secret_and_a_database():
    plugin, _ = started({"registry": {"enabled": True}})
    assert "identity_secret" in plugin._error
    plugin, _ = started({"identity_secret": SECRET, "registry": {"enabled": True}})
    with pytest.raises(TableError, match="datasource"):
        asyncio.run(plugin.initialize())


def test_a_replica_reads_the_records_once_in_a_while():
    source = Source()
    calls = []
    original = source.execute

    async def counted(statement, params=None):
        calls.append(statement)
        return await original(statement, params)

    source.execute = counted
    clock = [0.0]
    registry = Registry(source, lambda prefix: None, clock_seconds=lambda: clock[0])
    asyncio.run(registry.routes())
    asyncio.run(registry.routes())
    assert len(calls) == 1
    clock[0] = 5.0
    asyncio.run(registry.routes())
    assert len(calls) == 2


# --- the fallback -------------------------------------------------------------------------

def test_what_nobody_answers_is_still_404_and_a_wrong_method_still_405(registry_gateway):
    plugin, client, _ = registry_gateway
    missing = client.get("/nothing/here")
    assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}
    wrong = client.post("/api/health")
    assert wrong.status_code == 405 and "GET" in wrong.headers["allow"]


def test_a_route_mounted_after_the_fallback_still_answers(registry_gateway):
    plugin, client, _ = registry_gateway
    client.app.add_api_route("/api/late", lambda: {"late": True}, methods=["GET"])
    routes = [getattr(route, "path", "") for route in client.app.router.routes]
    assert routes.index(FALLBACK_PATH) < routes.index("/api/late")
    assert client.get("/api/late").json() == {"late": True}


def test_without_the_registry_or_a_local_service_there_is_no_fallback():
    plugin, _ = started({"routes": [{"prefix": "/x", "upstreams": ["http://a"]}]})
    assert FALLBACK_PATH not in [route["path"] for route in plugin.get_api_routes()]


# --- in process ---------------------------------------------------------------------------

def test_a_plugin_of_this_runtime_is_answered_by_a_call():
    plugin, services = started({})
    received = []

    async def orders(call):
        received.append(call)
        if call.path == "/boom":
            raise RuntimeError("broken")
        if call.path == "/missing":
            raise HTTPException(status_code=404, detail="no such order")
        if call.method == "DELETE":
            return None
        return {"path": call.path, "page": call.query.get("page"), "body": call.json()}

    services.require("gateway").register_local("orders", "/api/orders", orders,
                                               strip_prefix=True)
    asyncio.run(plugin.initialize())
    client = TestClient(mounted(plugin))

    answer = client.post("/api/orders/7?page=2", json={"qty": 3},
                         headers={"X-Keepup-User-Id": "1"})
    assert answer.status_code == 200
    assert answer.json() == {"path": "/7", "page": "2", "body": {"qty": 3}}
    assert received[-1].caller is None
    assert "x-keepup-user-id" not in {name.lower() for name in received[-1].headers}
    assert answer.headers["x-request-id"] == received[-1].request_id
    assert client.delete("/api/orders/7").status_code == 204
    assert client.get("/api/orders/missing").status_code == 404
    assert client.get("/api/orders/boom").status_code == 502


def test_a_local_service_gets_the_caller_the_edge_recognised():
    plugin, services = started({"identity_secret": SECRET})
    received = []
    services.require("gateway").register_local(
        "orders", "/api/orders", lambda call: received.append(call) or {"ok": True},
        auth="session")
    asyncio.run(plugin.initialize())

    async def who(request):
        if request.headers.get("authorization") != "Bearer good":
            raise HTTPException(status_code=401, detail="not signed in")
        return {"id": 7, "username": "ann", "roles": ["CLIENT"]}

    security.set_identity(security.Identity(subject_dependency=who, name="test"))
    client = TestClient(mounted(plugin))
    assert client.get("/api/orders").status_code == 401
    assert client.get("/api/orders", headers={"Authorization": "Bearer good"}).json() == \
        {"ok": True}
    assert received[-1].caller.username == "ann"
    assert received[-1].caller.request_id == received[-1].request_id


def test_a_local_prefix_somebody_holds_is_refused():
    plugin, services = started({"routes": [{"prefix": "/api/shop", "upstreams": ["http://a"]}]})
    gateway = services.require("gateway")
    with pytest.raises(TableError, match="taken"):
        gateway.register_local("shop", "/api/shop", lambda call: None)
    gateway.register_local("orders", "/api/orders", lambda call: None)
    with pytest.raises(TableError, match="taken"):
        gateway.register_local("again", "/api/orders/", lambda call: None)
    with pytest.raises(TableError):
        gateway.register_local("root", "/", lambda call: None)


# --- announcing ---------------------------------------------------------------------------

def test_an_announcement_must_outpace_its_own_record():
    assert parse_announce(None) is None
    with pytest.raises(TableError, match="announce.address"):
        parse_announce({"gateway": "http://g", "name": "o", "prefix": "/o"})
    with pytest.raises(TableError, match="shorter"):
        parse_announce({"gateway": "http://g", "name": "o", "prefix": "/o",
                        "address": "http://o", "ttl": 10, "interval": 10})
    settings = parse_announce({"gateway": "http://g/", "name": "o", "prefix": "/o",
                               "address": "http://o/", "ttl": 30, "auth": "session"})
    assert settings["interval"] == 10 and settings["gateway"] == "http://g"
    assert settings["record"] == {"name": "o", "prefix": "/o", "address": "http://o",
                                  "ttl": 30, "auth": "session"}


def test_an_application_registers_itself_and_takes_its_address_back(registry_gateway):
    plugin, client, _ = registry_gateway

    def to_gateway(request):
        reply = client.request(request.method, request.url.path, content=request.content,
                               headers=dict(request.headers))
        return httpx.Response(reply.status_code, content=reply.content)

    settings = parse_announce({"gateway": "http://gateway", "name": "orders",
                               "prefix": "/api/orders", "address": "http://orders-1:8000",
                               "ttl": 30, "interval": 5})

    async def life():
        announcer = Announcer(settings, SECRET, transport=httpx.MockTransport(to_gateway))
        announcer.start()
        await asyncio.sleep(0.05)
        registered = announcer.registered
        plugin.service.registry._read_at = None
        routes = [route.id for route in await plugin.service.table()]
        await announcer.stop()
        plugin.service.registry._read_at = None
        after = [route.id for route in await plugin.service.table()]
        return registered, routes, after

    registered, routes, after = asyncio.run(life())
    assert registered and "service:orders" in routes
    assert "service:orders" not in after


def test_a_gateway_that_does_not_answer_is_retried_not_fatal():
    settings = parse_announce({"gateway": "http://gateway", "name": "orders",
                               "prefix": "/api/orders", "address": "http://orders-1"})

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    async def once():
        announcer = Announcer(settings, SECRET, transport=httpx.MockTransport(down))
        ok = await announcer.send(ACTION_REGISTER)
        return ok, announcer.last_error

    ok, error = asyncio.run(once())
    assert ok is False and "did not answer" in error


# --- in a whole application ---------------------------------------------------------------

#: A product plugin that serves its prefix in process through the gateway.
PRODUCT = '''"""A product plugin with one route of its own and one service behind the gateway."""

from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.plugins.base import BasePlugin


class ProductPlugin(BasePlugin):
    descriptor = PluginDescriptor(id="product", name="Product", kind=KIND_OPTIONAL,
                                  priority=60, wants=("gateway>=1",))

    def __init__(self, config):
        super().__init__("product", "Product", config)

    async def initialize(self) -> bool:
        if self.services.has("gateway"):
            self.services.require("gateway").register_local(
                "catalogue", "/api/catalogue", self.catalogue, strip_prefix=True)
        return True

    def get_api_routes(self):
        return [{"path": "/api/product", "methods": ["GET"], "handler": self.own,
                 "require_auth": False, "audit": False}]

    def get_handlers(self):
        return {}

    async def own(self):
        return {"own": True}

    async def catalogue(self, call):
        return {"items": call.path}
'''


def application(tmp_path, monkeypatch):
    from keepup import KeepupSettings

    for name in ("plugins", "config", "data"):
        (tmp_path / name).mkdir()
    (tmp_path / "plugins" / "product.py").write_text(PRODUCT, encoding="utf-8")
    (tmp_path / "config" / "modules.json").write_text(json.dumps({
        "plugins": [{"id": "product", "name": "Product", "enabled": True, "priority": 60,
                     "config": {}},
                    {"id": "gateway", "name": "Gateway", "enabled": True,
                     "config": {"identity_secret": SECRET}}],
        "modules": [], "roles": []}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return KeepupSettings(title="behind", static_mounts=(), plugin_manager=None,
                          plugins_dir=str(tmp_path / "plugins"),
                          plugins_config_path=str(tmp_path / "config" / "modules.json"))


def test_a_whole_application_serves_its_plugin_in_process_and_keeps_its_own_routes(
        tmp_path, monkeypatch):
    from keepup import create_app

    app = create_app(application(tmp_path, monkeypatch))
    with TestClient(app) as client:
        assert client.get("/api/catalogue/tea").json() == {"items": "/tea"}
        assert client.get("/api/product").json() == {"own": True}
        assert client.get("/api/health").status_code in (200, 503)
        assert client.get("/no/such/thing").status_code == 404
        paths = [getattr(route, "path", "") for route in app.router.routes]
        assert paths.index(FALLBACK_PATH) > paths.index("/api/product")
