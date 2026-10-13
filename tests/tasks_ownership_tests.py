"""The background-tasks capability owns the scheduler and the locks (keepup-109).

The scheduler used to be the kernel's: its lifespan created it before the
plugins, started it after them, and every loop of the framework was an
`asyncio.create_task` in that same lifespan. The capability owns it now -- it
creates and starts it, takes the jobs other plugins declare through the `jobs`
contribution, stops it with the application, and publishes the `locks` service
the rest of the framework asks for.

    python3 -m pytest tests/tasks_ownership_tests.py -v
"""

import asyncio
from pathlib import Path

from keepup.kernel import create_runtime
from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup.kernel.descriptor import KIND_REQUIRED, PluginDescriptor
from keepup.plugins.base import BasePlugin
from keepup_tasks import scheduler as scheduling
from keepup_tasks.plugin import SERVICE_LOCKS, SERVICE_SCHEDULER, TasksPlugin
from tests.integration_log_plugin_tests import RecordingSource, entry_point

PACKAGE = Path(__file__).resolve().parents[1]


class SourcePlugin(BasePlugin):
    """`keepup-db`, reduced to the service this file needs."""

    descriptor = PluginDescriptor(id="db", name="Database", kind=KIND_REQUIRED, priority=5,
                                  provides=("datasource>=1",))

    def __init__(self, config=None):
        super().__init__("db", "Database", config)

    def register(self, services):
        services.provide(SERVICE_DATASOURCE, RecordingSource(), plugin_id="db")

    async def initialize(self):
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


class SweeperPlugin(BasePlugin):
    """A capability that declares its work instead of starting a loop of its own."""

    runs = []
    descriptor = PluginDescriptor(id="sweeper", name="Sweeper", kind=KIND_REQUIRED, priority=10)

    def __init__(self, config=None):
        super().__init__("sweeper", "Sweeper", config)

    async def initialize(self):
        return True

    def get_scheduled_jobs(self):
        return [("sweeper_sweep", self.sweep, {"seconds": 3600})]

    async def sweep(self):
        type(self).runs.append(True)

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


def runtime_with_tasks():
    """A runtime with the tasks capability and one plugin declaring a job."""
    return create_runtime(
        application_catalogue={"plugins": [{"id": "tasks", "enabled": True}]},
        entry_points=[entry_point(SourcePlugin), entry_point(SweeperPlugin),
                      entry_point(TasksPlugin)],
    )


def test_the_kernel_neither_creates_nor_starts_the_scheduler():
    """Where it used to live: the application's lifespan."""
    source = (PACKAGE / "factory.py").read_text(encoding="utf-8")
    assert "init_scheduler" not in source
    assert "scheduler.start()" not in source


def test_the_capability_holds_the_declared_jobs_and_publishes_the_services():
    """The contribution is the contract: nobody adds a loop behind the panel's back."""
    async def scenario():
        runtime = runtime_with_tasks()
        await runtime.start()
        try:
            scheduler = scheduling.get_scheduler()
            assert scheduler is not None and scheduler.running, "the capability did not start it"
            assert [job.id for job in scheduler.get_jobs()] == ["sweeper_sweep"], (
                "the declared job did not reach the scheduler")
            assert runtime.services.require(SERVICE_SCHEDULER) is scheduler
            assert runtime.services.require(SERVICE_LOCKS) is not None, (
                "the locks were not offered to the other capabilities")
        finally:
            await runtime.stop()
        assert scheduling.get_scheduler() is None, "the scheduler outlived the application"

    asyncio.run(scenario())


def test_the_scheduler_is_gone_when_the_capability_is_not_enabled():
    """A deployment without the capability runs no background work at all."""
    async def scenario():
        runtime = create_runtime(
            application_catalogue={"plugins": [{"id": "tasks", "enabled": False}]},
            entry_points=[entry_point(SourcePlugin)],
        )
        await runtime.start()
        try:
            assert scheduling.get_scheduler() is None
        finally:
            await runtime.stop()

    asyncio.run(scenario())
