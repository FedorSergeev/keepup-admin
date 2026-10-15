"""The route table: what the gateway passes on, and where to.

A route is data -- the plugin's `config` names it -- and the table is checked
once, at the start, so a mistake in it leaves the gateway down and named in the
plugins' report instead of turning
into a 404 that somebody reports a week later. Pure: no network, no FastAPI.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

__all__ = [
    "AUTH_NONE",
    "AUTH_SESSION",
    "ADMIN_PREFIX",
    "ALL_METHODS",
    "IDEMPOTENT_METHODS",
    "SOURCE_CONFIG",
    "SOURCE_REGISTERED",
    "SOURCE_LOCAL",
    "GatewayRoute",
    "TableError",
    "parse_table",
    "parse_route",
    "match",
]

#: Where a route came from: the plugin's config, a service that registered
#: itself over HTTP, or a plugin of this runtime that handles it in process.
SOURCE_CONFIG = "config"
SOURCE_REGISTERED = "registered"
SOURCE_LOCAL = "local"

AUTH_NONE = "none"
AUTH_SESSION = "session"
AUTH_KINDS = (AUTH_NONE, AUTH_SESSION)

ALL_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
#: Methods a second address may be asked again after the first one answered:
#: doing them twice leaves the service where doing them once would (RFC 9110 9.2.2).
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

#: The gateway's own addresses; a route may not cover them.
ADMIN_PREFIX = "/api/admin/gateway"
OWN_PREFIXES = (ADMIN_PREFIX, "/api/gateway", "/keepup-gateway")

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_COOLDOWN_SECONDS = 30.0
DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024


class TableError(ValueError):
    """The route table cannot be served as written."""


@dataclass(frozen=True)
class GatewayRoute:
    """One entry of the table.

    Attributes:
        id: the name an administrator reads in the report and in the log.
        prefix: the path this route answers, matched on a segment boundary.
        upstreams: the addresses requests are passed to, in turn.
        methods: the methods the route opens.
        strip_prefix: whether the service is sent the path without the prefix.
        auth: ``none`` or ``session`` -- whether the caller must be signed in.
        permission: the right a caller must hold, or None.
        timeout: seconds to wait for the service's answer to start.
        connect_timeout: seconds to wait for a connection.
        cooldown: seconds an address that failed is left out of the turn.
        max_body_bytes: the largest request body passed on.
        headers: headers added to every request of this route.
        application: whether a keepup application stands behind the route, so
            the panel shows a card of it (`applications.py`).
        source: ``config``, ``registered`` or ``local``.
        websocket: whether sockets to this prefix are carried as well as requests.
        group: the service a registered route belongs to -- one service holds
            several prefixes, and the panel shows it once.
        handler: for a local route, the callable of this runtime that answers it.
    """

    id: str
    prefix: str
    upstreams: Tuple[str, ...]
    methods: Tuple[str, ...] = ALL_METHODS
    strip_prefix: bool = False
    auth: str = AUTH_NONE
    permission: Optional[str] = None
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    cooldown: float = DEFAULT_COOLDOWN_SECONDS
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    headers: Mapping[str, str] = field(default_factory=dict)
    application: bool = False
    source: str = SOURCE_CONFIG
    group: str = ""
    websocket: bool = False
    handler: Optional[Callable[..., Any]] = field(default=None, compare=False, repr=False)

    @property
    def signed_in(self) -> bool:
        """Whether a caller must be signed in to pass."""
        return self.auth == AUTH_SESSION or self.permission is not None

    def covers(self, path: str) -> bool:
        """Whether a path falls under this route, on a segment boundary."""
        return path == self.prefix or path.startswith(self.prefix + "/")

    def upstream_path(self, path: str) -> str:
        """The path the service is sent."""
        if not self.strip_prefix:
            return path
        rest = path[len(self.prefix):]
        return rest if rest.startswith("/") else "/" + rest

    def describe(self) -> dict:
        """The route as the administrator's report shows it."""
        return {
            "id": self.id,
            "prefix": self.prefix,
            "methods": list(self.methods),
            "strip_prefix": self.strip_prefix,
            "auth": self.auth,
            "permission": self.permission,
            "timeout": self.timeout,
            "max_body_bytes": self.max_body_bytes,
            "application": self.application,
            "source": self.source,
            "service": self.group or None,
            "websocket": self.websocket,
        }


def parse_table(entries: Optional[Iterable[Mapping[str, Any]]]) -> List[GatewayRoute]:
    """The table as the plugin's config declares it, checked.

    Args:
        entries: the ``routes`` list of the plugin's config.

    Returns:
        The routes, longest prefix first.

    Raises:
        TableError: naming the route, for anything that cannot be served.
    """
    routes: List[GatewayRoute] = []
    seen_prefixes, seen_ids = {}, set()
    for index, entry in enumerate(entries or ()):
        if not isinstance(entry, Mapping):
            raise TableError(f"gateway route #{index + 1} is not an object")
        route = _route(entry, index)
        if route.id in seen_ids:
            raise TableError(f"gateway route '{route.id}': the id is used twice")
        if route.prefix in seen_prefixes:
            raise TableError(f"gateway route '{route.id}': the prefix {route.prefix} is "
                             f"already route '{seen_prefixes[route.prefix]}'")
        seen_ids.add(route.id)
        seen_prefixes[route.prefix] = route.id
        routes.append(route)
    return sorted(routes, key=lambda route: len(route.prefix), reverse=True)


