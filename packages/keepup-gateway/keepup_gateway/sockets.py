"""WebSocket through the gateway: the same table, the same edge, a different wire.

A route that says ``"websocket": true`` passes sockets on as well as requests.
The gateway matches the path, checks the sign-in and the right at the edge, and
connects to the route's addresses in turn -- an address that takes no connection
is left out and the next one tried, which is safe: nothing was sent yet. Only once
the service has accepted is the client accepted, with the subprotocol the service
chose; from then on every message is carried both ways as it is, and whichever
side closes, the other is closed with the same code.

A socket is not retried after it opened: what was said on it cannot be said
again to another address. The service sees the same headers a request would --
X-Forwarded-*, X-Request-Id and the signed identity -- minus the handshake's own,
which the client library writes for its connection.
"""

import asyncio
import logging
from typing import Any, Callable, List, Optional

from fastapi import HTTPException
from starlette.routing import Match
from starlette.websockets import WebSocket, WebSocketDisconnect

from keepup.kernel import security
from keepup_gateway import headers as edge
from keepup_gateway.identity import caller_of, sign_identity
from keepup_gateway.table import GatewayRoute, match, traverses

logger = logging.getLogger(__name__)

__all__ = ["SocketProxy", "POLICY_VIOLATION", "TRY_AGAIN_LATER"]

#: Closed before the handshake completed: the client sees a 403.
POLICY_VIOLATION = 1008
#: No address of the route took the socket.
TRY_AGAIN_LATER = 1013
#: The handshake's own headers: the client library writes them for its connection.
_HANDSHAKE = ("sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions",
              "sec-websocket-protocol", "sec-websocket-accept")


def _connect(uri: str, **options):
    """Open a socket to a service; a test passes its own."""
    from websockets.asyncio.client import connect

    return connect(uri, **options)


class SocketProxy:
    """The gateway's sockets for one table.

    Args:
        table: what reads the routes as they stand now.
        balancer: the turn and the cooldowns, shared with the requests.
        identity_secret: what the caller's headers are signed with.
        connect: what opens a socket to a service.
    """

    def __init__(self, table: Callable[[], Any], balancer, identity_secret: str = "",
                 connect: Callable[..., Any] = _connect):
        self.table = table
        self.balancer = balancer
        self.identity_secret = identity_secret
        self.connect = connect

    async def handle(self, websocket: WebSocket) -> None:
        """Carry one socket to the service its path is addressed to."""
        path = websocket.url.path
        if traverses(path):
            # Before the route is chosen: the socket client normalises a dot
            # segment the same way the request client does (keepup-170).
            await websocket.close(code=POLICY_VIOLATION)
            return
        route = match([route for route in await self.table() if route.websocket], path)
        if route is None:
            await _elsewhere(websocket)
            return
        request_id = edge.request_id_of(websocket.headers)
        added = dict(route.headers)
        if route.signed_in:
            subject = await self._signed_in(websocket, route)
            if subject is None:
                await websocket.close(code=POLICY_VIOLATION)
                return
            added.update(sign_identity(caller_of(subject), self.identity_secret, request_id))
        outgoing = [(name, value) for name, value in edge.to_upstream(
            websocket.headers.items(),
            client_host=websocket.client.host if websocket.client else "",
            scheme="https" if websocket.url.scheme == "wss" else "http",
            host=websocket.headers.get("host", ""), prefix=route.prefix,
            request_id=request_id, added=added) if name.lower() not in _HANDSHAKE]
        target = route.upstream_path(path) + (f"?{websocket.url.query}"
                                              if websocket.url.query else "")
        offered = list(websocket.scope.get("subprotocols") or [])

        upstream = await self._open(route, target, outgoing, offered)
        if upstream is None:
            await websocket.close(code=TRY_AGAIN_LATER)
            return
        try:
            await websocket.accept(subprotocol=getattr(upstream, "subprotocol", None),
                                   headers=[(edge.REQUEST_ID.lower().encode(),
                                             request_id.encode())])
            await _carry(websocket, upstream)
        finally:
            await _quietly(upstream.close())

    async def _open(self, route: GatewayRoute, target: str, headers: List, offered: List[str]):
        """The first address that takes the socket, or None."""
        for address in self.balancer.order(route.id, route.upstreams):
            uri = ("wss" if address.startswith("https:") else "ws") + address[address.index(":"):]
            try:
                upstream = await self.connect(
                    uri + target, additional_headers=headers,
                    subprotocols=offered or None, open_timeout=route.connect_timeout,
                    max_size=route.max_body_bytes)
            except Exception as error:  # noqa: BLE001 - any refusal moves on to the next
                self.balancer.failed(route.id, address, route.cooldown, str(error)[:200])
                logger.warning("Gateway route %s: %s took no socket (%s)", route.id, address,
                               error)
                continue
            self.balancer.answered(route.id, address)
            return upstream
        return None

    async def _signed_in(self, websocket: WebSocket, route: GatewayRoute) -> Optional[Any]:
        """The caller the kernel recognises, holding the route's right; or None."""
        dependency = security.subject_dependency()
        if dependency is None:
            return None
        try:
            subject = await dependency(websocket)
        except HTTPException:
            return None
        except Exception as error:  # noqa: BLE001 - an identity system that cannot read a socket
            logger.warning("Gateway route %s: the sign-in could not read a socket: %s",
                           route.id, error)
            return None
        if not subject:
            return None
        if route.permission is None:
            return subject
        checker = security.checker()
        if checker is None:
            return None
        try:
            allowed = await checker(subject, security.AccessRequest(
                permission=route.permission, method="GET", path=route.prefix))
        except HTTPException:
            return None
        return subject if allowed else None


