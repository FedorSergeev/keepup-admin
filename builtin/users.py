"""Users as a capability: accounts, the roles they hold and the rights granted.

Task keepup-105, first half. Accounts and their roles are what everything else
points at -- a session belongs to a user, an audit row is attributed to one, a
section is shown by role -- and the three tables that hold them (`users`,
`user_roles`, `user_permissions`) are declared in the kernel's schema, which
means the kernel cannot be assembled without them.

The plugin claims them, declares them as its own and publishes the `users`
service, so a capability asks who holds a role instead of importing the module
that reads the table. The declaration moves into this capability's distribution
in keepup-124; what is here is the claim and the service.
"""

import logging
from typing import Any, Dict, List, Mapping, Optional

from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.plugins.base import BasePlugin
from keepup_users.tables import USERS, USER_PERMISSIONS, USER_ROLES

logger = logging.getLogger(__name__)

__all__ = ["UsersPlugin", "UsersService", "SERVICE_USERS"]

#: What another plugin asks for to know who somebody is and what they hold.
SERVICE_USERS = "users"


class UsersService:
    """Who holds which role, asked by name.

    It is a thin reading of the module that owns the queries today
    (``keepup.auth.user_roles``); when the declaration travels, so do they, and
    a caller never learns which of the two answered.
    """

    def __init__(self, datasource: Any):
        self.datasource = datasource

    def roles_of(self, user_id: int, mirror: Optional[str] = None) -> List[str]:
        """The roles this account holds, as a set.

        Args:
            user_id: the account.
            mirror: the deprecated single-role mirror to fall back on.

        Returns:
            The roles, never empty: an account without one is refused on write.
        """
        from keepup.auth.user_roles import roles_of

        return roles_of(user_id, mirror)

    def has_role(self, user: Optional[Mapping[str, Any]], role: str) -> bool:
        """Whether this person holds this role.

        Args:
            user: the account as a route received it.
            role: the role asked about.

        Returns:
            True when it is held.
        """
        from keepup.auth.user_roles import has_role

        return has_role(user, role)

    async def count(self) -> int:
        """How many accounts there are."""
        rows = await self.datasource.execute("SELECT COUNT(*) AS count FROM users")
        if not rows:
            return 0
        return int(next(iter(dict(rows[0]).values()), 0) or 0)


class UsersPlugin(BasePlugin):
    """The capability: accounts, roles and rights."""

    descriptor = PluginDescriptor(
        id="users",
        name="Users and roles",
        version="0.4.0",
        distribution="keepup-users",
        kind=KIND_OPTIONAL,
        priority=35,
        provides=("users>=1",),
        requires=("datasource>=1",),
        wants=("auth>=1",),
        contributions=("routes", "sections", "tables"),
    )

    def __init__(self, config=None):
        super().__init__("users", "Users and roles", config)

    def register(self, services):
        """Publish the service where there is storage to read accounts from.

        Args:
            services: the runtime's registry.
        """
        if services.has(SERVICE_DATASOURCE):
            self.service = UsersService(services.require(SERVICE_DATASOURCE))
            services.provide(SERVICE_USERS, self.service, version=1, plugin_id="users")

    async def initialize(self):
        """Keep the service published at registration; build one if there is none."""
        if getattr(self, "service", None) is None:
            self.service = UsersService(self.services.require(SERVICE_DATASOURCE))
        return True

    def get_declared_tables(self) -> List[Any]:
        """The three tables this capability owns, declared once, until keepup-124."""
        return [USERS, USER_ROLES, USER_PERMISSIONS]

    def get_panel_sections(self) -> List[Dict[str, Any]]:
        """The users section, as data rather than as a catalogue entry."""
        return [{
            "id": "users",
            "name": "Users",
            "description": "Accounts, the roles they hold and the rights granted",
            "js": "/keepup-static/modules/js/users.js",
            "css": "/keepup-static/modules/css/users.css",
            "initFunction": "initUsers",
            "icon": "people",
            "version": "1.0.0",
        }]

    def get_api_routes(self) -> List[Any]:
        """No routes yet: the accounts section's own move here in keepup-124."""
        return []

    def get_handlers(self) -> Dict[str, Any]:
        """What another plugin may reach instead of importing the accounts."""
        return {"service": self.service, "roles_of": self.service.roles_of,
                "has_role": self.service.has_role}
