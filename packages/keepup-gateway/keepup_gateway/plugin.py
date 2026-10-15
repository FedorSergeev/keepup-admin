"""The gateway as a capability: one way in for the application's services.

The table is the plugin's `config`. Each route is mounted as two declarations of
the HTTP transport's ``raw_request`` kind -- the prefix itself and everything
below it -- rather than as one catch-all: a catch-all mounted before the
framework's own routes would cover them, and one mounted after would never be
reached. The handler then chooses the longest matching prefix again from the full
path, so the answer does not depend on the order the transport mounted them in.

A table the gateway cannot serve leaves the plugin failed, with the reason in the
plugins' report, and mounts none of its routes: a half-served table would answer
some prefixes and drop others without saying so.

The same plugin is what an application *behind* a gateway installs. With an
`identity_secret` and no routes of its own it answers `GET /api/gateway/self` --
its runtime's report -- to the gateway's signed identity of an administrator, and
that is what the gateway's panel section shows as the application's card
(`applications.py`).
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.plugins.base import BasePlugin
from keepup_gateway.applications import ADMIN_ROLE, SELF_PATH, Prober, describe_self
from keepup_gateway.balancer import Balancer
from keepup_gateway.identity import IdentityError, caller_of, verify_identity
from keepup_gateway.proxy import Proxy
from keepup_gateway.table import ADMIN_PREFIX, ALL_METHODS, TableError, parse_table

logger = logging.getLogger(__name__)

__all__ = ["GatewayPlugin", "GatewayService", "SERVICE_GATEWAY", "ADMIN_PERMISSION"]

#: What another plugin asks for to read the table or its state.
SERVICE_GATEWAY = "gateway"
#: The right the administrator's report asks for.
ADMIN_PERMISSION = "gateway.admin"
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
#: A whole value of the form ${NAME} is read from the environment.
_ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class GatewayService:
    """The table and what this replica knows about its addresses."""

    def __init__(self, routes, balancer: Balancer):
        self.routes = list(routes)
        self.balancer = balancer

    def report(self) -> Dict[str, Any]:
        """Every route and the state of each of its addresses, in this replica."""
        return {"routes": [
            {**route.describe(),
             "upstreams": [self.balancer.state(route.id, address)
                           for address in route.upstreams]}
            for route in sorted(self.routes, key=lambda item: item.prefix)
        ]}


class GatewayPlugin(BasePlugin):
    """The capability: a route table, a proxy, a balancer and the edge's sign-in."""

    descriptor = PluginDescriptor(
        id="gateway",
        name="Gateway",
        version="0.5.0",
        distribution="keepup-gateway",
        kind=KIND_OPTIONAL,
        priority=80,
        provides=("gateway>=1",),
        wants=("auth>=1", "permissions>=1"),
        contributions=("routes", "sections"),
    )

    def __init__(self, config=None, transport=None):
        super().__init__("gateway", "Gateway", config)
        self._transport = transport
        self.service: Optional[GatewayService] = None
        self.proxy: Optional[Proxy] = None
        self._error: Optional[str] = None
        self.identity_secret = ""
        self.prober: Optional[Prober] = None
        try:
            self._build()
        except TableError as error:
            self._error = str(error)

    def _build(self) -> None:
        """The table, the balancer and the proxy, from the config."""
        routes = parse_table(self.config.get("routes"))
        secret = _resolved(self.config.get("identity_secret"))
        if any(route.signed_in for route in routes) and not secret:
            raise TableError("gateway: a route asks for sign-in, and identity_secret is not "
                             "set -- the services behind it could not check who is calling")
        if any(route.application for route in routes) and not secret:
            raise TableError("gateway: a route stands for an application, and identity_secret "
                             "is not set -- the application would not tell its state to anybody")
        self.identity_secret = secret or ""
        balancer = Balancer()
        self.service = GatewayService(routes, balancer)
        self.proxy = Proxy(routes, balancer, identity_secret=self.identity_secret,
                           transport=self._transport)
        self.prober = Prober(self.proxy.client, self.identity_secret)

    def register(self, services):
        """Publish the table when it could be built.

        Args:
            services: the runtime's registry.
        """
        if self.service is not None and self._error is None:
            services.provide(SERVICE_GATEWAY, self.service, version=1, plugin_id="gateway")

    async def initialize(self) -> bool:
        """Refuse to run a table that cannot be served, naming the route."""
        if self._error is not None:
            raise TableError(self._error)
        logger.info("Gateway: %d routes", len(self.service.routes))
        return True

    async def cleanup(self):
        """Close the connections to the services."""
        if self.proxy is not None:
            await self.proxy.aclose()

    def get_panel_sections(self) -> List[Dict[str, Any]]:
        """The section that shows the routes and the applications behind them."""
        return [dict(SECTION)]

    def get_api_routes(self) -> List[Dict[str, Any]]:
        """The reports, the section's script, this application's own card, and
        two declarations per route."""
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
        for route in self.service.routes:
            for path in (route.prefix, route.prefix + "/{path:path}"):
                declared.append({
                    "path": path,
                    "methods": list(ALL_METHODS),
                    "handler": self.handle,
                    "raw_request": True,
                    "include_in_schema": False,
                    # The gateway reads the body itself, up to the route's own
                    # limit; the application's general limit does not apply.
                    "max_body_bytes": route.max_body_bytes,
                })
        return declared

    def get_handlers(self) -> Dict[str, Any]:
        """What another plugin may reach instead of importing this one."""
        return {"service": self.service}

    async def handle(self, request):
        """Pass one request on."""
        return await self.proxy.handle(request)

    async def routes_report(self, current_user=None):
        """The table and the state of every address."""
        return self.service.report() if self.service is not None else {"routes": []}

    async def applications_report(self, current_user=None):
        """A card for every address of every application route, asked as the
        administrator who opened the section."""
        if self.service is None:
            return {"applications": []}
        caller = caller_of(current_user or {})
        return {"applications": await self.prober.cards(self.service.routes,
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


def _resolved(value: Any) -> Optional[str]:
    """A setting's value, with a whole ``${NAME}`` read from the environment."""
    if value is None:
        return None
    text = str(value)
    found = _ENV_REFERENCE.match(text)
    if found:
        return os.environ.get(found.group(1)) or None
    return text or None
