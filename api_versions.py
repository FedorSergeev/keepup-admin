"""API versions: the version in the path, and the paths without one.

Clients cannot be updated together with the server: an agent is updated by the
host owner copying an archive, the client inside a VM by rebuilding the image.
So the model on the wire is pinned to a version named in the path -- `/api/v1/…`
for HTTP, `/ws/v1/…` for websockets -- and a later incompatible model gets a new
prefix while old clients keep talking to theirs.

v1 is the model the routes have today, and their handlers do not know about it:
this middleware maps `/api/v1/x` onto `/api/x` before routing, for the routes of
the application and of every plugin at once, so no route dictionary changes. A
path without a version stays an alias of v1 for as long as v1 exists (decision of
17.09.2026): the panel is served with the server and has no reason to move, and
agents already installed must not break. Its HTTP answers say so with a
`Deprecation` header and a link to the versioned path.

A future v2 route is registered under its own `/api/v2/…` path and is never
rewritten. `/api/v2/websocket/token` and `/ws/v2/websocket` predate this scheme:
they are the second version of the guest channel, whose first version is gone,
and are listed as such in `/api/versions`.
"""

from typing import Any, Dict, List

CURRENT = "v1"

#: Every version the server speaks. `sunset` is when a deprecated version stops
#: being served; None means no date is set.
VERSIONS: List[Dict[str, Any]] = [
    {"version": "v1", "status": "current", "http_prefix": "/api/v1", "websocket_prefix": "/ws/v1",
     "sunset": None},
]

#: Paths that carry their own version outside the scheme above.
CHANNEL_VERSIONS: List[Dict[str, str]] = [
    {"channel": "guest", "version": "v2", "paths": "/api/v2/websocket/token, /ws/v2/websocket"},
]

#: Versioned prefix -> the unversioned prefix the routes are registered under.
ALIASES = {"/api/v1": "/api", "/ws/v1": "/ws"}

#: Unversioned paths that are not an alias of v1 and get no deprecation notice.
NOT_DEPRECATED = ("/api/versions", "/api/v2/", "/api/v3/")


def unversioned(path: str):
    """The registered path for a versioned one, or None when the path has no v1 prefix."""
    for versioned, plain in ALIASES.items():
        if path == versioned or path.startswith(versioned + "/"):
            return plain + path[len(versioned):]
    return None


def successor(path: str):
    """The versioned path a deprecated unversioned one should become, or None."""
    if path.startswith(NOT_DEPRECATED) or path == "/api":
        return None
    for versioned, plain in ALIASES.items():
        if path.startswith(plain + "/") and not path.startswith(versioned + "/"):
            return versioned + path[len(plain):]
    return None


def describe() -> Dict[str, Any]:
    return {"current": CURRENT, "versions": VERSIONS, "channels": CHANNEL_VERSIONS,
            "unversioned_paths": "alias of " + CURRENT}


class ApiVersionMiddleware:
    """Route `/api/v1/…` and `/ws/v1/…` to the handlers, mark unversioned answers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        plain = unversioned(path)
        if plain is not None:
            scope = dict(scope)
            scope["path"] = plain
            scope["raw_path"] = plain.encode("utf-8")
            scope["api_version"] = CURRENT
            return await self.app(scope, receive, send)

        target = successor(path) if scope["type"] == "http" else None
        if target is None:
            return await self.app(scope, receive, send)

        async def send_with_notice(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"deprecation", b"true"))
                headers.append((b"link", f'<{target}>; rel="successor-version"'.encode("utf-8")))
                message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, send_with_notice)
