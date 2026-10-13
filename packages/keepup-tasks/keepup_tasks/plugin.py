"""Background tasks as a capability: the one scheduler and the locks under it.

Task keepup-109. The scheduler used to be the kernel's (`keepup/scheduler.py`):
the application's lifespan created it before the plugins and started it after
them, so a deployment that wanted no background work paid for a scheduler, the
panel's "Background tasks" section listed whatever the kernel happened to start
(nothing), and every loop of the framework was an `asyncio.create_task` in that
same lifespan instead of a job somebody could see.

The capability owns it now:

* it starts the scheduler when the application comes up -- with the job defaults
  the deployment named -- and shuts it down with the application;
* it publishes the `scheduler` service, and consumes the `jobs` contribution
  (`(job_id, callable, trigger)`, the contract in doc/plugin_constructor.md), so
  a capability declares its work instead of starting a loop of its own, and the
  section shows every declared job with its next run;
* it publishes the `locks` service. The lock machinery is kernel infrastructure
  -- the kernel's own plugin runtime and the capabilities that outlived it need
  it -- and the capability is what offers it to everyone else.

The routes are declared in `keepup_tasks.scheduler` and registered by the
application until the transport that mounts what a plugin declares exists
(`keepup-http`, keepup-121).
"""

import logging
from typing import Any, List

from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.plugins.base import BasePlugin
from keepup_tasks.scheduler import (
    close_scheduler,
    init_scheduler,
    jobs_payload,
    restart_scheduler,
    stats_payload,
    trigger_job,
)

logger = logging.getLogger(__name__)

__all__ = ["TasksPlugin", "SERVICE_LOCKS", "SERVICE_SCHEDULER"]

#: What another plugin asks for to have its work scheduled.
SERVICE_SCHEDULER = "scheduler"

#: What another plugin asks for to run its work once across the replicas.
SERVICE_LOCKS = "locks"

#: The metrics capability's service, asked for by name: the locks' own numbers
#: are published as a sample instead of being written into its table.
SERVICE_METRICS = "metrics"

#: The panel's section: the jobs the scheduler holds and the locks in force.
SECTION = {
    "id": "background_tasks",
    "name": "Background tasks",
    "description": "Scheduled jobs and the distributed locks they hold",
    "js": "/keepup-static/modules/js/background_tasks.js",
    "css": "/keepup-static/modules/css/background_tasks.css",
    "initFunction": "initBackgroundTasks",
    "icon": "clock",
    "version": "1.0.0",
}


class TasksPlugin(BasePlugin):
    """The scheduler and the locks, and the section that shows both."""

    descriptor = PluginDescriptor(
        id="tasks",
        name="Background tasks",
        version="0.4.0",
        distribution="keepup-tasks",
        kind=KIND_OPTIONAL,
        priority=80,
        provides=(f"{SERVICE_SCHEDULER}>=1", f"{SERVICE_LOCKS}>=1"),
        requires=("datasource>=1",),
        contributions=("routes", "sections", "tables", "jobs"),
    )

    def __init__(self, config=None):
        super().__init__("tasks", "Background tasks", config)
        self.scheduler = None

    def register(self, services):
        """Offer the locks and the scheduler.

        The locks are published here because a plugin may ask for them while it
        registers -- the runtime's own post-construct lock does -- and the
        scheduler is published once it exists (``initialize``), since a
        scheduler that is not started yet is not something to hand out.
        """
        if services.has(SERVICE_DATASOURCE):
            from keepup import locks

            services.provide(SERVICE_LOCKS, locks, version=1, plugin_id="tasks")
            # The locks' own numbers, when something is collecting them: the
            # metrics capability is optional, and the lock machinery does not
            # write into its table (keepup-109).
            if services.has(SERVICE_METRICS):
                services.require(SERVICE_METRICS).collect(locks.samples)

    async def initialize(self):
        """Create the scheduler and start it; the declared jobs join at the freeze."""
        settings = getattr(self.plugin_manager, "settings", None)
        self.scheduler = init_scheduler(getattr(settings, "scheduler_job_defaults", None))
        self.scheduler.start()
        if self.services is not None:
            self.services.provide(SERVICE_SCHEDULER, self.scheduler, version=1,
                                  plugin_id="tasks")
        return True

    async def post_construct(self):
        """Put the declared jobs in, now that every plugin has declared them.

        Contributions are mounted after the plugins initialise (step 8 of the
        lifecycle), so a job another capability declared is known here and not
        in `initialize` -- and a running scheduler takes a job added to it.
        """
        for job in self._declared_jobs():
            self._put(job)
        logger.info("The tasks capability holds %s job(s)",
                    len(self.scheduler.get_jobs()) if self.scheduler else 0)

    def _declared_jobs(self) -> List[Any]:
        """Every job the other plugins declared, in the order they initialised.

        Read here rather than collected by the kernel: the contribution is the
        contract, and consuming it is this capability's work (keepup-109).
        """
        contributions = getattr(self.plugin_manager, "contributions", None)
        if contributions is None:
            return []
        return list(contributions.of("jobs"))

    def _put(self, job) -> None:
        """One declared job: `(job_id, callable, trigger)`, intervals by default."""
        job_id, work, trigger = job
        self.scheduler.add_job(work, id=job_id, trigger="interval", **trigger)

    async def cleanup(self):
        """Stop the scheduler with the application that started it."""
        if self.scheduler is not None:
            close_scheduler()
            self.scheduler = None

    def get_api_routes(self):
        """The section's three doors, declared as data for the transport.

        The application registers the same three through the bridge until
        `keepup-http` mounts what a plugin declares (keepup-121); both answer
        with the payload functions of `keepup_tasks.scheduler`, so there is one
        implementation of each answer.
        """
        return [
            {"path": "/api/admin/scheduler/jobs", "methods": ["GET"],
             "handler": self.jobs},
            {"path": "/api/admin/scheduler/jobs/{job_id}/trigger", "methods": ["POST"],
             "handler": self.trigger},
            {"path": "/api/admin/scheduler/stats", "methods": ["GET"],
             "handler": self.stats},
            {"path": "/api/admin/scheduler/restart", "methods": ["POST"],
             "handler": self.restart},
        ]

    async def jobs(self, current_user=None):
        return jobs_payload()

    async def trigger(self, job_id: str, current_user=None):
        return trigger_job(job_id)

    async def stats(self, current_user=None):
        return stats_payload()

    async def restart(self, current_user=None):
        return restart_scheduler()

    def get_panel_sections(self):
        """The jobs and the locks, as the panel's section."""
        return [dict(SECTION)]

    def get_handlers(self):
        return {"scheduler": self.scheduler}
