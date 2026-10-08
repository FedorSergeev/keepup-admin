"""The data source as a plugin: the abstraction that holds the database library.

Task keepup-124, first slice. `keepup/kernel/datasource.py` owns the shapes and
the two names (keepup-106), the drivers answer with `spec()` (keepup-107,
keepup-108) -- and nobody published `datasource`, so no capability could reach
storage through it. This is the plugin that does: it requires a driver, publishes
the service built on the framework's current manager (`keepup.db`), and creates
nothing itself except what capabilities declare.

It answers the abstraction with the manager, so the dialect, the pool and the
creation of declared tables stay where they are today and move into
`packages/keepup-db` in the rest of keepup-124. What is fixed here is the part
that cannot be deferred: `keepup-admin` plus `keepup-db` plus one driver is a
deployment that talks to a database, and every capability asks it by name.
"""

import logging
from typing import Any, Dict, List, Mapping, Optional

from keepup.db import DatabaseManagerV2, db_config
from keepup.kernel.datasource import (
    SERVICE_DATASOURCE,
    SERVICE_DATASOURCE_DRIVER,
    DriverSpec,
)
from keepup.kernel.descriptor import KIND_REQUIRED, PluginDescriptor
from keepup.plugins.base import BasePlugin

logger = logging.getLogger(__name__)

__all__ = ["DbPlugin", "DatabaseSource", "SERVICE_DATASOURCE"]


class DatabaseSource:
    """The framework's manager, wearing the abstraction's shape.

    Everything a capability may ask for is here and nothing else: run a
    statement, run one that changes something, create what was declared, close.
    Which database this is comes from the driver, not from here.
    """

    def __init__(self, manager: Any, spec: Optional[DriverSpec] = None):
        self.manager = manager
        self.spec = spec

    @property
    def dialect(self) -> str:
        """The dialect the driver named, or the configured one."""
        if self.spec is not None and self.spec.dialect:
            return self.spec.dialect
        return "postgresql" if db_config.is_postgres() else "sqlite"

    async def execute(self, statement: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        """Run a statement and answer its rows."""
        return await self.manager.execute_async(statement, dict(params or {}))

    async def execute_one(self, statement: str,
                          params: Optional[Mapping[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Run a statement and answer its first row, or None."""
        return await self.manager.execute_one_async(statement, dict(params or {}))

    async def execute_commit(self, statement: str,
                             params: Optional[Mapping[str, Any]] = None) -> Any:
        """Run a statement that changes something, and commit it."""
        return await self.manager.execute_commit_async(statement, dict(params or {}))

    async def execute_many(self, statement: str, params: List[Mapping[str, Any]]) -> Any:
        """Run one statement for every set of parameters."""
        return await self.manager.execute_many_async(statement, [dict(item) for item in params])

    def ensure_tables(self, *tables: Any) -> None:
        """Create what the declaration says, and add the columns it gained.

        The declarations come from the capabilities that own them, which is why
        this takes them as arguments rather than knowing any itself.
        """
        from keepup import tables as table_language

        flat = []
        for table in tables:
            flat.extend(table if isinstance(table, (list, tuple)) else [table])
        created = [table for table in flat if table is not None]
        if not created:
            return
        table_language.ensure_tables(*created)

    def ensure_columns(self, table: Any) -> None:
        """Add the columns an existing table lacks."""
        from keepup import tables as table_language

        table_language.ensure_columns(table)

    def get_session(self) -> Any:
        """A session, for a caller that needs a transaction."""
        return self.manager.get_session()

    def raw_connection(self) -> Any:
        """A driver cursor, for code that has to go below the abstraction."""
        return self.manager.raw_connection()

    def pool_status(self) -> Any:
        """What the pool is doing, for the metrics capability."""
        reader = getattr(self.manager, "get_pool_status", None)
        return reader() if callable(reader) else {}

    def dispose(self) -> None:
        """Close what this opened."""
        self.manager.dispose()


class DbPlugin(BasePlugin):
    """The capability every other one needs: storage, by name."""

    descriptor = PluginDescriptor(
        id="db",
        name="Database abstraction",
        version="0.4.0",
        distribution="keepup-db",
        kind=KIND_REQUIRED,
        priority=10,
        provides=("datasource>=1",),
        requires=("datasource_driver>=1",),
        contributions=("tables",),
    )

    def __init__(self, config=None):
        super().__init__("db", "Database abstraction", config)

    def register(self, services):
        """Publish the data source the driver describes.

        Args:
            services: the runtime's registry.
        """
        spec = None
        if services.has(SERVICE_DATASOURCE_DRIVER):
            driver = services.require(SERVICE_DATASOURCE_DRIVER)
            spec = driver.spec() if callable(getattr(driver, "spec", None)) else None
        self.source = DatabaseSource(DatabaseManagerV2, spec)
        services.provide(SERVICE_DATASOURCE, self.source, version=1, plugin_id="db")
        logger.info("The data source is %s", self.source.dialect)

    async def initialize(self):
        """Open nothing yet: the manager opens its pool on the first query."""
        return True

    def get_declared_tables(self) -> List[Any]:
        """Nothing: the abstraction owns no table of the framework's.

        Every table belongs to the capability that keeps it there, which is what
        makes `keepup-db` an abstraction rather than a schema (keepup-106).
        """
        return []

    def get_api_routes(self) -> List[Any]:
        """No routes: storage is reached by capabilities, not by people."""
        return []

    def get_handlers(self) -> Dict[str, Any]:
        """What another plugin may reach instead of importing the manager."""
        return {"service": self.source}
