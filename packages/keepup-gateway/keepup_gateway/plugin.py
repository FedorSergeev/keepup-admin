"""The gateway as a capability: one way in for the application's services.

A route comes from one of three places. The plugin's `config` names the fixed
ones; each is mounted as two declarations of the HTTP transport's ``raw_request``
kind -- the prefix and everything below it. A service that registers itself
(`registry.py`) and a plugin of this runtime that serves in process (`local.py`)
are not known when the transport mounts the routes, so they are reached through
one fallback route that receives only what nobody else answers: the plugin starts
last (its priority) and the fallback is the last route it declares. The handler
chooses the longest matching prefix from all three, so the answer does not depend
on the order anything was mounted in.

A table the gateway cannot serve leaves the plugin failed, with the reason in the
plugins' report, and mounts none of its routes: a half-served table would answer
some prefixes and drop others without saying so.

The same plugin is what an application *behind* a gateway installs. With an
`identity_secret` it answers `GET /api/gateway/self` -- its runtime's report -- to
the gateway's signed identity of an administrator (`applications.py`); with an
`announce` section it registers itself with that gateway (`announcer.py`).
"""

import logging
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from keepup.instance import get_instance_id
from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.plugins.base import BasePlugin
from keepup_gateway.announcer import REGISTER_PATH, Announcer, parse_announce
from keepup_gateway.applications import ADMIN_ROLE, SELF_PATH, Prober, describe_self
from keepup_gateway.balancer import Balancer
from keepup_gateway.identity import IdentityError, caller_of, verify_identity
from keepup_gateway.proxy import FALLBACK_PATH, Proxy
from keepup_gateway.sockets import FALLBACK_SOCKET, SocketProxy
from keepup_gateway.registry import (
    ACTION_DEREGISTER,
    ACTION_REGISTER,
    Registry,
    RegistrationError,
    declared_table,
    prefixes_of,
    verify_registration,
)
from keepup_gateway.table import (
    ADMIN_PREFIX,
    ALL_METHODS,
    DEFAULT_MAX_BODY_BYTES,
    SOURCE_LOCAL,
    GatewayRoute,
    TableError,
    parse_route,
    parse_table,
)

logger = logging.getLogger(__name__)

__all__ = ["GatewayPlugin", "GatewayService", "SERVICE_GATEWAY", "ADMIN_PERMISSION",
           "PRIORITY"]

