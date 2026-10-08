"""The shapes of the data source: what the kernel means by `datasource`.

The kernel stores nothing itself and knows no database. What it owns is the two
names a deployment's storage is reached by -- ``datasource``, the thing every
capability queries, and ``datasource_driver``, the thing only the abstraction
queries -- and the shape of each (`doc/service-catalogue.md`). A capability
declares its tables and asks the first; a driver translates the second into one
database's dialect.

Nothing here imports a database library, and that is the point: the abstraction
that holds SQLAlchemy is a plugin, and the kernel can require it without knowing
it (`doc/plugin_constructor.md` section 4.9).
"""

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

__all__ = [
    "SERVICE_DATASOURCE",
    "SERVICE_DATASOURCE_DRIVER",
    "DataSource",
    "DriverSpec",
    "DatasourceDriver",
]

#: The service every capability queries.
SERVICE_DATASOURCE = "datasource"
#: The service only the abstraction queries: one database, one dialect.
SERVICE_DATASOURCE_DRIVER = "datasource_driver"


@runtime_checkable
class DataSource(Protocol):
    """What a capability may ask of storage, whatever is behind it.

    A capability declares tables, creates them once through :meth:`ensure_tables`
    and then queries by name. It never learns a dialect, a driver or a
    connection string; it learns whether an answer came back.
    """

    @property
    def dialect(self) -> str:
        """The name of the dialect behind the service, for the report."""
        ...

    async def execute(self, statement: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        """Run a statement and return its rows."""
        ...

    async def execute_commit(self, statement: str,
                             params: Optional[Mapping[str, Any]] = None) -> Any:
        """Run a statement that changes something, and commit it."""
        ...

    def ensure_tables(self, *tables: Any) -> None:
        """Create what the declaration says and add the columns it gained."""
        ...

    def dispose(self) -> None:
        """Close what this service opened."""
        ...


@dataclass(frozen=True)
class DriverSpec:
    """One database, described in the terms the abstraction needs.

    Attributes:
        dialect: ``postgresql``, ``sqlite``, or whatever else speaks the same way.
        connection_url: the URL the abstraction builds its engine from.
        engine_params: pool and connection arguments for that engine.
        supports_returning: whether ``INSERT ... RETURNING`` answers the new id,
            rather than the driver having to ask for the last one.
        carries_messages: whether this database can carry a message between
            replicas -- what ``notify_transport`` needs.
    """

    dialect: str
    connection_url: str = ""
    engine_params: Mapping[str, Any] = field(default_factory=dict)
    supports_returning: bool = True
    carries_messages: bool = False


@runtime_checkable
class DatasourceDriver(Protocol):
    """What a database backend answers the abstraction with.

    A driver creates no table and owns no pool: it says what database this is and
    how to open it, and the abstraction does the rest.
    """

    def spec(self) -> DriverSpec:
        """How this database is opened and what it can do."""
        ...
