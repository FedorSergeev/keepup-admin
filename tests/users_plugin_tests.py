"""Users as a capability: three tables, one service and a section.

Task keepup-105, first half. Accounts are what everything else points at, and
the three tables holding them sit in the kernel's schema; the plugin claims
them, declares them and publishes `users`, so a capability asks who holds a role
rather than importing the module that reads the table.
"""

import asyncio

from keepup.kernel import create_runtime
from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup.kernel.descriptor import KIND_REQUIRED, PluginDescriptor
from keepup.plugins.base import BasePlugin
from tests.integration_log_plugin_tests import RecordingSource, entry_point


class SourcePlugin(BasePlugin):
    """`keepup-db`, reduced to what this file needs."""

    descriptor = PluginDescriptor(id="db", name="Database", kind=KIND_REQUIRED,
                                  priority=5, provides=("datasource>=1",))
    source = None

    def __init__(self, config=None):
        super().__init__("db", "Database", config)

    def register(self, services):
        SourcePlugin.source = RecordingSource()
        services.provide(SERVICE_DATASOURCE, SourcePlugin.source, plugin_id="db")

    async def initialize(self):
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


def runtime_with(catalogue, with_source=True):
    """The framework's own plugins, with these enabled."""
    SourcePlugin.source = None
    return create_runtime(
        application_catalogue={"plugins": catalogue},
        entry_points=[entry_point(SourcePlugin)] if with_source else [],
    )


def test_the_capability_owns_the_three_tables_and_publishes_the_service():
    """Accounts, roles and rights are one capability's tables, declared once."""
    runtime = runtime_with([{"id": "db", "enabled": True}, {"id": "users", "enabled": True}])
    asyncio.run(runtime.start())
    ensured = [declaration.name for declaration in SourcePlugin.source.ensured[0]]
    assert ensured == ["users", "user_roles", "user_permissions"]
    assert runtime.services.has("users")


def test_a_role_is_asked_of_the_service_not_of_the_table():
    """Another capability asks by name; who answers is not its business."""
    runtime = runtime_with([{"id": "db", "enabled": True}, {"id": "users", "enabled": True}])
    asyncio.run(runtime.start())
    service = runtime.services.require("users")
    assert service.has_role({"username": "tester", "role": "ADMIN"}, "ADMIN") is True
    assert service.has_role({"username": "tester", "role": "CLIENT"}, "ADMIN") is False


def test_the_accounts_section_is_a_contribution():
    """The panel's users section comes from the plugin that owns accounts."""
    runtime = runtime_with([{"id": "db", "enabled": True}, {"id": "users", "enabled": True}])
    asyncio.run(runtime.start())
    sections = runtime.contributions.of("sections")
    assert [section["id"] for section in sections] == ["users"]
    assert sections[0]["icon"] == "people"


def test_without_storage_the_accounts_do_not_start():
    """No storage, no accounts: the report says which service is missing."""
    runtime = runtime_with([{"id": "db", "enabled": False},
                            {"id": "postgres", "enabled": False},
                            {"id": "sqlite", "enabled": False},
                            {"id": "users", "enabled": True}], with_source=False)
    asyncio.run(runtime.start())
    row = next(row for row in runtime.report() if row["id"] == "users")
    assert row["outcome"] == "unsatisfied"
    assert "datasource>=1" in row["reason"]