#: What another plugin asks for to register a local service or read the table.
SERVICE_GATEWAY = "gateway"
#: The right the administrator's reports ask for.
ADMIN_PERMISSION = "gateway.admin"
#: Last of the plugins, so the fallback is the last route the transport mounts and
#: every local service has registered by the time the routes are collected.
PRIORITY = 10_000
#: Where the panel section's script is served from: this distribution's own
#: route, since the panel's static directory belongs to keepup-http.
SECTION_SCRIPT = "/keepup-gateway/gateway.js"
SCRIPT_FILE = Path(__file__).resolve().parent / "static" / "gateway.js"
#: The section, declared as data. The catalogue imports it without a grant: the
#: application gives it to ADMIN in its own file, or an administrator in the panel.
SECTION = {
    "id": "gateway",
    "name": "Gateway",
    "description": "Routes of the gateway, their addresses and the applications behind them",
    "js": SECTION_SCRIPT,
    "initFunction": "initGatewayModule",
    "icon": "share-2",
    "adminOnly": True,
    "version": "1.0.0",
}
#: The largest registration body: a name, a prefix, an address and a few keys.
REGISTRATION_MAX_BYTES = 16 * 1024
#: A whole value of the form ${NAME} is read from the environment.
_ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class GatewayService:
    """The table from all three sources, and what this replica knows of its addresses.

    Args:
        routes: the config's routes.
        balancer: the turn and the cooldowns.
    """

    def __init__(self, routes, balancer: Balancer):
        self.routes = list(routes)
        self.local: List[GatewayRoute] = []
        self.balancer = balancer
        self.registry: Optional[Registry] = None

    def register_local(self, name: str, prefix: str, handler: Callable[..., Any], *,
                       methods=None, auth: str = "none", permission: Optional[str] = None,
                       strip_prefix: bool = False,
                       max_body_bytes: int = DEFAULT_MAX_BODY_BYTES) -> GatewayRoute:
        """Serve a prefix by calling a handler of this runtime, without a proxy.

        A plugin calls this from its ``initialize``; the handler receives a
        :class:`keepup_gateway.local.LocalCall`.

        Raises:
            TableError: for a route that cannot be served, or a prefix that
                another route already holds.
        """
        if not callable(handler):
            raise TableError(f"gateway route 'local:{name}': the handler is not callable")
        entry = {"id": f"local:{name}", "prefix": prefix, "upstreams": ["http://in-process"],
                 "auth": auth, "permission": permission, "strip_prefix": strip_prefix,
                 "max_body_bytes": max_body_bytes}
        if methods is not None:
            entry["methods"] = methods
        route = parse_route(entry)
        owner = self.reserved(route.prefix)
        if owner is not None:
            raise TableError(f"gateway route 'local:{name}': the prefix {route.prefix} is "
                             f"taken by {owner}")
        route = replace(route, upstreams=(), source=SOURCE_LOCAL, handler=handler)
        self.local.append(route)
        return route

    def reserved(self, prefix: str) -> Optional[str]:
        """Who already holds a prefix in the config or in process, or None."""
        for route in self.routes + self.local:
            if route.prefix == prefix:
                return f"route '{route.id}'"
        return None

    async def table(self) -> List[GatewayRoute]:
        """Every route the gateway answers right now."""
        registered = await self.registry.routes() if self.registry is not None else []
        return self.routes + self.local + list(registered)

    async def report(self) -> Dict[str, Any]:
        """Every route and the state of each of its addresses, in this replica."""
        return {"routes": [
            {**route.describe(),
             "upstreams": [self.balancer.state(route.id, address)
                           for address in route.upstreams]}
            for route in sorted(await self.table(), key=lambda item: item.prefix)
        ]}


