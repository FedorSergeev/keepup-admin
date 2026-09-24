"""How a declared route becomes an endpoint of the application.

A plugin's routes are data -- a path, a set of methods, a handler and a few
flags -- and this module is the single place where that data turns into
something FastAPI will call. The wrapper it builds signs the caller in, unpacks
the path and the query into the handler's arguments, parses a body when the
method carries one, and writes the call to the incoming request audit.

Single is the point. There used to be eight nearly identical wrappers here,
one per shape of route, and every change had to be made in all of them: the
request mask added a hundred and fifty lines by being added eight times, and
two of the eight had drifted apart from the rest without anybody noticing
(keepup-21). Every route of every plugin of every application is born here, so
what this module does once, it does to all of them.
"""

import inspect
import logging

from fastapi import Depends, HTTPException, Request

from keepup.audit import IncomingRequestLogger, log_api_request
from keepup.auth.dependencies import get_panel_user
from keepup.auth.websocket import authenticate_websocket
from keepup.plugins import route_mask

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "accepted_params",
    "raw_request_wrapper",
    "register_plugin_routes",
]

logger = logging.getLogger(__name__)


def raw_request_wrapper(handler):
    """A route whose handler takes the request as it is and answers as it likes.

    For surfaces with a wire format of their own -- the OpenAI-compatible gateway
    answers errors in OpenAI's shape and streams server-sent events. The handler
    authenticates the caller itself, and the body is not written to the incoming
    request log: it carries users' prompts.
    """
    async def wrapper_raw(request: Request):
        return await handler(request=request)

    return wrapper_raw


def accepted_params(handler, params):
    """The parameters this handler declares, refusing the ones it does not.

    A plugin route is data, so the framework unpacks what arrived into keyword
    arguments -- and an argument the handler never heard of used to raise
    TypeError, which left the client with a 500 and the audit table with a row.
    Sending `?nosuch=1` in a loop was a cheap way to fill the database and the
    log at once, and on a route with `require_auth: False` anybody could.

    A handler that takes **kwargs is asking for everything and gets it.

    Args:
        handler: the plugin's callable.
        params: what arrived in the path and the query.

    Returns:
        The parameters to pass.

    Raises:
        HTTPException: 400, naming the parameters the route does not take.
    """
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        # A callable that cannot be inspected (a builtin, a C extension) is
        # left alone: refusing it would break a plugin over introspection.
        return params

    parameters = signature.parameters.values()
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters):
        return params

    declared = {p.name for p in parameters
                if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.KEYWORD_ONLY)}
    # Supplied by the framework, never by the caller: a client sending
    # ?current_user=1 used to get a 500 out of the duplicate argument.
    supplied = params.keys() - declared - {"current_user"}
    if supplied:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown parameter(s): {', '.join(sorted(supplied))}")
    return {name: value for name, value in params.items() if name != "current_user"}


def admit_params(handler, mask, params):
    """What reaches the handler: the mask decides, or the signature still does.

    A route that declared a mask is checked against it -- names, types,
    required, allowed values -- and the values arrive converted. A route
    without one keeps the previous behaviour exactly, which is the condition of
    the change: the applications convert their routes by their own tasks, and a
    framework that changed the default would have turned every route of every
    application into a refusal at once. See doc/keepup.md.

    Args:
        handler: the plugin's callable.
        mask: the route's parsed mask, or None.
        params: what arrived in the path and the query.

    Returns:
        The parameters to pass.

    Raises:
        HTTPException: 400, naming everything the request got wrong.
    """
    if mask is None:
        return accepted_params(handler, params)

    values, complaints = mask.admit(params)
    if complaints:
        raise HTTPException(status_code=400, detail="; ".join(complaints))
    return values


#: What the runtime does with a route, decided by the methods it declares.
#: A route is one of three shapes: a read, a write (which carries a body) and
#: a deletion.
KIND_READ = "read"
KIND_WRITE = "write"
KIND_DELETE = "delete"

#: The methods that carry a body. PUT and PATCH go the way POST goes.
WRITE_METHODS = ("POST", "PUT", "PATCH")


