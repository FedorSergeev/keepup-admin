"""The panel as a capability: sections contributed, granted, and served without storage.

Task keepup-104, first half. The shell, its sections and its themes were the
kernel's. The plugin claims them and publishes `ui`, and storage is a *want*: a
panel whose sections come from code can be served with no database at all --
which is the deployment the specification calls a server that is not an admin
panel.
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


def test_the_panel_owns_its_theme_table_and_publishes_the_service():
    """Themes are the panel's table, declared once and created by the service."""
    runtime = runtime_with([{"id": "db", "enabled": True}, {"id": "ui", "enabled": True}])
    asyncio.run(runtime.start())
    ensured = [declaration.name for declaration in SourcePlugin.source.ensured[0]]
    assert ensured == ["visual_themes"]
    assert runtime.services.has("ui")


def test_the_sections_of_other_capabilities_are_seen_by_the_panel():
    """A capability offers a section; the panel is what knows about them all."""
    runtime = runtime_with([{"id": "db", "enabled": True}, {"id": "ui", "enabled": True}])
    asyncio.run(runtime.start())
    from keepup.kernel import security  # noqa: F401 - the seam is not what this asks

    sections = runtime.services.require("ui").contributed_sections()
    ids = {section["id"] for section in sections}
    assert "panel_sections" in ids


def test_a_panel_without_storage_is_still_a_panel():
    """Storage is a want, not a requirement: sections from code serve anyway."""
    runtime = runtime_with([{"id": "postgres", "enabled": False},
                            {"id": "sqlite", "enabled": False},
                            {"id": "ui", "enabled": True}], with_source=False)
    asyncio.run(runtime.start())
    row = next(row for row in runtime.report() if row["id"] == "ui")
    assert row["initialized"] is True
    assert row["degraded"] == ["datasource>=1"]
    service = runtime.services.require("ui")
    assert [section["id"] for section in service.sections_for({"role": "ADMIN"})] == \
        ["panel_sections"]
    assert asyncio.run(service.themes()) == []


def test_the_panel_offers_its_own_section_and_icon():
    """What the shell shows about itself is a contribution like any other."""
    runtime = runtime_with([{"id": "db", "enabled": True}, {"id": "ui", "enabled": True}])
    asyncio.run(runtime.start())
    section = next(section for section in runtime.services.require("ui").contributed_sections()
                   if section["id"] == "panel_sections")
    assert section["icon"] == "layout"
    assert section["js"].endswith("panel_sections.js")
