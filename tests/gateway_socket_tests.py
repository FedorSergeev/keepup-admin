"""WebSocket through the gateway: the same table and edge, carried to a real service.

A service behind the gateway is often more than requests: agents and consoles
hold sockets. A route that says ``"websocket": true`` carries them -- matched by
prefix, signed in at the edge, connected to the addresses in turn, every message
both ways, and the close code of whichever side closed passed to the other.

The service here is a real one: a socket echo served by uvicorn on a free port,
so what is checked is the gateway talking to a socket server over the network.

    python -m pytest tests/gateway_socket_tests.py -v
"""

import asyncio
import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from keepup.kernel import security
from keepup_gateway import verify_identity
from keepup_gateway.plugin import GatewayPlugin
from keepup_gateway.sockets import FALLBACK_SOCKET
from keepup_http.routes import register_declared_routes, register_declared_sockets

SECRET = "shared-with-the-services"


async def echo(websocket):
    """Echo every message; "headers" answers what the gateway sent; "bye" closes 4001."""
    offered = websocket.scope.get("subprotocols") or []
    await websocket.accept(subprotocol="v1" if "v1" in offered else None)
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        if message.get("bytes") is not None:
            await websocket.send_bytes(message["bytes"])
            continue
        text = message["text"]
        if text == "headers":
            await websocket.send_text(json.dumps({**dict(websocket.headers),
                                                  "path": websocket.url.path,
                                                  "query": websocket.url.query}))
        elif text == "bye":
            await websocket.close(code=4001, reason="done")
            return
        else:
            await websocket.send_text(text)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="module")
def service():
    """A real socket service on a free port, for the whole module."""
    port = free_port()
    app = Starlette(routes=[WebSocketRoute("/ws/{rest:path}", echo)])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="error", ws="websockets-sansio"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "the stand-in service did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def no_identity():
    security.clear_identity()
    yield
    security.clear_identity()


def gateway(routes, secret=SECRET, extra=None, before=()):
    """An application with the gateway's routes and sockets, and the plugin."""
    config = {"identity_secret": secret, "routes": routes, **(extra or {})}
    plugin = GatewayPlugin(config)
    app = FastAPI()
    for path, handler in before:
        from starlette.routing import WebSocketRoute as Route

        app.router.routes.append(Route(path, handler))
    register_declared_routes(app, plugin.get_api_routes())
    register_declared_sockets(app, plugin.get_websocket_routes())
    return TestClient(app), plugin


def test_messages_go_both_ways_with_the_subprotocol_and_the_headers(service):
    client, _ = gateway([{"prefix": "/ws", "upstreams": [service], "websocket": True}])
    with client.websocket_connect("/ws/agent?node=7", subprotocols=["v1"],
                                  headers={"X-Request-Id": "abc-1",
                                           "X-Keepup-User-Id": "1"}) as ws:
        assert ws.accepted_subprotocol == "v1"
        ws.send_text("hello")
        assert ws.receive_text() == "hello"
        ws.send_bytes(b"\x00\x01")
        assert ws.receive_bytes() == b"\x00\x01"
        ws.send_text("headers")
        seen = json.loads(ws.receive_text())
    assert seen["path"] == "/ws/agent" and seen["query"] == "node=7"
    assert seen["x-request-id"] == "abc-1"
    assert seen["x-forwarded-prefix"] == "/ws"
    assert "x-keepup-user-id" not in seen


def test_a_dot_segment_closes_the_socket_before_a_route_is_chosen(service):
    """The socket's prefix must hold for the path the service is addressed on."""
    client, _ = gateway([{"prefix": "/ws", "upstreams": [service], "websocket": True}])
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/ws/%2e%2e/admin"):
            pass
    assert closed.value.code == 1008


def test_the_services_close_code_reaches_the_client(service):
    client, _ = gateway([{"prefix": "/ws", "upstreams": [service], "websocket": True}])
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text("bye")
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 4001


def test_an_address_that_takes_no_socket_is_passed_over(service):
    dead = f"http://127.0.0.1:{free_port()}"
    client, plugin = gateway([{"id": "agents", "prefix": "/ws",
                               "upstreams": [dead, service], "websocket": True}])
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text("hello")
        assert ws.receive_text() == "hello"
    assert plugin.service.balancer.state("agents", dead)["state"] == "cooling"


def test_no_address_taking_the_socket_refuses_it():
    client, _ = gateway([{"prefix": "/ws", "upstreams": [f"http://127.0.0.1:{free_port()}"],
                          "websocket": True}])
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws/agent"):
            pass
    assert refused.value.code == 1013


def test_a_route_that_does_not_carry_sockets_refuses_them(service):
    client, plugin = gateway([{"prefix": "/ws", "upstreams": [service]}])
    assert plugin.get_websocket_routes() == []
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/agent"):
            pass


def test_a_socket_is_signed_in_at_the_edge(service):
    client, _ = gateway([{"prefix": "/ws", "upstreams": [service], "websocket": True,
                          "auth": "session"}])
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws/agent"):
            pass
    assert refused.value.code == 1008

    async def who(connection):
        if connection.headers.get("authorization") != "Bearer good":
            raise HTTPException(status_code=401)
        return {"id": 7, "username": "ann", "roles": ["ADMIN"]}

    security.set_identity(security.Identity(subject_dependency=who, name="test"))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/agent"):
            pass
    with client.websocket_connect("/ws/agent",
                                  headers={"Authorization": "Bearer good"}) as ws:
        ws.send_text("headers")
        seen = json.loads(ws.receive_text())
    caller = verify_identity(seen, SECRET)
    assert caller.username == "ann" and caller.roles == ("ADMIN",)


def test_a_registered_service_carries_sockets_through_the_fallback(service):
    from tests.gateway_registry_tests import Services, Source

    plugin = GatewayPlugin({"identity_secret": SECRET, "registry": {"enabled": True}})
    services = Services(datasource=Source())
    plugin.register(services)
    plugin.services = services
    asyncio.run(plugin.initialize())
    asyncio.run(plugin.service.registry.register(
        {"name": "agents", "address": service, "prefixes": ["/ws"], "websocket": True}))
    assert FALLBACK_SOCKET in [item["path"] for item in plugin.get_websocket_routes()]
    app = FastAPI()
    register_declared_routes(app, plugin.get_api_routes())
    register_declared_sockets(app, plugin.get_websocket_routes())
    with TestClient(app).websocket_connect("/ws/agent") as ws:
        ws.send_text("hello")
        assert ws.receive_text() == "hello"


def test_the_fallback_hands_a_socket_to_a_route_mounted_after_it():
    plugin = GatewayPlugin({"identity_secret": SECRET, "registry": {"enabled": True}})

    async def own(websocket):
        await websocket.accept()
        await websocket.send_text("own")
        await websocket.close()

    async def no_routes():
        return []

    plugin.sockets.table = no_routes
    app = FastAPI()
    register_declared_sockets(app, plugin.get_websocket_routes())
    app.router.routes.append(WebSocketRoute("/ws/own", own))
    with TestClient(app).websocket_connect("/ws/own") as ws:
        assert ws.receive_text() == "own"
