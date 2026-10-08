"""The integration log: the first capability built from the constructor.

Task keepup-110. Writing to the log has worked for years and showing it never
did -- the panel's section calls four paths and no module of the framework
registered any of them. The plugin declares its table, its routes and its
section as data, publishes a service so another plugin records a call without
importing it, and answers exactly the paths the section calls: the check that
matters most here reads the section's own JavaScript and fails if the two ever
drift apart again.
"""

import asyncio
import re
from pathlib import Path


from keepup.kernel import create_runtime, invoke
from keepup.kernel.call import Call
from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup.kernel.descriptor import KIND_REQUIRED, PluginDescriptor
from keepup.plugins.base import BasePlugin

PACKAGE = Path(__file__).resolve().parents[1]
SECTION_JS = PACKAGE / "static" / "modules" / "js" / "integration_logs.js"


class RecordingSource:
    """A data source that answers like a database and remembers what it was asked."""

    dialect = "stub"

    def __init__(self):
        self.statements = []
        self.ensured = []

    async def execute(self, statement, params=None):
        self.statements.append(("read", statement, params))
        if "COUNT(*)" in statement:
            return [{"count": 2}]
        if "GROUP BY" in statement:
            return [{"day": "2026-10-08", "calls": 2}]
        if "WHERE id" in statement:
            return []
        return [{"id": 1, "host": "billing", "endpoint": "/pay"}]

    async def execute_commit(self, statement, params=None):
        self.statements.append(("write", statement, params))
        return None

    def ensure_tables(self, *tables):
        self.ensured.append(tables)

    def dispose(self):
        return None


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


def entry_point(plugin_class):
    """An entry point for a plugin class, named by its descriptor."""

    class FakeEntryPoint:
        def __init__(self):
            self.name = plugin_class.descriptor.id
            self.group = "keepup.plugins"

        def load(self):
            return plugin_class

    return FakeEntryPoint()


async def running_deployment():
    """The framework's own log plugin on a stub data source, started."""
    SourcePlugin.source = None
    runtime = create_runtime(
        application_catalogue={"plugins": [{"id": "db", "enabled": True},
                                           {"id": "integration_logs", "enabled": True}]},
        entry_points=[entry_point(SourcePlugin)],
    )
    await runtime.start()
    return runtime


def route_of(runtime, path):
    """The declared route with this path."""
    for spec in runtime.route_specs():
        if spec.path == path:
            return spec
    raise AssertionError(f"no route {path}; declared: {[s.path for s in runtime.route_specs()]}")


def test_the_framework_offers_the_log_and_the_plugin_declares_its_table():
    """A capability owns its table and does not create it (keepup-106)."""
    runtime = asyncio.run(running_deployment())
    assert runtime.get_plugin("integration_logs") is not None
    ensured = [declaration.name for declaration in SourcePlugin.source.ensured[0]]
    assert ensured == ["integration_logs"]


def test_the_service_is_how_another_plugin_records_a_call():
    """Nothing imports the log: it is asked, by name, to write."""
    runtime = asyncio.run(running_deployment())
    service = runtime.services.require("integration_log")
    asyncio.run(service.record("billing", "/pay", "POST", user_id=7, username="tester",
                               request_body={"sum": 10}, status_code=200, duration_ms=12))
    kind, statement, params = SourcePlugin.source.statements[-1]
    assert kind == "write"
    assert "INSERT INTO integration_logs" in statement
    assert params["host"] == "billing" and params["username"] == "tester"
    assert params["request_body"] == '{"sum": 10}'


def test_a_body_that_is_too_large_is_truncated_not_kept_whole():
    """One large payload must not dominate the table."""
    runtime = asyncio.run(running_deployment())
    service = runtime.services.require("integration_log")
    asyncio.run(service.record("billing", "/pay", "POST", request_body="x" * 20000))
    params = SourcePlugin.source.statements[-1][2]
    assert params["request_body"].endswith("... [truncated]")
    assert len(params["request_body"]) < 20000


def test_a_route_answers_through_the_neutral_call():
    """The route is data, and the invocation knows nothing about the wire."""
    runtime = asyncio.run(running_deployment())
    spec = route_of(runtime, "/api/integration-logs")
    answer = asyncio.run(invoke(Call(route=spec, params={"limit": 5}, source="test")))
    assert answer["logs"][0]["host"] == "billing"
    assert answer["total"] == 2


def test_the_table_has_a_retention_route_at_last():
    """The table grew without bound: nothing ever deleted from it."""
    runtime = asyncio.run(running_deployment())
    spec = route_of(runtime, "/api/integration-logs/cleanup")
    assert spec.methods == ("POST",)
    answer = asyncio.run(invoke(Call(route=spec, params={"days": 30}, source="test")))
    assert answer == {"removed": 2, "days": 30}
    assert "DELETE FROM integration_logs" in SourcePlugin.source.statements[-1][1]


def test_every_path_the_panel_calls_is_one_the_plugin_declares():
    """The section was a storefront with no shop: this is the check that it is not."""
    runtime = asyncio.run(running_deployment())
    declared = {spec.path for spec in runtime.route_specs()}
    called = set()
    for match in re.finditer(r"/api/integration-logs[^`'\"\s?]*",
                             SECTION_JS.read_text(encoding="utf-8")):
        path = match.group(0).rstrip("/")
        called.add(re.sub(r"\$\{[^}]+\}", "{log_id}", path))
    assert called, "the section calls nothing at all"
    assert called <= declared, f"the panel calls what nothing declares: {sorted(called - declared)}"


def test_the_section_is_a_contribution_and_carries_its_icon():
    """A section comes from the plugin that owns it, with what the shell needs."""
    runtime = asyncio.run(running_deployment())
    sections = runtime.contributions.of("sections")
    assert [section["id"] for section in sections] == ["integration_logs"]
    assert sections[0]["icon"] == "activity"
    assert sections[0]["js"].endswith("integration_logs.js")


def test_a_deployment_without_the_data_source_does_not_start_the_log():
    """No storage, no log: the plugin says so in the report rather than writing nowhere."""
    runtime = create_runtime(application_catalogue={"plugins": [
        {"id": "db", "enabled": False},
        {"id": "postgres", "enabled": False},
        {"id": "sqlite", "enabled": False},
        {"id": "integration_logs", "enabled": True},
    ]})
    asyncio.run(runtime.start())
    row = next(row for row in runtime.report() if row["id"] == "integration_logs")
    assert row["initialized"] is False
    assert row["outcome"] == "unsatisfied"
    assert "datasource>=1" in row["reason"]
    assert runtime.get_plugin("integration_logs") is None
