"""An open socket ends with its session (keepup-65).

A socket is signed in once, at the handshake. Before this, a logout, a password
change or a block refused every later request but left a socket opened with the
same token working. Against the session's throwaway SQLite database: sockets
signed in through the framework's door are held, and a revocation closes them.

    python3 -m pytest keepup/tests/socket_session_end_tests.py -v
"""

import asyncio
from datetime import timedelta

import pytest
from starlette.websockets import WebSocketDisconnect
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup.auth import dependencies, panel_session, socket_sessions
from keepup.plugins.routes import signed_in_websocket
from keepup.schema import init_db


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


def account(name):
    user = dependencies.get_user_by_username(name)
    if not user:
        uid = dependencies.save_user_to_db(name, "a-long-password-65")
        dependencies.update_user(uid, status="active")
        user = dependencies.get_user_by_username(name)
    return user


def token_for(user):
    sid = panel_session.open_session(user["id"], timedelta(hours=1))
    token = dependencies.create_access_token({"sub": user["username"], "sid": sid},
                                             expires_delta=timedelta(hours=1))
    return sid, token


class FakeSocket:
    """What sweep() needs of a socket: close()."""

    def __init__(self):
        self.closed = None

    async def close(self, code, reason):
        self.closed = (code, reason)


def test_a_revoked_session_closes_its_socket_and_only_it():
    user = account("socket-owner")
    sid_a, _ = token_for(user)
    sid_b, _ = token_for(user)
    a, b = FakeSocket(), FakeSocket()
    socket_sessions.hold(a, {"session_id": sid_a})
    socket_sessions.hold(b, {"session_id": sid_b})
    panel_session.revoke(sid_a)
    assert asyncio.run(socket_sessions.sweep()) == 1
    assert a.closed == (socket_sessions.CLOSE_CODE, socket_sessions.CLOSE_REASON)
    assert b.closed is None


def test_a_password_change_closes_every_socket_of_the_user():
    user, other = account("socket-changer"), account("socket-bystander")
    mine = [FakeSocket() for _ in range(3)]
    for socket in mine:
        socket_sessions.hold(socket, {"session_id": token_for(user)[0]})
    theirs = FakeSocket()
    socket_sessions.hold(theirs, {"session_id": token_for(other)[0]})
    panel_session.revoke_all(user["id"], panel_session.REASON_PASSWORD_CHANGED)
    asyncio.run(socket_sessions.sweep())
    assert all(socket.closed for socket in mine)
    assert theirs.closed is None


def test_a_socket_forgotten_by_its_handler_is_not_kept_alive():
    socket = FakeSocket()
    socket_sessions.hold(socket, {"session_id": "gone"})
    before = socket_sessions.held_count()
    del socket
    assert socket_sessions.held_count() == before - 1


def test_a_forged_wake_up_closes_nothing_that_is_live():
    user = account("socket-live")
    socket = FakeSocket()
    socket_sessions.hold(socket, {"session_id": token_for(user)[0]})
    asyncio.run(socket_sessions.on_envelope({"kind": socket_sessions.ENVELOPE_KIND}))
    asyncio.run(socket_sessions.sweep())
    assert socket.closed is None


def test_a_real_socket_is_closed_after_logout(monkeypatch):
    """End to end: the framework's door holds the socket, the sweeper ends it."""
    import threading
    monkeypatch.setenv(socket_sessions.RECHECK_ENV, "1")
    user = account("socket-e2e")
    sid, token = token_for(user)

    async def echo(websocket, current_user):
        await websocket.accept()
        try:
            while True:
                await websocket.send_text(await websocket.receive_text())
        except (WebSocketDisconnect, RuntimeError):
            pass

    app = FastAPI()
    app.router.add_websocket_route("/ws/echo", signed_in_websocket(echo))

    @app.on_event("startup")
    async def sweeper():
        asyncio.get_running_loop().create_task(socket_sessions.run_forever())

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/echo?token={token}") as ws:
            ws.send_text("before")
            assert ws.receive_text() == "before"
            panel_session.revoke(sid)

            closed = {}

            def listen():
                try:
                    ws.receive_text()
                except WebSocketDisconnect as error:
                    closed["code"] = error.code

            # A deadline rather than a blocking receive: a socket that is never
            # closed must fail the test, not hang it.
            listener = threading.Thread(target=listen, daemon=True)
            listener.start()
            listener.join(timeout=5)
            assert closed.get("code") == socket_sessions.CLOSE_CODE, "the socket outlived its session"