async def _carry(websocket: WebSocket, upstream) -> None:
    """Every message both ways, until one side closes; the other closes the same way."""
    from websockets.exceptions import ConnectionClosed

    async def from_client():
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    await upstream.close(code=message.get("code") or 1000)
                    return
                if message.get("text") is not None:
                    await upstream.send(message["text"])
                elif message.get("bytes") is not None:
                    await upstream.send(message["bytes"])
        except (WebSocketDisconnect, ConnectionClosed):
            return

    async def from_service():
        try:
            async for message in upstream:
                if isinstance(message, (bytes, bytearray)):
                    await websocket.send_bytes(bytes(message))
                else:
                    await websocket.send_text(message)
            await _quietly(websocket.close(code=upstream.close_code or 1000,
                                           reason=upstream.close_reason or ""))
        except ConnectionClosed as closed:
            await _quietly(websocket.close(code=_code_of(closed),
                                           reason=getattr(closed.rcvd, "reason", "") or ""))
        except (WebSocketDisconnect, RuntimeError):
            return

    tasks = [asyncio.ensure_future(from_client()), asyncio.ensure_future(from_service())]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
        await _quietly(task)


def _code_of(closed) -> int:
    """The code the service closed with; 1011 when it went away without one."""
    received = getattr(closed, "rcvd", None)
    return getattr(received, "code", None) or 1011


async def _elsewhere(websocket: WebSocket) -> None:
    """A socket no gateway route takes: another route's, or refused as before."""
    app = websocket.scope.get("app")
    for candidate in getattr(getattr(app, "router", None), "routes", []) or []:
        if getattr(candidate, "path", None) == FALLBACK_SOCKET:
            continue
        found, child = candidate.matches(websocket.scope)
        if found == Match.FULL:
            websocket.scope.update(child)
            # The connection's own channels: the socket has not been accepted, so
            # the route behind receives the handshake as the router would hand it.
            await candidate.handle(websocket.scope, websocket._receive, websocket._send)
            return
    await websocket.close(code=POLICY_VIOLATION)


#: The fallback socket route: what registered services answer, unknown at mounting.
FALLBACK_SOCKET = "/{gateway_socket:path}"


async def _quietly(awaitable) -> None:
    """Wait for something whose failure does not matter any more."""
    try:
        await awaitable
    except BaseException:  # noqa: BLE001 - closing what is already closed
        return
