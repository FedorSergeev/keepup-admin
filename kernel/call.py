"""One invocation of a declared route, whatever carried it.

A route is data -- a path, methods, a handler, a mask, a right, a few flags --
and what happens between the wire and the handler is the same whoever carried
it: the mask decides what the handler receives, the caller is injected, a write
gets its body, the right is checked, and the handler's answer goes back. What
differs is only how a transport reads those four things (the parameters, the
caller, the body, the source) and how it renders a refusal.

This module is that same part, and it holds no transport: no Request, no
Response, no HTTP status. A transport builds a :class:`Call`, hands it to
:func:`invoke`, and maps :class:`CallError` to its own vocabulary -- HTTPException
for the HTTP adapter, a gRPC status for another (`doc/plugin_constructor.md`
section 5.1).
"""

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

__all__ = [
    "Call",
    "CallError",
    "KIND_DELETE",
    "KIND_READ",
    "KIND_WRITE",
    "RouteSpec",
    "WRITE_METHODS",
    "admit",
    "invoke",
    "route_kind",
]

#: The three shapes a route takes, as the audit and the body limit see them.
KIND_READ = "read"
KIND_WRITE = "write"
KIND_DELETE = "delete"

#: The methods whose body the framework reads and hands to the handler.
WRITE_METHODS = ("POST", "PUT", "PATCH")


class CallError(Exception):
    """A call the framework refuses before the handler runs.

    The refusal is transport-neutral: a transport turns it into its own
    vocabulary. ``http_status`` is a hint for the one that speaks HTTP, not a
    claim that every transport has status codes.

    Attributes:
        detail: what is wrong, in the words the caller will read.
        code: a stable name for the kind of refusal.
        http_status: what an HTTP transport should answer.
    """

    def __init__(self, detail: str, code: str = "invalid_request", http_status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.http_status = http_status


def route_kind(methods: Sequence[str]) -> Tuple[Optional[str], Optional[str]]:
    """Which of the three shapes a route is, and the method the audit records.

    A GET is a read; a write method makes it a write -- the first one declared,
    so a route that takes POST and PUT is logged by the one it named first; a
    DELETE-only route is a delete. Anything else is not a route the runtime
    knows, and the caller answers 501.

    Args:
        methods: the methods the route declares.

    Returns:
        ``(kind, method_name)``, either of which may be None.
    """
    methods = [str(method).upper() for method in methods or ()]
    if not methods:
        return None, None
    if "GET" in methods:
        return KIND_READ, methods[0]
    for method in methods:
        if method in WRITE_METHODS:
            return KIND_WRITE, methods[0]
    if "DELETE" in methods:
        return KIND_DELETE, methods[0]
    return None, None


@dataclass(frozen=True)
class RouteSpec:
    """A declared route, as the runtime reads it.

    Attributes:
        path: the path as declared, parameters and all.
        methods: the methods it answers.
        handler: the plugin's callable.
        mask: the parsed request mask, or None.
        permission: the right the caller must hold, or None.
        require_auth: whether the transport signs the caller in.
        is_upload: whether the handler is handed the body as it arrived.
        raw_request: whether the handler takes the request itself and answers
            with whatever it likes -- the transport's own business, never
            invoked through :func:`invoke`.
        include_in_schema: whether the route belongs in the API schema.
        max_body_bytes: the largest body it accepts, or None.
        audit: whether a call of it is written to the incoming-request audit.
        response_media_type: what it answers, when that is not JSON.
        plugin_id: who declared it.
    """

    path: str
    methods: Tuple[str, ...]
    handler: Callable
    mask: Any = None
    permission: Optional[str] = None
    require_auth: bool = True
    is_upload: bool = False
    raw_request: bool = False
    include_in_schema: bool = True
    max_body_bytes: Optional[int] = None
    audit: bool = True
    response_media_type: Optional[str] = None
    plugin_id: str = ""

    @classmethod
    def of(cls, route: Mapping[str, Any], plugin_id: str = "") -> "RouteSpec":
        """Read a route dictionary.

        Args:
            route: what the plugin declared.
            plugin_id: who declared it, as the collector recorded.

        Returns:
            The specification.
        """
        return cls(
            path=str(route["path"]),
            methods=tuple(str(method).upper() for method in route.get("methods") or ()),
            handler=route["handler"],
            mask=route.get("_mask"),
            permission=route.get("permission"),
            require_auth=bool(route.get("require_auth", True)),
            is_upload=bool(route.get("is_upload", False)),
            raw_request=bool(route.get("raw_request", False)),
            include_in_schema=bool(route.get("include_in_schema", True)),
            max_body_bytes=route.get("max_body_bytes"),
            audit=bool(route.get("audit", True)),
            response_media_type=route.get("response_media_type"),
            plugin_id=str(route.get("_plugin_id") or plugin_id),
        )

    @property
    def kind(self) -> Optional[str]:
        """Which of the three shapes this route is."""
        return route_kind(self.methods)[0]

    @property
    def method_name(self) -> Optional[str]:
        """The method the audit records for a call of this route."""
        return route_kind(self.methods)[1]


@dataclass(frozen=True)
class Call:
    """One invocation of a declared route, whatever carried it.

    Attributes:
        route: the declaration.
        params: what the transport read from its envelope -- path and query
            values for HTTP, the message fields for another -- before the mask.
        actor: who is calling, or None for a public route.
        body: the parsed body for a write, or whatever the transport carries.
        source: which transport carried it, for the audit and the logs.
    """

    route: RouteSpec
    params: Mapping[str, Any] = field(default_factory=dict)
    actor: Any = None
    body: Any = None
    source: str = "http"


def admitted(handler: Callable, params: Mapping[str, Any]) -> Dict[str, Any]:
    """The parameters a handler takes, refusing the ones it does not.

    A handler that takes ``**kwargs`` is asking for everything and gets it.

    Args:
        handler: the plugin's callable.
        params: what the transport read.

    Returns:
        The parameters to pass.

    Raises:
        CallError: naming the parameters the route does not take.
    """
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        # A callable that cannot be inspected (a builtin, a C extension) is left
        # alone: refusing it would break a plugin over introspection.
        return dict(params)

    parameters = signature.parameters.values()
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters):
        return dict(params)

    declared = {p.name for p in parameters
                if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.KEYWORD_ONLY)}
    # Supplied by the framework, never by the caller: a client sending
    # ?current_user=1 used to get a 500 out of the duplicate argument.
    supplied = params.keys() - declared - {"current_user"}
    if supplied:
        raise CallError(f"Unknown parameter(s): {', '.join(sorted(supplied))}")
    return {name: value for name, value in params.items() if name != "current_user"}


