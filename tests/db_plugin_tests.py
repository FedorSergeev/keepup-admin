"""The abstraction as a plugin: every capability reaches storage by name.

Task keepup-124, first slice. The shapes (keepup-106) and the drivers
(keepup-107, keepup-108) were in place and nobody published `datasource`, so a
capability could not reach storage through the seam at all. This is the plugin
that does, and these checks are about the seam: the driver names the dialect, the
declared tables are created through the service, and the abstraction owns none of
the framework's tables itself.
"""

import asyncio

from keepup.kernel import create_runtime
from keepup.kernel.datasource import SERVICE_DATASOURCE, SERVICE_DATASOURCE_DRIVER, DataSource
from keepup.kernel.descriptor import KIND_REQUIRED, PluginDescriptor
from keepup.plugins.base import BasePlugin


class StubDriver(BasePlugin):
    """A driver that answers `spec()` and nothing else."""

    descriptor = PluginDescriptor(id="sqlite", name="SQLite driver", kind=KIND_REQUIRED,
                                  priority=7, provides=("datasource_driver>=1",))

    def __init__(self, config=None):
        super().__init__("sqlite", "SQLite driver", config)

    def register(self, services):
        services.provide(SERVICE_DATASOURCE_DRIVER, self, plugin_id="sqlite")

    def spec(self):
        from keepup.kernel.datasource import DriverSpec

        return DriverSpec(dialect="sqlite", supports_returning=False, carries_messages=False)

    async def initialize(self):
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


class TableOwnerPlugin(BasePlugin):
    """A capability that declares a table and never creates it."""

    descriptor = PluginDescriptor(id="notes", name="Notes", kind=KIND_REQUIRED, priority=20,
                                  requires=("datasource>=1",), contributions=("tables",))

    def __init__(self, config=None):
        super().__init__("notes", "Notes", config)

    async def initialize(self):
        return True

    def get_declared_tables(self):
        return [NOTES]

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


#: A real declaration, of a table no other check uses: the abstraction creates
#: what it is handed, so the check has to hand it something a database accepts.
from sqlalchemy import Column, Integer  # noqa: E402 - after the plugins above

from keepup import tables as table_language  # noqa: E402

NOTES = table_language.table(
    "notes_probe_db_plugin",
    table_language.auto_id(),
    Column("title", Integer),
)


def entry_point(plugin_class):
    """An entry point for a plugin class, named by its descriptor."""

    class FakeEntryPoint:
        def __init__(self):
            self.name = plugin_class.descriptor.id
            self.group = "keepup.plugins"

        def load(self):
            return plugin_class

    return FakeEntryPoint()


def runtime_with(*plugin_classes):
    """The framework's own plugins, with these enabled."""
    return create_runtime(
        application_catalogue={"plugins": [{"id": plugin.descriptor.id, "enabled": True}
                                           for plugin in plugin_classes]},
        entry_points=[entry_point(plugin) for plugin in plugin_classes],
    )


def test_the_abstraction_publishes_the_data_source_every_capability_asks_for():
    """`keepup-db` plus a driver is a deployment with storage."""
    runtime = runtime_with(StubDriver, TableOwnerPlugin)
    asyncio.run(runtime.start())
    source = runtime.services.require(SERVICE_DATASOURCE)
    assert isinstance(source, DataSource)
    assert runtime.get_plugin("db") is not None


def test_the_driver_names_the_dialect_not_the_abstraction():
    """The abstraction asks the driver what this database is."""
    runtime = runtime_with(StubDriver, TableOwnerPlugin)
    asyncio.run(runtime.start())
    assert runtime.services.require(SERVICE_DATASOURCE).dialect == "sqlite"


def test_a_capability_reaches_storage_by_name():
    """Nothing imports the manager: it is asked, through the service."""
    runtime = runtime_with(StubDriver, TableOwnerPlugin)
    asyncio.run(runtime.start())
    handlers = runtime.get_plugin("db").get_handlers()
    assert "service" in handlers
    assert handlers["service"] is runtime.services.require(SERVICE_DATASOURCE)


def test_the_abstraction_owns_no_table_of_the_framework():
    """A table belongs to the capability that keeps it, not to the abstraction."""
    runtime = runtime_with(StubDriver, TableOwnerPlugin)
    asyncio.run(runtime.start())
    assert runtime.get_plugin("db").get_declared_tables() == []


def test_without_a_driver_the_abstraction_does_not_start():
    """No database is named, so there is nothing to be the abstraction of."""
    runtime = runtime_with(TableOwnerPlugin)
    try:
        asyncio.run(runtime.start())
    except Exception as error:  # noqa: BLE001 - any refusal is the point
        assert "datasource_driver" in str(error) or "db" in str(error)
    else:
        raise AssertionError("a deployment with no driver started anyway")
