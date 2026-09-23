"""Who is on the other end of a WebSocket.

A handshake carries no Authorization header a browser would let a page set, so
a socket is signed in by one of two things, in this order:

* a real token in the address (`?token=`) -- what programmatic clients send;
* the panel session cookie -- what the panel sends. A handshake is not bound by
  CORS, so the cookie counts only when the page that opened the socket is
  served from this same server; otherwise any site the user visits could open
  a socket as them.

Whatever the reason for refusing, the socket is closed with 1008 (policy
violation) and a short reason; the reasons are the ones the panel and the logs
already know from the first socket that did this by hand.

A plugin gets this without calling anything: a WebSocket route declared with
`require_auth: True` is signed in by the framework before its handler runs
(keepup/plugins/registry.py).
"""

from typing import Any, Dict, Optional

from fastapi import HTTPException

from keepup.auth import dependencies, panel_session

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "WebSocketRefused",
    "authenticate_websocket",
]

#: WebSocket close code for "policy violation": the connection is refused on
#: grounds of who is asking, not because something broke.
WS_CLOSE_POLICY_VIOLATION = 1008

REASON_CROSS_ORIGIN = "Cross-origin session"
REASON_MISSING_TOKEN = "Missing token"
REASON_INVALID_TOKEN = "Invalid token"


class WebSocketRefused(Exception):
    """The socket is not signed in; `code` and `reason` are what it is closed with."""

    def __init__(self, reason: str, code: int = WS_CLOSE_POLICY_VIOLATION):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _token_of(websocket) -> str:
    token = panel_session.real_bearer(websocket.query_params.get("token"))
    if token:
        return token
    if not panel_session.same_origin(websocket.headers):
        raise WebSocketRefused(REASON_CROSS_ORIGIN)
    token = panel_session.websocket_token(None, websocket.cookies)
    if not token:
        raise WebSocketRefused(REASON_MISSING_TOKEN)
    return token


async def websocket_user(websocket) -> Dict[str, Any]:
    """The signed-in user of this socket, or `WebSocketRefused`.

    The user is resolved through `dependencies.get_current_user` looked up at
    call time, so whatever replaces it (a test, another provider) is honoured.
    """
    token = _token_of(websocket)
    try:
        user = await dependencies.get_current_user(token=token)
    except HTTPException:
        raise WebSocketRefused(REASON_INVALID_TOKEN)
    if not (user or {}).get("id"):
        raise WebSocketRefused(REASON_INVALID_TOKEN)
    return user


async def authenticate_websocket(websocket) -> Optional[Dict[str, Any]]:
    """The user of this socket, or None after closing it with the reason."""
    try:
        return await websocket_user(websocket)
    except WebSocketRefused as refused:
        await websocket.close(code=refused.code, reason=refused.reason)
        return None
