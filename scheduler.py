"""One scheduler for the whole process.

Plugins are meant to register their jobs against it rather than each starting
a scheduler of its own, which is what keeps the jobs visible in one place -- and
what lets an administrator see and trigger them. A plugin that does need its
own takes it from new_scheduler(), so it gets the same job defaults. Running the
same job in every replica is prevented one level up, by the distributed lock
around the job itself, not here.

The job defaults are what APScheduler leaves open and a replica set cannot
leave open (keepup-46):

- ``coalesce``: a job that missed several runs -- the process was paused, the
  loop was busy, the machine slept -- runs once on waking, not once per missed
  slot. Without it a sync job that fell an hour behind fires sixty times in a
  row, on every replica that woke up.
- ``max_instances = 1``: a run that has not finished is not started again
  beside itself. Two passes of the same job in one process would contend for
  the same distributed lock and one of them would only log that it lost.
- ``misfire_grace_time``: how late a run may still start. APScheduler's default
  is one second, so a loop held for longer than that silently drops the run.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, HTTPException

from keepup.auth.dependencies import get_current_admin

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "JOB_DEFAULTS",
    "init_scheduler",
    "new_scheduler",
]

logger = logging.getLogger(__name__)

#: The process-wide scheduler, created by init_scheduler().
scheduler = None


def get_scheduler():
    """Return the process-wide scheduler, or None before it is created."""
    return scheduler


#: The job defaults every scheduler of the framework starts with; an
#: application overrides them with ``KeepupSettings.scheduler_job_defaults``.
JOB_DEFAULTS = {
    "coalesce": True,
    "max_instances": 1,
    "misfire_grace_time": 60,
}


def new_scheduler(job_defaults: Optional[Dict[str, Any]] = None) -> AsyncIOScheduler:
    """A scheduler with the framework's job defaults, overridden by ``job_defaults``."""
    return AsyncIOScheduler(job_defaults={**JOB_DEFAULTS, **(job_defaults or {})})


def init_scheduler(job_defaults: Optional[Dict[str, Any]] = None):
    """Create and configure the task scheduler."""
    global scheduler

    scheduler = new_scheduler(job_defaults)

    logger.info("Global scheduler initialized - tasks will be registered by plugins")
    return scheduler


def register_scheduler_routes(app):
    """Register the scheduler endpoints on the application."""

    @app.get("/api/admin/scheduler/jobs")
    async def get_scheduler_jobs(admin: dict = Depends(get_current_admin)):
        """Return the scheduler's jobs (administrators only)."""
        if not scheduler:
            return {"error": "Scheduler not initialized"}

        jobs = []
        for job in scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time,
                "trigger": str(job.trigger)
            })

        return {"jobs": jobs}
    @app.post("/api/admin/scheduler/jobs/{job_id}/trigger")
    async def trigger_scheduler_job(job_id: str, admin: dict = Depends(get_current_admin)):
        """Run a scheduler job by hand (administrators only)."""
        if not scheduler:
            raise HTTPException(status_code=500, detail="Scheduler not initialized")

        job = scheduler.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        job.modify(next_run_time=datetime.utcnow())

        return {"success": True, "message": f"Job {job_id} triggered manually"}
    @app.get("/api/admin/scheduler/stats")
    async def get_scheduler_stats(admin: dict = Depends(get_current_admin)):
        """Return scheduler statistics (administrators only)."""
        if not scheduler:
            return {"error": "Scheduler not initialized"}

        jobs = scheduler.get_jobs()
        next_run_times = [job.next_run_time for job in jobs if job.next_run_time]
        next_wakeup = min(next_run_times) if next_run_times else None

        return {
            "scheduler_running": scheduler.running,
            "total_jobs": len(jobs),
            "next_wakeup": next_wakeup,
            "pending_tasks": len(asyncio.all_tasks()) if asyncio.get_event_loop() else 0
        }
