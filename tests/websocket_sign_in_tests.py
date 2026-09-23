"""Signing a WebSocket in, as the framework does it for any application.

    python3 -m pytest keepup/tests/websocket_sign_in_tests.py -v
"""

import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from keepup.auth import dependencies, panel_session
from keepup.auth import websocket as ws_auth
from keepup.plugins import routes
from keepup.plugins.registry import signed_in_websocket

USERS = {"good-token": {"id": 7, "username": "anna", "role": "CLIENT"}}


@pytest.fixture(autouse=True)
def tokens(monkeypatch):
    """Tokens resolve to users by name; the token check itself is covered elsewhere."""
    async def fake_current_user(token=None, **_):
        if token not in USERS:
            raise HTTPException(status_code=401, detail="Invalid token")
        return USERS[token]

    monkeypatch.setattr(dependencies, "get_current_user", fake_current_user)


def build():
    seen = []

    async def handler(websocket, current_user):
        seen.append(current_user)
        await websocket.accept()
        await websocket.send_json({"hello": current_user["username"]})
        await websocket.close()

    app = FastAPI()
    routes._register_websocket(app, "/ws/test", signed_in_websocket(handler))
    return TestClient(app), seen


def refusal(client, url, **kwargs):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(url, **kwargs) as socket:
            socket.receive_json()
    return closed.value.code, closed.value.reason


def test_a_token_in_the_address_signs_in():
    client, seen = build()
    with client.websocket_connect("/ws/test?token=good-token") as socket:
        assert socket.receive_json() == {"hello": "anna"}
    assert seen == [USERS["good-token"]]


def test_the_panel_cookie_signs_in_from_a_page_of_this_server():
    client, seen = build()
    client.cookies.set(panel_session.SESSION_COOKIE, "good-token")
    with client.websocket_connect("/ws/test", headers={"origin": "http://testserver"}) as socket:
        assert socket.receive_json() == {"hello": "anna"}
    assert seen == [USERS["good-token"]]


def test_the_panel_cookie_from_another_site_is_refused():
    client, seen = build()
    client.cookies.set(panel_session.SESSION_COOKIE, "good-token")
    code, reason = refusal(client, "/ws/test", headers={"origin": "http://evil.example"})
    assert (code, reason) == (1008, ws_auth.REASON_CROSS_ORIGIN)
    assert seen == []


def test_nothing_to_sign_in_with_is_refused():
    client, seen = build()
    code, reason = refusal(client, "/ws/test", headers={"origin": "http://testserver"})
    assert (code, reason) == (1008, ws_auth.REASON_MISSING_TOKEN)
    assert seen == []


def test_an_invalid_token_is_refused():
    client, seen = build()
    code, reason = refusal(client, "/ws/test?token=forged")
    assert (code, reason) == (1008, ws_auth.REASON_INVALID_TOKEN)
    assert seen == []


def test_a_placeholder_token_falls_back_to_the_cookie():
    """The panel sends a placeholder where the token used to be."""
    placeholder = next(iter(panel_session.BEARER_PLACEHOLDERS))
    client, seen = build()
    client.cookies.set(panel_session.SESSION_COOKIE, "good-token")
    with client.websocket_connect(f"/ws/test?token={placeholder}",
                                  headers={"origin": "http://testserver"}) as socket:
        socket.receive_json()
    assert seen == [USERS["good-token"]]


class FakeSocket:
    def __init__(self, query=None, headers=None, cookies=None):
        self.query_params = query or {}
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.closed = None

    async def close(self, code, reason):
        self.closed = (code, reason)


def test_authenticate_websocket_closes_and_answers_none():
    socket = FakeSocket(query={"token": "forged"})
    assert asyncio.run(ws_auth.authenticate_websocket(socket)) is None
    assert socket.closed == (1008, ws_auth.REASON_INVALID_TOKEN)


def test_a_route_without_the_flag_is_registered_as_is():
    from types import SimpleNamespace

    from keepup.plugins.registry import register_plugin_routes

    async def own(websocket):
        pass

    async def guarded(websocket, current_user):
        pass

    manager = SimpleNamespace(
        get_all_api_routes=lambda: [],
        get_all_websocket_routes=lambda: [
            {"path": "/ws/own", "handler": own},
            {"path": "/ws/guarded", "handler": guarded, "require_auth": True},
        ])
    app = FastAPI()
    asyncio.run(register_plugin_routes(app, manager))
    endpoints = {route.path: route.endpoint for route in app.routes if route.path.startswith("/ws/")}
    assert endpoints["/ws/own"] is own
    assert endpoints["/ws/guarded"] is not guarded