class GatewayPlugin(BasePlugin):
    """The capability: a route table, a proxy, a balancer, a registry and the edge."""

    descriptor = PluginDescriptor(
        id="gateway",
        name="Gateway",
        version="0.5.0",
        distribution="keepup-gateway",
        kind=KIND_OPTIONAL,
        priority=PRIORITY,
        provides=("gateway>=1",),
        wants=("auth>=1", "permissions>=1", "datasource>=1"),
        contributions=("routes", "sockets", "sections", "tables"),
    )

    def __init__(self, config=None, transport=None, socket_connect=None):
        super().__init__("gateway", "Gateway", config)
        self._transport = transport
        self._socket_connect = socket_connect
        self.sockets: Optional[SocketProxy] = None
        self.service: Optional[GatewayService] = None
        self.proxy: Optional[Proxy] = None
        self.prober: Optional[Prober] = None
        self.announcer: Optional[Announcer] = None
        self.identity_secret = ""
        self.registry_enabled = False
        self._announce: Optional[Dict[str, Any]] = None
        self._error: Optional[str] = None
        try:
            self._build()
        except TableError as error:
            self._error = str(error)

    def _build(self) -> None:
        """The table, the balancer, the proxy and the announcement, from the config."""
        routes = parse_table(self.config.get("routes"))
        secret = _resolved(self.config.get("identity_secret"))
        registry = self.config.get("registry") or {}
        if not isinstance(registry, dict):
            raise TableError("gateway: registry must be an object")
        self.registry_enabled = bool(registry.get("enabled", False))
        self._announce = parse_announce(self.config.get("announce"))
        for needs, what in ((any(route.signed_in for route in routes), "a route asks for sign-in"),
                            (any(route.application for route in routes),
                             "a route stands for an application"),
                            (self.registry_enabled, "the registry is enabled"),
                            (self._announce is not None, "the application announces itself")):
            if needs and not secret:
                raise TableError(f"gateway: {what}, and identity_secret is not set")
        self.identity_secret = secret or ""
        balancer = Balancer()
        self.service = GatewayService(routes, balancer)
        self.proxy = Proxy(self.service.table, balancer, identity_secret=self.identity_secret,
                           transport=self._transport)
        self.prober = Prober(self.proxy.client, self.identity_secret)
        options = {"connect": self._socket_connect} if self._socket_connect else {}
        self.sockets = SocketProxy(self.service.table, balancer, self.identity_secret,
                                   **options)

    def register(self, services):
        """Publish the gateway when it could be built, so plugins register local services.

        Args:
            services: the runtime's registry.
        """
        if self.service is not None and self._error is None:
            services.provide(SERVICE_GATEWAY, self.service, version=1, plugin_id="gateway")

    async def initialize(self) -> bool:
        """Refuse a table that cannot be served; open the registry; start announcing."""
        if self._error is not None:
            raise TableError(self._error)
        if self.registry_enabled:
            services = getattr(self, "services", None)
            if services is None or not services.has(SERVICE_DATASOURCE):
                raise TableError("gateway: the registry keeps its records in the database, "
                                 "and this deployment has no datasource")
            self.service.registry = Registry(services.require(SERVICE_DATASOURCE),
                                             self.service.reserved)
        if self._announce is not None:
            self.announcer = Announcer(self._announce, self.identity_secret,
                                       transport=self._transport)
            self.announcer.start()
        logger.info("Gateway: %d routes from the config, %d local, registry %s",
                    len(self.service.routes), len(self.service.local),
                    "on" if self.registry_enabled else "off")
        return True

    async def cleanup(self):
        """Take this application's address back and close the connections."""
        if self.announcer is not None:
            await self.announcer.stop()
        if self.proxy is not None:
            await self.proxy.aclose()

    def get_declared_tables(self) -> List[Any]:
        """The registry's table, when the registry is on."""
        return [declared_table()] if self.registry_enabled and self._error is None else []

    def get_panel_sections(self) -> List[Dict[str, Any]]:
        """The section that shows the routes and the applications behind them."""
        return [dict(SECTION)]

    def get_api_routes(self) -> List[Dict[str, Any]]:
        """The reports, the script, the card, the registry, the config's routes and,
        last, the fallback."""
        declared: List[Dict[str, Any]] = [
            {"path": f"{ADMIN_PREFIX}/routes", "methods": ["GET"],
             "handler": self.routes_report, "permission": ADMIN_PERMISSION},
            {"path": f"{ADMIN_PREFIX}/applications", "methods": ["GET"],
             "handler": self.applications_report, "permission": ADMIN_PERMISSION},
            # The script of a section is as public as the panel's own static
            # files: what it shows comes from the two routes above.
            {"path": SECTION_SCRIPT, "methods": ["GET"], "handler": self.section_script,
             "require_auth": False, "audit": False, "include_in_schema": False,
             "response_media_type": "application/javascript"},
        ]
        if self._error is not None or self.service is None:
            return declared
        if self.identity_secret:
            declared.append({"path": SELF_PATH, "methods": ["GET"], "handler": self.self_card,
                             "raw_request": True, "include_in_schema": False})
        if self.registry_enabled:
            declared.append({"path": REGISTER_PATH, "methods": ["POST", "DELETE"],
                             "handler": self.registration, "raw_request": True,
                             "include_in_schema": False,
                             "max_body_bytes": REGISTRATION_MAX_BYTES})
        for route in self.service.routes:
            for path in (route.prefix, route.prefix + "/{path:path}"):
                declared.append(self._passing(path, route.max_body_bytes))
        if self.registry_enabled or self.service.local:
            largest = max([DEFAULT_MAX_BODY_BYTES]
                          + [route.max_body_bytes for route in self.service.local])
            declared.append(self._passing(FALLBACK_PATH, largest))
        return declared

    def get_websocket_routes(self) -> List[Dict[str, Any]]:
        """Sockets of the config's routes that carry them, and the fallback when the
        registry may hold a service that does."""
        if self._error is not None or self.service is None:
            return []
        declared = [{"path": path, "handler": self.sockets.handle}
                    for route in self.service.routes if route.websocket
                    for path in (route.prefix, route.prefix + "/{path:path}")]
        if self.registry_enabled:
            declared.append({"path": FALLBACK_SOCKET, "handler": self.sockets.handle})
        return declared

    def _passing(self, path: str, limit: int) -> Dict[str, Any]:
        """A declaration the gateway answers by passing the request on."""
        return {"path": path, "methods": list(ALL_METHODS), "handler": self.handle,
                "raw_request": True, "include_in_schema": False,
                # The gateway reads the body itself, up to the route's own limit.
                "max_body_bytes": limit}

    def get_handlers(self) -> Dict[str, Any]:
        """What another plugin may reach instead of importing this one."""
        return {"service": self.service}

    async def handle(self, request):
        """Pass one request on."""
        return await self.proxy.handle(request)

    async def routes_report(self, current_user=None):
        """The table and the state of every address."""
        return await self.service.report() if self.service is not None else {"routes": []}

    async def applications_report(self, current_user=None):
        """A card for every address of every application route, asked as the
        administrator who opened the section."""
        if self.service is None:
            return {"applications": []}
        caller = caller_of(current_user or {})
        return {"applications": await self.prober.cards(await self.service.table(),
                                                        self.service.balancer, caller)}

    async def section_script(self):
        """The panel section's script, from this distribution."""
        return SCRIPT_FILE.read_text(encoding="utf-8")

    async def self_card(self, request):
        """This application's own card, for a gateway that signed an administrator in."""
        from fastapi.responses import JSONResponse

        try:
            caller = verify_identity(request.headers, self.identity_secret)
        except IdentityError as error:
            return JSONResponse({"detail": str(error)}, status_code=401)
        if ADMIN_ROLE not in caller.roles:
            return JSONResponse({"detail": "an administrator's identity is required"},
                                status_code=403)
        return JSONResponse(describe_self(self.plugin_manager))

    async def registration(self, request):
        """A service registers an address (POST) or takes it back (DELETE)."""
        import json

        from fastapi.responses import JSONResponse

        action = ACTION_REGISTER if request.method.upper() == "POST" else ACTION_DEREGISTER
        body = await request.body()
        try:
            if len(body) > REGISTRATION_MAX_BYTES:
                raise RegistrationError("the registration is too large", 413)
            verify_registration(self.identity_secret, action, request.headers, body)
            try:
                record = json.loads(body or b"{}")
            except ValueError:
                raise RegistrationError("the registration is not JSON")
            if not isinstance(record, dict):
                raise RegistrationError("the registration must be an object")
            if action == ACTION_DEREGISTER:
                removed = await self.service.registry.deregister(record)
                return JSONResponse({"removed": removed})
            for prefix in prefixes_of(record):
                if _answered_here(request, prefix):
                    raise RegistrationError(
                        f"the application itself answers {prefix}: a service registered "
                        "there would never be reached", 409)
            accepted = await self.service.registry.register(record, get_instance_id())
            return JSONResponse(accepted)
        except RegistrationError as error:
            return JSONResponse({"detail": str(error)}, status_code=error.status)


def _answered_here(request, prefix: str) -> bool:
    """Whether a route of the application, not the gateway's, answers a prefix."""
    from starlette.routing import Match

    app = request.scope.get("app")
    scope = {"type": "http", "path": prefix.rstrip("/") or "/", "method": "GET",
             "root_path": "", "path_params": {}}
    for candidate in getattr(getattr(app, "router", None), "routes", []) or []:
        if getattr(candidate, "path", None) == FALLBACK_PATH:
            continue
        found, _ = candidate.matches(scope)
        if found != Match.NONE:
            return True
    return False


def _resolved(value: Any) -> Optional[str]:
    """A setting's value, with a whole ``${NAME}`` read from the environment."""
    if value is None:
        return None
    text = str(value)
    found = _ENV_REFERENCE.match(text)
    if found:
        return os.environ.get(found.group(1)) or None
    return text or None
