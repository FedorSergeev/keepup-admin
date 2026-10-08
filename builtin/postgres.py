"""PostgreSQL as this deployment knows it.

The driver answers the abstraction one question -- what database is this -- and
creates nothing itself: the pool, the dialect registry and the creation of
declared tables are the abstraction's business
(``doc/capabilities-out-of-the-kernel.md``, card 3).

The knowledge is the framework's current configuration (``keepup.db.db_config``)
until the driver travels in a distribution of its own and keeps its own
(keepup-124).
"""

from keepup.db import db_config
from keepup.kernel.datasource import SERVICE_DATASOURCE_DRIVER, DriverSpec
from keepup.kernel.descriptor import KIND_REQUIRED, PluginDescriptor
from keepup.plugins.base import BasePlugin


class PostgresPlugin(BasePlugin):
    """The PostgreSQL driver: a dialect, a connection and what it can do."""

    descriptor = PluginDescriptor(
        id="postgres",
        name="PostgreSQL driver",
        version="0.4.0",
        distribution="keepup-postgres",
        kind=KIND_REQUIRED,
        priority=6,
        provides=("datasource_driver>=1",),
        # Chosen by the deployment's configuration, not by being installed: two
        # drivers of one required service cannot both answer (keepup-107).
        default_enabled=False,
    )

    def __init__(self, config=None):
        super().__init__("postgres", "PostgreSQL driver", config)

    def register(self, services):
        """Publish the driver under the name only the abstraction asks for.

        Args:
            services: the runtime's registry.
        """
        services.provide(SERVICE_DATASOURCE_DRIVER, self, version=1, plugin_id="postgres")

    async def initialize(self):
        """Nothing to open: the abstraction owns the pool (keepup-106)."""
        return True

    def spec(self) -> DriverSpec:
        """What the abstraction needs in order to open this database.

        Returns:
            The dialect, the connection, the engine's parameters, and the two
            things a capability may ask about: whether ``RETURNING`` answers the
            new row, and whether the database carries a message between replicas.
        """
        return DriverSpec(
            dialect="postgresql",
            connection_url=db_config.get_connection_string(),
            engine_params=dict(db_config.get_sqlalchemy_engine_params()),
            supports_returning=True,
            carries_messages=True,
        )

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}