def parse_route(entry: Mapping[str, Any], index: int = 0) -> GatewayRoute:
    """One entry, checked the way the table checks each of its own.

    Raises:
        TableError: naming the route.
    """
    if not isinstance(entry, Mapping):
        raise TableError(f"gateway route #{index + 1} is not an object")
    return _route(entry, index)


def match(routes: Sequence[GatewayRoute], path: str) -> Optional[GatewayRoute]:
    """The route with the longest prefix covering the path, or None.

    Args:
        routes: the table, in any order.
        path: the request's path.
    """
    best = None
    for route in routes:
        if route.covers(path) and (best is None or len(route.prefix) > len(best.prefix)):
            best = route
    return best


def _route(entry: Mapping[str, Any], index: int) -> GatewayRoute:
    """One entry, or the reason it is refused."""
    prefix = entry.get("prefix")
    name = str(entry.get("id") or prefix or f"#{index + 1}")
    if not isinstance(prefix, str) or not prefix.startswith("/"):
        raise TableError(f"gateway route '{name}': prefix must be a path starting with '/'")
    prefix = prefix.rstrip("/")
    if not prefix:
        raise TableError(f"gateway route '{name}': the prefix '/' would cover the panel "
                         "and the framework's own routes")
    if ".." in prefix.split("/") or "{" in prefix or "}" in prefix:
        raise TableError(f"gateway route '{name}': the prefix {prefix} is not a plain path")
    for own in OWN_PREFIXES:
        if prefix == own or prefix.startswith(own + "/") or own.startswith(prefix + "/"):
            raise TableError(f"gateway route '{name}': the prefix {prefix} covers the "
                             f"gateway's own {own}")

    upstreams = entry.get("upstreams", entry.get("upstream"))
    if isinstance(upstreams, str):
        upstreams = [upstreams]
    if not upstreams or not isinstance(upstreams, (list, tuple)):
        raise TableError(f"gateway route '{name}': no upstream address")
    checked = tuple(_upstream(name, value) for value in upstreams)

    methods = entry.get("methods") or ALL_METHODS
    if isinstance(methods, str):
        methods = [methods]
    methods = tuple(str(method).upper() for method in methods)
    unknown = [method for method in methods if method not in ALL_METHODS]
    if unknown:
        raise TableError(f"gateway route '{name}': unknown methods {unknown}")

    auth = str(entry.get("auth") or AUTH_NONE)
    if auth not in AUTH_KINDS:
        raise TableError(f"gateway route '{name}': auth must be one of {list(AUTH_KINDS)}")
    permission = entry.get("permission")
    if permission is not None and (not isinstance(permission, str) or not permission.strip()):
        raise TableError(f"gateway route '{name}': permission must be a non-empty string")

    headers = entry.get("headers") or {}
    if not isinstance(headers, Mapping):
        raise TableError(f"gateway route '{name}': headers must be an object")
    if any(str(key).lower().startswith("x-keepup-") for key in headers):
        raise TableError(f"gateway route '{name}': X-Keepup-* headers are the gateway's own")

    return GatewayRoute(
        id=name,
        prefix=prefix,
        upstreams=checked,
        methods=methods,
        strip_prefix=_flag(name, entry, "strip_prefix"),
        auth=auth,
        permission=permission,
        timeout=_positive(name, entry, "timeout", DEFAULT_TIMEOUT_SECONDS),
        connect_timeout=_positive(name, entry, "connect_timeout",
                                  DEFAULT_CONNECT_TIMEOUT_SECONDS),
        cooldown=_positive(name, entry, "cooldown", DEFAULT_COOLDOWN_SECONDS),
        max_body_bytes=int(_positive(name, entry, "max_body_bytes", DEFAULT_MAX_BODY_BYTES)),
        headers={str(key): str(value) for key, value in headers.items()},
        application=_flag(name, entry, "application"),
        websocket=_flag(name, entry, "websocket"),
    )


def _upstream(name: str, value: Any) -> str:
    """One address, without a trailing slash, or the reason it is refused."""
    if not isinstance(value, str):
        raise TableError(f"gateway route '{name}': an upstream must be a URL")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise TableError(f"gateway route '{name}': upstream {value!r} is not an http(s) URL")
    if parts.query or parts.fragment:
        raise TableError(f"gateway route '{name}': upstream {value!r} carries a query")
    return value.rstrip("/")


def _flag(name: str, entry: Mapping[str, Any], key: str) -> bool:
    """A yes-or-no setting: a string "false" is a mistake, not a yes."""
    value = entry.get(key, False)
    if not isinstance(value, bool):
        raise TableError(f"gateway route '{name}': {key} must be true or false")
    return value


def _positive(name: str, entry: Mapping[str, Any], key: str, default: float) -> float:
    """A positive number from the entry, or its default."""
    value = entry.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise TableError(f"gateway route '{name}': {key} must be a positive number")
    return float(value)
