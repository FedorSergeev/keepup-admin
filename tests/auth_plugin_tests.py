"""Sign-in as a capability: two services, two tables and an adopted identity.

Task keepup-116, first half. The kernel owns the shape and the names
(keepup-119); this is a deployment putting an answer behind them, and the two
tables that answer needs -- the panel's sessions and the attempts that throttle
a guesser -- belong to it. The sign-in's routes, sockets and middleware travel
with it in keepup-124.
"""

import asyncio

from keepup.kernel import create_runtime, security
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


def test_the_capability_publishes_both_services_and_owns_both_tables():
    """Who is calling and what they may do: two names, one answer, two tables."""
    runtime = runtime_with([{"id": "db", "enabled": True}, {"id": "auth", "enabled": True}])
    asyncio.run(runtime.start())
    assert runtime.services.has("auth") and runtime.services.has("permissions")
    ensured = [declaration.name for declaration in SourcePlugin.source.ensured[0]]
    assert ensured == ["auth_session", "login_attempts"]


def test_the_runtime_lets_the_plugin_answer_for_the_process():
    """Otherwise the panel would keep signing people in with the old identity."""
    runtime = runtime_with([{"id": "db", "enabled": True}, {"id": "auth", "enabled": True}])
    asyncio.run(runtime.start())
    identity = security.get_identity()
    assert identity is not None and identity.name == "keepup-auth"
    assert security.subject_dependency() is not None
    assert security.checker() is not None


def test_without_the_data_source_the_sign_in_does_not_start():
    """No storage, no sessions: the plugin says so in the report."""
    runtime = runtime_with([{"id": "db", "enabled": False},
                            {"id": "postgres", "enabled": False},
                            {"id": "sqlite", "enabled": False},
                            {"id": "auth", "enabled": True}], with_source=False)
    asyncio.run(runtime.start())
    row = next(row for row in runtime.report() if row["id"] == "auth")
    assert row["outcome"] == "unsatisfied"
    assert "datasource>=1" in row["reason"]


def test_the_right_of_a_route_is_asked_of_the_plugins_identity():
    """A route that declares a right is refused when the plugin says no."""
    runtime = runtime_with([{"id": "db", "enabled": True}, {"id": "auth", "enabled": True}])
    asyncio.run(runtime.start())

    async def refuses(actor, action):
        raise PermissionError("not yours")

    security.set_identity(security.Identity(checker=refuses, name="asked"))
    from keepup.kernel.call import Call, RouteSpec
    from keepup.kernel import invoke

    spec = RouteSpec(path="/api/x", methods=("GET",), handler=lambda: {"ok": True},
                     permission="x.read")
    try:
        asyncio.run(invoke(Call(route=spec, actor={"id": 1}), checker=security.checker()))
    except PermissionError:
        pass
    else:
        raise AssertionError("the right was never asked")
