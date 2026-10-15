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
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional

from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.plugins.base import BasePlugin
from keepup_gateway.balancer import Balancer
from keepup_gateway.proxy import Proxy
from keepup_gateway.table import ADMIN_PREFIX, ALL_METHODS, TableError, parse_table

logger = logging.getLogger(__name__)

__all__ = ["GatewayPlugin", "GatewayService", "SERVICE_GATEWAY", "ADMIN_PERMISSION"]

#: What another plugin asks for to read the table or its state.
SERVICE_GATEWAY = "gateway"
#: The right the administrator's report asks for.
ADMIN_PERMISSION = "gateway.admin"
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
        contributions=("routes",),
    )

    def __init__(self, config=None, transport=None):
        super().__init__("gateway", "Gateway", config)
        self._transport = transport
        self.service: Optional[GatewayService] = None
        self.proxy: Optional[Proxy] = None
        self._error: Optional[str] = None
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
        balancer = Balancer()
        self.service = GatewayService(routes, balancer)
        self.proxy = Proxy(routes, balancer, identity_secret=secret or "",
                           transport=self._transport)

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

    def get_api_routes(self) -> List[Dict[str, Any]]:
        """Two declarations per route, and the administrator's report."""
        declared: List[Dict[str, Any]] = [{
            "path": f"{ADMIN_PREFIX}/routes", "methods": ["GET"],
            "handler": self.routes_report, "permission": ADMIN_PERMISSION,
        }]
        if self._error is not None or self.service is None:
            return declared
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


def _resolved(value: Any) -> Optional[str]:
    """A setting's value, with a whole ``${NAME}`` read from the environment."""
    if value is None:
        return None
    text = str(value)
    found = _ENV_REFERENCE.match(text)
    if found:
        return os.environ.get(found.group(1)) or None
    return text or None