def route_kind(methods):
    """The shape of a route, and the method name its calls are recorded under.

    The order of the checks is the order the eight wrappers used to be written
    in, and it is what decides a tie: a route declaring ["POST", "GET"] is a
    read and is recorded as GET, not as the first method it named.

    Args:
        methods: the methods the route declares.

    Returns:
        A (kind, method name) pair, or (None, None) for a set of methods the
        runtime has no wrapper for.
    """
    if "GET" in methods:
        return KIND_READ, "GET"
    if any(method in WRITE_METHODS for method in methods):
        return KIND_WRITE, methods[0]
    if "DELETE" in methods:
        return KIND_DELETE, "DELETE"
    return None, None


async def json_body(request: Request):
    """The body of a write, parsed, or an empty one.

    The content type decides, not the bytes: a handler written for a dictionary
    would otherwise be handed a string the first time somebody posted a form.
    A body that claims to be JSON and is not becomes empty rather than a
    refusal -- the plugin is handed {} and decides for itself, because a
    handler that needs a field says so by missing it.
    """
    if request.headers.get("content-type") != "application/json":
        return {}
    try:
        return await request.json()
    except Exception:
        # Not a bare except, which used to be here: CancelledError does not
        # inherit from Exception, and swallowing it turned a cancelled request
        # into an empty body and a handler that ran on anyway.
        return {}


def create_wrapper(handler, path, methods, require_auth: bool = True,
                   is_upload: bool = False, mask=None):
    """Build the endpoint FastAPI will call for one declared route.

    One implementation rather than one per shape. It used to be eight nearly
    identical copies -- three shapes x a path parameter x authentication -- and
    two of them had quietly drifted apart from the rest (keepup-21).

    Two signatures remain, and only because FastAPI builds an endpoint out of
    one: a route that is signed in has to declare the dependency, and a public
    route must not.

    Args:
        handler: the plugin's callable.
        path: the route's path as declared, parameters and all.
        methods: the methods the route declares.
        require_auth: whether the framework signs the caller in.
        is_upload: whether the handler is handed the request instead of a body.
        mask: the route's parsed mask, or None.

    Returns:
        An async endpoint to hand to app.add_api_route().
    """
    kind, method_name = route_kind(methods)
    if kind is None:
        logger.warning(f"No matching wrapper found for {path} with methods {methods}")

        # It takes nothing on purpose. Written as (*args, **kwargs) it never
        # answered 501 at all: FastAPI reads a signature to build the endpoint,
        # so those two became required query parameters and the route answered
        # 422 "args, kwargs: field required" (keepup-20).
        async def fallback_wrapper():
            raise HTTPException(status_code=501,
                                detail=f"Not implemented for methods {methods}")

        return fallback_wrapper

    has_path_params = any('{' in part and '}' in part for part in path.split('/'))

    async def call(request: Request, current_user):
        """One call of a plugin route, from the audit's first row to its last."""
        request_data = {
            'user_id': current_user.get('id') if current_user else None,
            'username': current_user.get('username') if current_user else 'anonymous',
        }
        if has_path_params:
            request_data['path_params'] = dict(request.path_params)
        request_data['query_params'] = dict(request.query_params)
        if kind == KIND_WRITE:
            request_data['is_upload'] = is_upload

        async with log_api_request(
                method=method_name,
                endpoint=path,
                host='plugin_api',
                request_data=request_data
        ) as request_id:
            try:
                params = {}
                # The query first and the path over it: a value taken from the
                # address is what the route matched on, and a client that sends
                # ?id=99 to /api/x/5 used to reach the handler with 99 while
                # every log and every rule built on the path still said 5.
                params.update(request.query_params)
                params.update(request.path_params)
                params = admit_params(handler, mask, params)

                if current_user is not None:
                    params['current_user'] = current_user
                if kind == KIND_WRITE:
                    # An upload is handed the request itself: reading a file
                    # into memory to pass it as a value is what such a route
                    # exists to avoid.
                    params['request'] = request if is_upload else await json_body(request)

                response = await handler(**params)

                await IncomingRequestLogger.end_request(
                    request_id=request_id,
                    http_status=200,
                    response_data=response if isinstance(response, dict) else {"data": response}
                )

                return response
            except HTTPException as e:
                await IncomingRequestLogger.end_request(
                    request_id=request_id,
                    http_status=e.status_code,
                    error_message=e.detail
                )
                raise
            except Exception as e:
                await IncomingRequestLogger.end_request(
                    request_id=request_id,
                    http_status=500,
                    error_message=str(e)
                )
                raise

    if require_auth:
        async def wrapper_signed_in(request: Request,
                                    current_user: dict = Depends(get_panel_user)):
            return await call(request, current_user)

        return wrapper_signed_in

    async def wrapper_public(request: Request):
        return await call(request, None)

    return wrapper_public


