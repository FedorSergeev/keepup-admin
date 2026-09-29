"""How large a request body a route accepts, checked before the body is read.

``KeepupSettings.max_upload_bytes`` was declared and enforced nowhere: every
route read whatever it was sent. It is enforced here, as ASGI middleware, and
per route (keepup-49):

- **A route that declares ``max_body_bytes`` gets exactly that** -- a number of
  bytes, or ``None`` for no limit. That is how a route that legitimately takes
  gigabytes (a machine image) says so, and how one that takes a few kilobytes
  asks for less.
- **A route whose body the framework reads** -- a plain write route, whose JSON
  is parsed before the handler runs, and every route of the framework itself --
  gets the application's ``max_upload_bytes`` when it declares nothing.
- **A route that reads its own body** (``is_upload``, ``raw_request``) and
  declares nothing gets no limit from the framework. Such routes stream files
  and proxy other people's traffic; a general number applied to them the day
  the framework starts enforcing would cut off an upload nobody had sized, so
  each of them states its own.

The check comes before the body is read. A request whose ``Content-Length``
already says too much is answered 413 without the application seeing it. A
body without one (chunked) is counted as it arrives: once it passes the limit
the application is told the client went away, and whatever it answers is
replaced with the 413 -- the handler may have run with a partial read, which is
why a declared length is the path that matters.
"""

import json
import logging
from typing import Optional

from starlette.routing import Match

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "BodyLimitMiddleware",
    "ROUTE_LIMIT_ATTRIBUTE",
    "declare_limit",
]

logger = logging.getLogger(__name__)

#: The attribute a route's endpoint carries when the route declared a limit.
ROUTE_LIMIT_ATTRIBUTE = "keepup_max_body_bytes"
#: The attribute of an endpoint that reads its own body.
READS_OWN_BODY_ATTRIBUTE = "keepup_reads_own_body"

_NOT_DECLARED = object()
#: Methods whose requests are not checked. Everything else is: a route declared
#: with OPTIONS or a custom method beside a write reads its body as well.
_BODYLESS_METHODS = {"GET", "HEAD"}


def declare_limit(endpoint, route: dict) -> None:
    """Mark a plugin route's endpoint with what its declaration says about bodies."""
    limit = route.get('max_body_bytes', _NOT_DECLARED)
    if limit is not _NOT_DECLARED:
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool)
                                  or limit <= 0):
            raise ValueError(f"{route.get('path')}: max_body_bytes must be a positive "
                             f"number of bytes or None, not {limit!r}")
        setattr(endpoint, ROUTE_LIMIT_ATTRIBUTE, limit)
    if route.get("is_upload") or route.get("raw_request"):
        setattr(endpoint, READS_OWN_BODY_ATTRIBUTE, True)


class BodyLimitMiddleware:
    """Refuses a body larger than its route accepts, before the route reads it."""

    def __init__(self, app, router_of=None, default_limit: Optional[int] = None):
        self.app = app
        self.router_of = router_of
        self.default_limit = default_limit

    def limit_for(self, scope) -> Optional[int]:
        """The limit of the route this request goes to; None for none."""
        router = getattr(self.router_of, "router", None)
        for route in getattr(router, "routes", ()):
            match, _ = route.matches(scope)
            if match == Match.FULL:
                endpoint = getattr(route, "endpoint", None)
                declared = getattr(endpoint, ROUTE_LIMIT_ATTRIBUTE, _NOT_DECLARED)
                if declared is not _NOT_DECLARED:
                    return declared
                if getattr(endpoint, READS_OWN_BODY_ATTRIBUTE, False):
                    return None
                return self.default_limit
        return self.default_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in _BODYLESS_METHODS:
            return await self.app(scope, receive, send)
        limit = self.limit_for(scope)
        if limit is None:
            return await self.app(scope, receive, send)

        declared = _content_length(scope)
        if declared is not None and declared > limit:
            logger.warning("Request body refused: %s %s declares %s bytes, the route "
                           "accepts %s", scope.get("method"), scope.get("path"),
                           declared, limit)
            return await _answer_too_large(send, limit)

        state = {"received": 0, "over": False, "started": False}

        async def counted_receive():
            if state["over"]:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > limit:
                    state["over"] = True
                    logger.warning("Request body refused: %s %s passed %s bytes without "
                                   "declaring its length", scope.get("method"),
                                   scope.get("path"), limit)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            if state["over"]:
                return
            if message["type"] == "http.response.start":
                state["started"] = True
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except Exception:
            if not state["over"]:
                raise
        if state["over"] and not state["started"]:
            await _answer_too_large(send, limit)


def _content_length(scope) -> Optional[int]:
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _answer_too_large(send, limit: int) -> None:
    body = json.dumps({"detail": f"Request body too large: this route accepts "
                                 f"at most {limit} bytes"}).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (b"connection", b"close")]})
    await send({"type": "http.response.body", "body": body})
