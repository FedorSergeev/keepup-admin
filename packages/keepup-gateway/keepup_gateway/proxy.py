"""Passing one request on: the route, the edge, the addresses in turn, the answer.

The body is read whole, up to the route's limit, before an address is chosen:
that is what makes a second address possible without the client's help, and what
lets an oversized body be refused before any service sees it. The answer is
streamed back -- a download or an event stream may be long, and the gateway has
no reason to hold it.

A second address is asked only when that is safe. A connection that was never
made carried nothing, so any method moves on. An answer of 502, 503 or 504 moves
on only for an idempotent method: a POST may have been carried out before the
service failed to say so.
"""

import logging
from typing import Any, List, Optional, Sequence

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from keepup.kernel import security
from keepup_gateway import headers as edge
from keepup_gateway.balancer import Balancer
from keepup_gateway.identity import caller_of, sign_identity
from keepup_gateway.table import IDEMPOTENT_METHODS, GatewayRoute, match

logger = logging.getLogger(__name__)

__all__ = ["Proxy", "RETRY_STATUSES"]

#: Answers that say "this address, not this request".
RETRY_STATUSES = frozenset({502, 503, 504})


class _Refused(Exception):
    """The gateway answers the client itself."""

    def __init__(self, status: int, detail: str, headers: Optional[dict] = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.headers = headers or {}


class Proxy:
    """The gateway's work for one table.

    Args:
        routes: the checked table.
        balancer: the turn and the cooldowns.
        identity_secret: what the caller's headers are signed with.
        transport: an httpx transport, for a test; the network otherwise.
    """

    def __init__(self, routes: Sequence[GatewayRoute], balancer: Balancer,
                 identity_secret: str = "", transport: Optional[httpx.AsyncBaseTransport] = None):
        self.routes = list(routes)
        self.balancer = balancer
        self.identity_secret = identity_secret
        self.client = httpx.AsyncClient(transport=transport, follow_redirects=False)

    async def aclose(self) -> None:
        """Close the connections to the services."""
        await self.client.aclose()

    async def handle(self, request) -> Any:
        """Answer one request that reached a route of the gateway."""
        request_id = edge.request_id_of(request.headers)
        try:
            return await self._handle(request, request_id)
        except _Refused as refused:
            return JSONResponse({"detail": refused.detail}, status_code=refused.status,
                                headers={**refused.headers, edge.REQUEST_ID: request_id})

    async def _handle(self, request, request_id: str) -> Any:
        path = request.url.path
        route = match(self.routes, path)
        if route is None:
            raise _Refused(404, "no gateway route for this path")
        method = request.method.upper()
        if method not in route.methods:
            raise _Refused(405, f"route '{route.id}' does not open {method}",
                           {"Allow": ", ".join(route.methods)})

        added = dict(route.headers)
        if route.signed_in:
            subject = await self._signed_in(request, route, method)
            added.update(sign_identity(caller_of(subject), self.identity_secret, request_id))

        body = await _body(request, route.max_body_bytes)
        outgoing = edge.to_upstream(
            request.headers.items(),
            client_host=request.client.host if request.client else "",
            scheme=request.url.scheme,
            host=request.headers.get("host", ""),
            prefix=route.prefix,
            request_id=request_id,
            added=added,
        )
        target_path = route.upstream_path(path)
        query = request.url.query
        timeout = httpx.Timeout(route.timeout, connect=route.connect_timeout)

        addresses = self.balancer.order(route.id, route.upstreams)
        last = _Refused(503, f"route '{route.id}' has no address to pass the request to")
        for position, address in enumerate(addresses):
            more = position + 1 < len(addresses)
            url = address + target_path + (f"?{query}" if query else "")
            outbound = self.client.build_request(method, url, headers=outgoing,
                                                  content=body, timeout=timeout)
            try:
                answer = await self.client.send(outbound, stream=True)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
                self.balancer.failed(route.id, address, route.cooldown, _reason(error))
                logger.warning("Gateway route %s: %s took no connection (%s)",
                               route.id, address, _reason(error))
                last = _Refused(502, f"route '{route.id}': the service did not answer")
                continue
            except httpx.TimeoutException as error:
                self.balancer.failed(route.id, address, route.cooldown, _reason(error))
                last = _Refused(504, f"route '{route.id}': the service did not answer in time")
                if method in IDEMPOTENT_METHODS and more:
                    continue
                raise last
            except httpx.HTTPError as error:
                self.balancer.failed(route.id, address, route.cooldown, _reason(error))
                last = _Refused(502, f"route '{route.id}': the service broke the connection")
                if method in IDEMPOTENT_METHODS and more:
                    continue
                raise last

            if answer.status_code in RETRY_STATUSES:
                self.balancer.failed(route.id, address, route.cooldown,
                                     f"answered {answer.status_code}")
                if method in IDEMPOTENT_METHODS and more:
                    await answer.aclose()
                    continue
            else:
                self.balancer.answered(route.id, address)
            return _streamed(answer, request_id)
        raise last

    async def _signed_in(self, request, route: GatewayRoute, method: str) -> Any:
        """The caller the kernel recognises, holding the route's right; or a refusal."""
        dependency = security.subject_dependency()
        if dependency is None:
            raise _Refused(401, "sign-in is not configured")
        try:
            subject = await dependency(request)
        except HTTPException as error:
            raise _Refused(error.status_code if error.status_code in (401, 403) else 401,
                           str(error.detail or "not signed in"))
        if not subject:
            raise _Refused(401, "not signed in")
        if route.permission is None:
            return subject
        checker = security.checker()
        if checker is None:
            raise _Refused(403, "this deployment cannot decide rights")
        asked = security.AccessRequest(permission=route.permission, method=method,
                                       path=route.prefix)
        try:
            allowed = await checker(subject, asked)
        except HTTPException as error:
            raise _Refused(403 if error.status_code != 401 else 401,
                           str(error.detail or "not allowed"))
        if not allowed:
            raise _Refused(403, "not allowed")
        return subject


async def _body(request, limit: int) -> bytes:
    """The request's body, or 413 as soon as it is longer than the route allows."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise _Refused(413, f"the body is larger than {limit} bytes")
    chunks: List[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise _Refused(413, f"the body is larger than {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _streamed(answer: httpx.Response, request_id: str) -> StreamingResponse:
    """The service's answer, passed on as it arrives."""
    # A transport that hands the answer over already read (a test's, a cache's)
    # has nothing left to stream; what it read is passed on as it is.
    body = _once(answer.content) if answer.is_stream_consumed else answer.aiter_raw()
    response = StreamingResponse(body, status_code=answer.status_code,
                                 background=BackgroundTask(answer.aclose))
    # Set as raw pairs: a name may repeat (Set-Cookie), which a mapping would lose.
    passed = edge.to_client(answer.headers.multi_items(), request_id)
    if answer.is_stream_consumed:
        # What was read is already decoded: the encoding no longer describes it.
        passed = [(name, value) for name, value in passed
                  if name.lower() != "content-encoding"]
    response.raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in passed
    ]
    return response


async def _once(content: bytes):
    """One chunk, as a stream."""
    yield content


def _reason(error: Exception) -> str:
    """A failure as one short line."""
    text = str(error) or type(error).__name__
    return text[:200]
