"""Sign-in as a capability: who is calling, and what they may do.

Task keepup-116, first half. The kernel owns the shapes and the two names
(``keepup/kernel/security.py``, keepup-119); this plugin is a deployment putting
an answer behind them, and the tables that answer needs -- the panel's sessions
and the login attempts that throttle a guesser -- belong to it.

The sign-in machinery itself (``auth/routes.py``, ``panel_session.py``,
``login_throttle.py``, the providers, OIDC and somebody else's identity system)
travels into this capability's distribution in keepup-124, together with the
routes, the sockets and the CSRF middleware it contributes. What is here is the
claim: two services, two tables, and an identity the runtime adopts.
"""

import logging
from typing import Any, Dict, List

from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.kernel.security import SERVICE_AUTH, SERVICE_PERMISSIONS, Identity
from keepup.plugins.base import BasePlugin

logger = logging.getLogger(__name__)

__all__ = ["AuthPlugin", "build_identity"]


def build_identity(name: str = "keepup-auth") -> Identity:
    """The framework's own sign-in, behind the shape the kernel owns.

    Args:
        name: what to call it in a log line.

    Returns:
        The identity: how a caller is found, and what decides a right.
    """
    from keepup.auth.dependencies import get_panel_user
    from keepup.auth.identity import access

    return Identity(subject_dependency=get_panel_user, checker=access.check, name=name)


class AuthPlugin(BasePlugin):
    """The capability: sessions, attempts, and the two services that answer."""

    descriptor = PluginDescriptor(
        id="auth",
        name="Sign-in and sessions",
        version="0.4.0",
        distribution="keepup-auth",
        kind=KIND_OPTIONAL,
        priority=30,
        provides=("auth>=1", "permissions>=1"),
        requires=("datasource>=1",),
        contributions=("routes", "sockets", "middleware", "tables", "permissions"),
    )

    def __init__(self, config=None):
        super().__init__("auth", "Sign-in and sessions", config)

    def register(self, services):
        """Publish the identity under both names, once there is storage.

        Args:
            services: the runtime's registry.
        """
        if services.has(SERVICE_DATASOURCE):
            self.identity = build_identity()
            services.provide(SERVICE_AUTH, self.identity, version=1, plugin_id="auth")
            services.provide(SERVICE_PERMISSIONS, self.identity, version=1, plugin_id="auth")

    async def initialize(self):
        """Keep the identity published at registration; build one if there is none."""
        if getattr(self, "identity", None) is None:
            self.identity = build_identity()
        return True

    def get_declared_tables(self) -> List[Any]:
        """The two tables this capability owns, declared once, until keepup-124."""
        from keepup.auth.login_throttle import LOGIN_ATTEMPTS
        from keepup.auth.panel_session import AUTH_SESSION

        return [AUTH_SESSION, LOGIN_ATTEMPTS]

    def get_route_permissions(self) -> Dict[str, Any]:
        """The rights this capability's routes ask for, by name.

        Empty here, and declared all the same: the kind of contribution is what
        another plugin reads to know that a right it asks for exists.
        """
        return {}

    def get_api_routes(self) -> List[Any]:
        """No routes yet: the sign-in's own (sign in, out, refresh, WebSocket, CSRF)
        travel into this capability's distribution in keepup-124."""
        return []

    def get_handlers(self) -> Dict[str, Any]:
        """What another plugin may reach instead of importing the sign-in."""
        return {"identity": self.identity}
