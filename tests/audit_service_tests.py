"""The audit as a service, and the wrapper that no longer imports it.

Task keepup-111. Three modules of the kernel record what happens -- the incoming
call, the application's events and what an administrator decided -- and all
three import SQLAlchemy, so the base bundle cannot be free of database libraries
while they stay. What this step cuts is the dependency: the route runtime
reaches the audit through the kernel's own name, a deployment that registers no
recording writes none, and the capability publishes the tables it owns.
"""

import asyncio
import ast
from pathlib import Path

from keepup.kernel import create_runtime, invoke
from keepup.kernel.call import Call
from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup.kernel.descriptor import KIND_REQUIRED, PluginDescriptor
from keepup.kernel import observability
from keepup.plugins.base import BasePlugin
from tests.integration_log_plugin_tests import RecordingSource, entry_point

PACKAGE = Path(__file__).resolve().parents[1]


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


class Spy:
    """A recording that remembers every call it was told about."""

    def __init__(self):
        self.started = []
        self.finished = []

    def start(self, method=None, endpoint=None, host=None, request_data=None):
        """An async context manager, as the seam expects."""
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def record():
            self.started.append((method, endpoint))
            yield "id-1"

        return record()

    async def end(self, request_id=None, http_status=None, response_data=None,
                  error_message=None):
        """Remember the outcome of the call."""
        self.finished.append((request_id, http_status))


async def deployment():
    """The framework's own audit plugin on a stub data source, started."""
    SourcePlugin.source = None
    runtime = create_runtime(
        application_catalogue={"plugins": [{"id": "db", "enabled": True},
                                           {"id": "audit", "enabled": True}]},
        entry_points=[entry_point(SourcePlugin)],
    )
    await runtime.start()
    return runtime


def test_the_capability_owns_both_tables_and_publishes_both_services():
    """What is recorded goes through the capability, which owns the tables."""
    runtime = asyncio.run(deployment())
    ensured = [declaration.name for declaration in SourcePlugin.source.ensured[0]]
    assert ensured == ["incoming_requests", "app_events"]
    assert runtime.services.has("audit") and runtime.services.has("events")
    assert runtime.get_plugin("audit") is not None


def test_an_event_is_written_through_the_seam():
    """The kernel's own trail emits without importing the event log."""
    written = []

    async def emitter(event_type, payload):
        written.append((event_type, payload))

    observability.set_recording(observability.Recording(emitters=[emitter]))
    try:
        count = asyncio.run(observability.emit("plugin.decision", {"plugin": "metrics"}))
    finally:
        observability.set_recording(None)
    assert count == 1
    assert written == [("plugin.decision", {"plugin": "metrics"})]


def test_a_deployment_that_records_nothing_still_answers():
    """No recording is a deployment: the call is served, nothing is written."""
    observability.set_recording(None)
    try:
        async def handler():
            return {"ok": True}

        from keepup.kernel.call import RouteSpec

        spec = RouteSpec(path="/api/x", methods=("GET",), handler=handler)
        assert asyncio.run(invoke(Call(route=spec, source="test"))) == {"ok": True}
        assert asyncio.run(observability.emit("anything")) == 0
    finally:
        observability.set_recording(observability.Recording())


def test_the_route_runtime_does_not_import_the_audit_module():
    """The one edge this step cut, kept cut."""
    tree = ast.parse((PACKAGE / "plugins" / "routes.py").read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("keepup.audit") or node.module.startswith("keepup.events"):
                found.add(node.module)
    assert found == set(), f"the route runtime still imports {sorted(found)}"
