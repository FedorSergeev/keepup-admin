"""SQLite as this deployment knows it.

The second driver of the same abstraction, and the reason the abstraction exists:
a capability asks for `datasource` and never learns which of the two answered.
SQLite cannot answer ``INSERT ... RETURNING`` in the shapes the framework uses,
and it cannot carry a message between replicas -- both are said here, in the one
place that knows, rather than discovered by a capability at run time.

The knowledge is the framework's current configuration (``keepup.db.db_config``)
until the driver travels in a distribution of its own (keepup-124).
"""

from keepup.db import db_config
from keepup.kernel.datasource import SERVICE_DATASOURCE_DRIVER, DriverSpec
from keepup.kernel.descriptor import KIND_REQUIRED, PluginDescriptor
from keepup.plugins.base import BasePlugin


class SqlitePlugin(BasePlugin):
    """The SQLite driver: a dialect, a connection and what it cannot do."""

    descriptor = PluginDescriptor(
        id="sqlite",
        name="SQLite driver",
        version="0.4.0",
        distribution="keepup-sqlite",
        kind=KIND_REQUIRED,
        priority=7,
        provides=("datasource_driver>=1",),
        default_enabled=False,
    )

    def __init__(self, config=None):
        super().__init__("sqlite", "SQLite driver", config)

    def register(self, services):
        """Publish the driver under the name only the abstraction asks for.

        Args:
            services: the runtime's registry.
        """
        services.provide(SERVICE_DATASOURCE_DRIVER, self, version=1, plugin_id="sqlite")

    async def initialize(self):
        """Nothing to open: the abstraction owns the pool (keepup-106)."""
        return True

    def spec(self) -> DriverSpec:
        """What the abstraction needs in order to open this database.

        Returns:
            The dialect, the connection, the engine's parameters -- and the two
            honest answers: no ``RETURNING``, and no messages between replicas,
            so a cluster on SQLite is reported rather than silently deaf.
        """
        return DriverSpec(
            dialect="sqlite",
            connection_url=db_config.get_connection_string(),
            engine_params=dict(db_config.get_sqlalchemy_engine_params()),
            supports_returning=False,
            carries_messages=False,
        )

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}
