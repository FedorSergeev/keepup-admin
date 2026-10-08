"""System metrics as a capability, and the two route keys it needed.

Task keepup-112. The scrape is the reason keepup-102 added two keys to a route:
it answers text rather than JSON, and it is asked every fifteen seconds by every
replica, so it must not be written to the incoming-request audit. The panel's
replica list comes from the table itself rather than from the cluster registry,
which is a soft requirement -- metrics report on replicas and must not fail to
start for want of the registry they report on.
"""

import asyncio

from keepup.kernel import create_runtime, invoke
from keepup.kernel.call import Call, route_kind
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


def add_metric_rows(rows):
    """Answer the metrics queries with these rows."""
    async def execute(statement, params=None):
        SourcePlugin.source.statements.append(("read", statement, params))
        return rows
    SourcePlugin.source.execute = execute


async def running_deployment(*extra):
    """The framework's own metrics plugin on a stub data source, started."""
    SourcePlugin.source = None
    runtime = create_runtime(
        application_catalogue={"plugins": [{"id": "db", "enabled": True},
                                           {"id": "metrics", "enabled": True}]},
        entry_points=[entry_point(SourcePlugin)] + [entry_point(cls) for cls in extra],
    )
    await runtime.start()
    return runtime


def route_of(runtime, path):
    """The declared route with this path."""
    for spec in runtime.route_specs():
        if spec.path == path:
            return spec
    raise AssertionError(f"no route {path}")


def test_the_scrape_is_text_and_is_not_audited():
    """A row per scrape per replica is not an audit; JSON is not a scrape."""
    runtime = asyncio.run(running_deployment())
    spec = route_of(runtime, "/metrics")
    assert spec.require_auth is False
    assert spec.audit is False
    assert spec.response_media_type == "text/plain"
    assert route_kind(spec.methods)[0] == "read"


def test_the_scrape_answers_text_even_with_no_registry():
    """A scrape that answers nothing is fine; a 500 is not."""
    runtime = asyncio.run(running_deployment())
    answer = asyncio.run(invoke(Call(route=route_of(runtime, "/metrics"), source="test")))
    assert isinstance(answer, str)


def test_the_panel_sees_the_replicas_the_table_has_heard_from():
    """No cluster registry is needed: the table says who is alive."""
    runtime = asyncio.run(running_deployment())
    add_metric_rows([
        {"metric_name": "cpu.percent.total", "metric_value": 12.5,
         "app_instance": "replica-a", "timestamp": "2026-10-08 12:00:00"},
        {"metric_name": "memory.percent", "metric_value": 40.0,
         "app_instance": "replica-a", "timestamp": "2026-10-08 12:00:00"},
        {"metric_name": "cpu.percent.total", "metric_value": 3.0,
         "app_instance": "replica-b", "timestamp": "2026-10-08 12:00:01"},
    ])
    answer = asyncio.run(invoke(Call(route=route_of(runtime, "/api/admin/metrics/system"),
                                     params={"fresh_minutes": 5}, source="test")))
    assert {instance["instance"] for instance in answer["instances"]} == {"replica-a", "replica-b"}
    assert answer["instances"][0]["metrics"]["cpu.percent.total"]["value"] == 12.5


def test_history_asks_for_the_metrics_the_panel_draws():
    """The panel's two lines are the query's two names."""
    runtime = asyncio.run(running_deployment())
    add_metric_rows([])
    asyncio.run(invoke(Call(route=route_of(runtime, "/api/admin/metrics/history"),
                            params={"hours": 24}, source="test")))
    kind, statement, params = SourcePlugin.source.statements[-1]
    assert "system_metrics" in statement
    assert params["names"] == ("cpu.percent.total", "memory.percent")


def test_the_cluster_is_a_soft_requirement():
    """Metrics report on replicas; they must not fail for want of the registry."""
    runtime = asyncio.run(running_deployment())
    row = next(row for row in runtime.report() if row["id"] == "metrics")
    assert row["initialized"] is True
    assert row["degraded"] == ["cluster>=1", "scheduler>=1"]
    assert runtime.get_plugin("metrics") is not None


def test_another_plugin_publishes_numbers_through_the_service():
    """The metrics service is how a capability contributes a collector."""
    runtime = asyncio.run(running_deployment())
    service = runtime.services.require("metrics")
    service.collect(lambda: [("notes.count", 3)])
    assert runtime.get_plugin("metrics").get_metric_collectors() == service.collectors


def test_the_section_and_the_table_are_contributions():
    """A capability brings its own section and owns its own table."""
    runtime = asyncio.run(running_deployment())
    sections = runtime.contributions.of("sections")
    assert [section["id"] for section in sections] == ["metrics"]
    assert sections[0]["icon"] == "activity"
    ensured = [declaration.name for declaration in SourcePlugin.source.ensured[0]]
    assert ensured == ["system_metrics"]
