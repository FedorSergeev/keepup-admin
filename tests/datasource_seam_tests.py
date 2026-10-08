"""The data source as a service: declared tables, and one place that creates them.

Task keepup-106. The kernel stores nothing and knows no database, but three
things have to be true for a capability to be able to: it declares its tables
instead of creating them, the kernel hands those declarations to whatever
answers `datasource`, and a deployment with no data source at all is a
deployment -- one whose plugins that needed storage simply did not start.

The service is a stub here, and that is the check: what is being tested is the
seam and the order, not a database. `keepup-db` and the two drivers are the
subject of their own tasks, and they arrive behind exactly this service.
"""

import pytest

from keepup.kernel import (
    KIND_OPTIONAL,
    KIND_REQUIRED,
    DataSource,
    DatasourceDriver,
    DriverSpec,
    PluginDescriptor,
    SERVICE_DATASOURCE,
    SERVICE_DATASOURCE_DRIVER,
    create_runtime,
)
from keepup.plugins.base import BasePlugin


class RecordingSource:
    """A data source that remembers what it was asked to create."""

    dialect = "stub"

    def __init__(self):
        self.ensured = []
        self.disposed = False

    async def execute(self, statement, params=None):
        """A real source runs the statement; this one answers with nothing."""
        return []

    async def execute_commit(self, statement, params=None):
        """A real source commits; this one has nothing to commit."""
        return None

    def ensure_tables(self, *tables):
        """Remember the declarations, as a real abstraction would create them."""
        self.ensured.append(tables)

    def dispose(self):
        """Remember that it was closed."""
        self.disposed = True


class SourcePlugin(BasePlugin):
    """What `keepup-db` will be: a required plugin that publishes `datasource`."""

    descriptor = PluginDescriptor(
        id="db", name="Database abstraction", kind=KIND_REQUIRED,
        priority=5, provides=("datasource>=1",),
    )

    source = None

    def __init__(self, config=None):
        super().__init__("db", "Database abstraction", config)

    def register(self, services):
        """Publish one data source, and let the check find it afterwards."""
        SourcePlugin.source = RecordingSource()
        services.provide(SERVICE_DATASOURCE, SourcePlugin.source,
                         version=1, plugin_id="db")

    async def initialize(self):
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


class DriverPlugin(BasePlugin):
    """What `keepup-postgres` will be: it says which database this is."""

    descriptor = PluginDescriptor(
        id="postgres", name="PostgreSQL driver", kind=KIND_REQUIRED,
        priority=6, provides=("datasource_driver>=1",),
    )

    def __init__(self, config=None):
        super().__init__("postgres", "PostgreSQL driver", config)

    def register(self, services):
        """Publish the driver, whose shape the abstraction reads."""
        services.provide(SERVICE_DATASOURCE_DRIVER, self, version=1, plugin_id="postgres")

    def spec(self):
        """The one thing a driver answers the abstraction with."""
        return DriverSpec(dialect="postgresql", connection_url="postgresql://localhost/x",
                          carries_messages=True)

    async def initialize(self):
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


class NotesPlugin(BasePlugin):
    """A capability that declares tables and never creates one itself."""

    descriptor = PluginDescriptor(
        id="notes", name="Notes", kind=KIND_REQUIRED,
        priority=10, requires=("datasource>=1",), contributions=("tables",),
    )

    def __init__(self, config=None):
        super().__init__("notes", "Notes", config)

    async def initialize(self):
        return True

    def get_declared_tables(self):
        """What this plugin owns, as declarations rather than DDL."""
        return ["notes", "note_tags"]

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


class OptionalReportPlugin(NotesPlugin):
    """A capability that is optional and still owns tables."""

    descriptor = PluginDescriptor(
        id="reports", name="Reports", kind=KIND_OPTIONAL,
        priority=20, requires=("datasource>=1",), contributions=("tables",),
    )

    def __init__(self, config=None):
        super().__init__(config)
        self.plugin_id = "reports"
        self.name = "Reports"

    def get_declared_tables(self):
        return ["reports"]


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
    """A runtime whose catalogue enables exactly these plugins."""
    return create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue={
            "plugins": [{"id": plugin_class.descriptor.id, "enabled": True}
                        for plugin_class in plugin_classes]
        },
        entry_points=[entry_point(plugin_class) for plugin_class in plugin_classes],
    )


async def test_declared_tables_are_created_through_the_service():
    """A capability declares; the abstraction creates; the kernel only hands them over."""
    SourcePlugin.source = None
    runtime = runtime_with(DriverPlugin, SourcePlugin, NotesPlugin)
    await runtime.start()
    assert [list(declaration) for declaration in SourcePlugin.source.ensured] == [
        ["notes", "note_tags"],
    ]
    assert runtime.get_plugin("notes") is not None


async def test_the_required_tables_come_before_the_optional_ones():
    """The order is the one foreign keys need: what a report points at exists first."""
    SourcePlugin.source = None
    runtime = runtime_with(DriverPlugin, SourcePlugin, NotesPlugin, OptionalReportPlugin)
    await runtime.start()
    created = [declaration[0] for declaration in SourcePlugin.source.ensured]
    assert created == ["notes", "reports"]


async def test_a_deployment_with_no_data_source_creates_nothing_and_still_reports():
    """No storage is a deployment: the plugins that needed it say so, the rest runs."""
    SourcePlugin.source = None
    runtime = runtime_with(OptionalReportPlugin)
    await runtime.start()
    assert SourcePlugin.source is None
    row = runtime.report()[0]
    assert row["outcome"] == "unsatisfied"
    assert "datasource>=1" in row["reason"]


async def test_a_plugin_declaring_tables_without_a_source_is_not_started():
    """It cannot keep what it owns, so it does not pretend to."""
    SourcePlugin.source = None
    runtime = runtime_with(NotesPlugin)
    with pytest.raises(Exception) as refused:
        await runtime.start()
    assert "datasource>=1" in str(refused.value)


async def test_a_driver_says_which_database_this_is():
    """The abstraction asks the driver; nothing else knows the driver's name."""
    runtime = runtime_with(DriverPlugin, SourcePlugin, NotesPlugin)
    await runtime.start()
    driver = runtime.services.require(SERVICE_DATASOURCE_DRIVER)
    spec = driver.spec()
    assert spec.dialect == "postgresql"
    assert spec.carries_messages is True
    assert isinstance(SourcePlugin.source, DataSource)
    assert isinstance(driver, DatasourceDriver)


async def test_the_service_is_closed_when_the_runtime_stops():
    """What a provider opened, it closes: a pool outliving its runtime leaks."""
    SourcePlugin.source = None
    runtime = runtime_with(DriverPlugin, SourcePlugin, NotesPlugin)
    await runtime.start()
    await runtime.stop()
    assert SourcePlugin.source.disposed is True


def test_the_two_names_are_the_ones_the_catalogue_writes_down():
    """The names are data: the guide, the service catalogue and the code agree."""
    from keepup.kernel.datasource import SERVICE_DATASOURCE as own
    from keepup.kernel.datasource import SERVICE_DATASOURCE_DRIVER as driver

    assert (own, driver) == ("datasource", "datasource_driver")
    catalogue = (__import__("pathlib").Path("doc") / "service-catalogue.md").read_text(
        encoding="utf-8")
    assert "`datasource`" in catalogue and "`datasource_driver`" in catalogue
