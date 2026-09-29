"""The largest body a route accepts, refused before it is read (keepup-49).

``max_upload_bytes`` was declared and enforced nowhere. A route now declares
``max_body_bytes``; a route whose body the framework reads falls under the
application's general limit; a route that reads its own body is limited only by
what it declares. Plugin routes are registered the way the runtime registers
them.

    python3 -m pytest keepup/tests/body_limit_tests.py -v
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from keepup.body_limit import BodyLimitMiddleware, declare_limit
from keepup.plugins.routes import register_plugin_routes
from keepup.schema import init_db

PACKAGE = Path(__file__).resolve().parents[1]
GENERAL = 100


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


class Manager:
    """The part of the plugin manager route registration asks for."""

    def __init__(self, routes):
        self.routes = routes

    def get_all_api_routes(self):
        return self.routes

    def get_all_websocket_routes(self):
        return []


async def write(request: dict = None):
    return {"got": len(str(request or {}))}


async def upload(request: Request = None):
    return {"got": len(await request.body())}


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.add_middleware(BodyLimitMiddleware, router_of=app, default_limit=GENERAL)

    @app.post("/framework")
    async def framework_route(request: Request):
        return {"got": len(await request.body())}

    import asyncio
    asyncio.run(register_plugin_routes(app, Manager([
        {"path": "/api/plain", "methods": ["POST"], "handler": write, "require_auth": False},
        {"path": "/api/small", "methods": ["POST"], "handler": write, "require_auth": False,
         "max_body_bytes": 20},
        {"path": "/api/big", "methods": ["POST"], "handler": write, "require_auth": False,
         "max_body_bytes": 1000},
        {"path": "/api/image", "methods": ["POST"], "handler": upload, "require_auth": False,
         "is_upload": True},
        {"path": "/api/image-capped", "methods": ["POST"], "handler": upload,
         "require_auth": False, "is_upload": True, "max_body_bytes": 500},
        {"path": "/api/free", "methods": ["POST"], "handler": write, "require_auth": False,
         "max_body_bytes": None},
    ])))
    return TestClient(app)


def body(size):
    """A JSON object of exactly this many bytes."""
    return ('{"x":"' + "a" * (size - 8) + '"}').encode()


@pytest.mark.parametrize("path, fits, too_big", [
    ("/api/plain", GENERAL, GENERAL + 1),        # the framework reads it: the general limit
    ("/framework", GENERAL, GENERAL + 1),        # the framework's own routes too
    ("/api/small", 20, 21),                      # a route that asks for less
    ("/api/big", 1000, 1001),                    # a route that asks for more
    ("/api/image-capped", 500, 501),             # an upload that states its size
])
def test_a_body_up_to_the_limit_passes_and_one_byte_more_is_refused(client, path, fits, too_big):
    assert client.post(path, content=body(fits),
                       headers={"content-type": "application/json"}).status_code == 200
    refused = client.post(path, content=body(too_big),
                          headers={"content-type": "application/json"})
    assert refused.status_code == 413
    assert "too large" in refused.json()["detail"]


def test_a_route_reading_its_own_body_is_not_limited_unless_it_says(client):
    answer = client.post("/api/image", content=b"x" * 10_000)
    assert answer.status_code == 200 and answer.json() == {"got": 10_000}


def test_a_route_that_declares_no_limit_has_none(client):
    assert client.post("/api/free", content=body(10_000),
                       headers={"content-type": "application/json"}).status_code == 200


def test_a_body_without_a_declared_length_is_counted_as_it_arrives(client):
    def chunks():
        for _ in range(10):
            yield b"x" * 100
    assert client.post("/api/image-capped", content=chunks()).status_code == 413
    assert client.post("/api/image-capped", content=iter([b"x" * 400])).status_code == 200


def test_a_nonsense_limit_stops_the_start():
    with pytest.raises(ValueError, match="max_body_bytes"):
        declare_limit(write, {"path": "/api/x", "max_body_bytes": "big"})
    with pytest.raises(ValueError, match="max_body_bytes"):
        declare_limit(write, {"path": "/api/x", "max_body_bytes": 0})


def test_the_application_gets_the_middleware_with_its_setting():
    source = (PACKAGE / "factory.py").read_text(encoding="utf-8")
    assert "app.add_middleware(BodyLimitMiddleware, router_of=app," in source
    assert "else settings.max_json_bytes))" in source


# --- keepup-61: small by default where the framework parses, and the holes around it ---

from keepup.factory import create_app  # noqa: E402
from keepup.settings import KeepupSettings  # noqa: E402

MIB = 1024 * 1024


def framework_app(**settings):
    return TestClient(create_app(KeepupSettings(title="Limits", static_mounts=(),
                                                plugin_manager=None, **settings)))


def json_of(size):
    return ('{"username":"' + "a" * (size - 30) + '","password":"x"}').encode()


def test_a_sign_in_body_is_refused_long_before_it_could_fill_memory():
    """JSON is parsed before the sign-in check; 256 MiB used to be let through."""
    client = framework_app()
    refused = client.post("/api/auth/login", content=json_of(3 * MIB),
                          headers={"content-type": "application/json"})
    assert refused.status_code == 413
    assert client.post("/api/auth/login", content=json_of(MIB),
                       headers={"content-type": "application/json"}).status_code != 413


def test_the_json_limit_and_the_application_s_own_limit_are_settings():
    small = framework_app(max_json_bytes=1000)
    assert small.post("/api/auth/login", content=json_of(2000),
                      headers={"content-type": "application/json"}).status_code == 413
    own = framework_app(max_json_bytes=1000, max_upload_bytes=4000)
    assert own.post("/api/auth/login", content=json_of(2000),
                    headers={"content-type": "application/json"}).status_code != 413


calls = []


async def counted_write(request: dict = None):
    calls.append(request)
    return {"ok": True}


def test_a_body_over_the_limit_without_a_length_never_reaches_the_handler():
    import asyncio
    app = FastAPI()
    app.add_middleware(BodyLimitMiddleware, router_of=app, default_limit=100)
    asyncio.run(register_plugin_routes(app, Manager([
        {"path": "/api/counted", "methods": ["POST", "OPTIONS"], "handler": counted_write,
         "require_auth": False}])))
    client = TestClient(app)
    calls.clear()
    answer = client.post("/api/counted", content=iter([b"{" + b" " * 300 + b"}"]),
                         headers={"content-type": "application/json"})
    assert answer.status_code == 413 and calls == []
    # A route declared with OPTIONS beside the write reads its body there too.
    refused = client.request("OPTIONS", "/api/counted", content=b"{" + b" " * 300 + b"}",
                             headers={"content-type": "application/json"})
    assert refused.status_code == 413 and calls == []


def test_the_stripped_mode_does_not_read_the_body_to_log_it():
    source = (Path(__file__).resolve().parents[1] / "factory.py").read_text(encoding="utf-8")
    stripped = source[source.index("def _create_stripped_app"):source.index("def create_app(")]
    assert "await request.body()" not in stripped
