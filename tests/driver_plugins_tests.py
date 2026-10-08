"""The two drivers of one abstraction, and the choice the deployment makes.

Tasks keepup-107 and keepup-108. A driver is a plugin of kind `required` that
provides `datasource_driver` and answers the abstraction one question: what
database is this, how is it opened, and what can it do. Two of them ship with
the framework, and both enabled at once would stop the start on purpose -- a
deployment runs one dialect -- so the catalogue, not the installation, is where
the choice is made.

These checks are about the seam and the honesty of `spec()`, not about a
database: nothing here opens one.
"""

import pytest

from keepup.kernel import (
    SERVICE_DATASOURCE_DRIVER,
    DatasourceDriver,
    DriverSpec,
    DuplicateProvider,
    create_runtime,
)
from keepup.kernel.loader import builtin_directory


class AbstractionPlugin:
    """What `keepup-db` will be: the one plugin that needs a driver."""

    from keepup.kernel import KIND_REQUIRED, PluginDescriptor

    descriptor = PluginDescriptor(
        id="db", name="Database abstraction", kind=KIND_REQUIRED,
        priority=5, requires=("datasource_driver>=1",),
    )

    def __init__(self, config=None):
        self.plugin_id = "db"
        self.name = "Database abstraction"

    async def initialize(self):
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


def entry_point(plugin_class):
    """An entry point for that plugin, so the catalogue may declare it."""

    class FakeEntryPoint:
        def __init__(self):
            self.name = plugin_class.descriptor.id
            self.group = "keepup.plugins"

        def load(self):
            return plugin_class

    return FakeEntryPoint()


def runtime_with(catalogue):
    """A runtime over the framework's own plugins and the given catalogue."""
    declared = {entry["id"] for entry in catalogue.get("plugins", [])}
    extra = {"entry_points": [entry_point(AbstractionPlugin)]} if "db" in declared else {}
    return create_runtime(builtin_catalogue={}, application_catalogue=catalogue, **extra)


async def test_the_framework_ships_two_drivers():
    """They are files of the framework until each travels in its own distribution."""
    import os

    shipped = {name for name in os.listdir(builtin_directory()) if name.endswith(".py")}
    assert {"postgres.py", "sqlite.py"} <= shipped


async def test_the_deployment_chooses_the_dialect_by_enabling_one_driver():
    """Two providers of one required service, one of them enabled: that is a stand."""
    runtime = runtime_with({"plugins": [
        {"id": "postgres", "enabled": True},
        {"id": "sqlite", "enabled": False},
    ]})
    await runtime.start()
    driver = runtime.services.require(SERVICE_DATASOURCE_DRIVER)
    assert isinstance(driver, DatasourceDriver)
    spec = driver.spec()
    assert spec.dialect == "postgresql"
    assert spec.carries_messages is True
    assert spec.supports_returning is True
    assert runtime.get_plugin("sqlite") is None


async def test_the_other_choice_gives_the_other_dialect():
    """Nothing else changes: the same code, one line of catalogue."""
    runtime = runtime_with({"plugins": [
        {"id": "postgres", "enabled": False},
        {"id": "sqlite", "enabled": True},
    ]})
    await runtime.start()
    spec = runtime.services.require(SERVICE_DATASOURCE_DRIVER).spec()
    assert spec.dialect == "sqlite"
    assert spec.carries_messages is False
    assert spec.supports_returning is False


async def test_both_enabled_stops_the_start_and_names_both():
    """A deployment that silently picked one would hide which database it runs on."""
    runtime = runtime_with({"plugins": [
        {"id": "db", "enabled": True},
        {"id": "postgres", "enabled": True},
        {"id": "sqlite", "enabled": True},
    ]})
    with pytest.raises(DuplicateProvider) as refused:
        await runtime.start()
    assert "postgres" in str(refused.value) and "sqlite" in str(refused.value)


async def test_a_driver_describes_itself_and_creates_nothing():
    """It brings no tables and owns no pool: that is the abstraction's business."""
    runtime = runtime_with({"plugins": [{"id": "postgres", "enabled": True}]})
    await runtime.start()
    driver = runtime.get_plugin("postgres")
    assert not hasattr(driver, "ensure_tables")
    assert not hasattr(driver, "get_declared_tables")
    assert isinstance(driver.spec(), DriverSpec)


async def test_the_framework_offers_them_without_being_asked():
    """A runtime with the framework's own catalogue finds them and leaves them off."""
    runtime = create_runtime(application_catalogue={"plugins": []})
    await runtime.start()
    ids = {row["id"] for row in runtime.report()}
    assert {"postgres", "sqlite"} <= ids
    assert runtime.services.has(SERVICE_DATASOURCE_DRIVER) is False