def admit(route: RouteSpec, params: Mapping[str, Any]) -> Dict[str, Any]:
    """What reaches the handler: the mask decides, or the signature still does.

    Args:
        route: the declaration.
        params: what the transport read.

    Returns:
        The parameters to pass.

    Raises:
        CallError: naming everything the call got wrong.
    """
    if route.mask is None:
        return admitted(route.handler, params)
    values, complaints = route.mask.admit(params)
    if complaints:
        raise CallError("; ".join(complaints))
    return values


async def invoke(call: Call, *, checker: Callable = None) -> Any:
    """Run the handler of a declared route.

    Args:
        call: the invocation, with the parameters the transport read, the
            caller and the body.
        checker: ``await checker(actor, AccessRequest)`` -- what decides a
            route's ``permission``. Required for a route that declares one: a
            right nobody checks is worse than a route that refuses to run.

    Returns:
        Whatever the handler answered. Shaping it for a wire format is the
        transport's business.

    Raises:
        CallError: when a parameter is wrong, or the route declares a right
            nobody can check.
        Exception: whatever the handler raises; a refusal of its own travels
            as it is.
    """
    route = call.route
    if route.raw_request:
        raise CallError(
            f"{route.path}: a raw_request route is handed to its transport, not invoked",
            code="not_invocable",
            http_status=501,
        )
    params = admit(route, call.params)
    if call.actor is not None:
        params["current_user"] = call.actor
    if route.kind == KIND_WRITE:
        params["request"] = call.body
    if route.permission:
        if checker is None:
            raise CallError(
                f"{route.path}: declares the permission {route.permission!r} and the "
                "transport checked nothing",
                code="unchecked_permission",
                http_status=500,
            )
        await checker(call.actor, access_request(call))
    return await route.handler(**params)


def access_request(call: Call) -> Any:
    """The action a call takes, in the shape the identity provider is asked about.

    Args:
        call: the invocation.

    Returns:
        An ``AccessRequest``; imported here so that the neutral module does not
        hold the identity system, only the shape of the question.
    """
    from keepup.auth.identity.contract import AccessRequest

    return AccessRequest(
        permission=call.route.permission,
        method=call.route.method_name or "",
        path=call.route.path,
        path_params={key: value for key, value in call.params.items()
                     if "{" + key + "}" in call.route.path},
    )