async def register_plugin_routes(app, manager):
    """Register every plugin's API routes on the FastAPI application."""

    routes = manager.get_all_api_routes()
    logger.info(f"Found {len(routes)} plugin routes to register")

    for route in routes:
        handler = route['handler']
        path = route['path']
        methods = route['methods']
        require_auth = route.get('require_auth', True)
        is_upload = route.get('is_upload', False)

        logger.info(
            f"Registering route {path} - methods: {methods}, auth: {require_auth}, upload: {is_upload}")

        try:
            mask = route_mask.parse(route.get(route_mask.MASK_FIELD), path)
            route_mask.check_signature(handler, mask)
        except route_mask.MaskError as error:
            # The same loudness as the raw_request check below: a mask naming a
            # parameter the handler does not take is a typo, and finding it on
            # a request would mean hearing about it from a user.
            raise ValueError(f"{path}: {error}")

        if route.get('raw_request'):
            # A raw route authenticates inside the handler -- it is handed the
            # Request and nothing else. Declaring require_auth on it therefore
            # promises a check the framework does not make, and the start-up
            # line used to print auth=True over an open route (keepup-12).
            if route.get('require_auth') is True:
                raise ValueError(
                    f"{path}: a raw_request route cannot also declare "
                    "require_auth -- the handler receives the Request and "
                    "authenticates itself. Drop one of the two.")
            require_auth = False
            wrapper = raw_request_wrapper(handler)
        else:
            wrapper = create_wrapper(handler, path, methods, require_auth, is_upload, mask)

        app.add_api_route(
            path,
            wrapper,
            methods=methods,
            include_in_schema=route.get('include_in_schema', True)
        )

        logger.info(
            f"Registered plugin route: {path} - {methods} (auth={require_auth}, upload={is_upload})")

    websocket_routes = manager.get_all_websocket_routes()
    logger.info(f"Found {len(websocket_routes)} WebSocket plugin routes to register")

    for ws_route in websocket_routes:
        path = ws_route['path']
        handler = ws_route['handler']
        # Off by default, unlike HTTP routes: sockets of agents and guests carry
        # tokens of their own and would be refused by the panel's sign-in rule.
        if ws_route.get('require_auth', False):
            handler = signed_in_websocket(handler)
        _register_websocket(app, path, handler)
        logger.info(f"Registered WebSocket plugin route: {path} "
                    f"(auth={bool(ws_route.get('require_auth', False))})")


def _register_websocket(app, path, handler):
    """Register a socket route on the application, whichever Starlette it has.

    The route is appended as a plain ``WebSocketRoute`` rather than through the
    application's helpers, because neither helper works on both lines:
    Starlette 1.0 dropped ``add_websocket_route``, and FastAPI's
    ``add_api_websocket_route`` reads the handler's signature and refuses one
    whose socket parameter carries no annotation -- which a plugin's handler
    need not carry.

    It is done this way rather than by pinning Starlette below 1.0, because
    that pin is what held installations on a version with four published
    advisories (keepup-30).

    Args:
        app: The FastAPI application.
        path: The address the socket answers on.
        handler: The coroutine serving the connection.
    """
    from starlette.routing import WebSocketRoute

    app.router.routes.append(WebSocketRoute(path, handler))


def signed_in_websocket(handler):
    """Wrap a socket handler so it runs only for a signed-in user, who is passed in.

    Refusal closes the socket with 1008 before the handler sees it.
    """
    async def wrapper(websocket):
        user = await authenticate_websocket(websocket)
        if user is None:
            return
        await handler(websocket, current_user=user)

    return wrapper


# --- Post-construct and the administrative surface ----------------------------
#
# post_construct runs after the server is up, for the self-checks that need a
# live HTTP server; the endpoints below are what an administrator sees of the
# runtime -- which plugins are declared, which are running, and why one is not.
