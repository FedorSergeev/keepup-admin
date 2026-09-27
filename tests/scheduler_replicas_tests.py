"""The scheduler and post_construct on several replicas (keepup-46).

Job defaults that a replica set cannot leave to APScheduler, and a way for a
plugin to run post_construct on one replica of a set that starts together.

    python3 -m pytest keepup/tests/scheduler_replicas_tests.py -v
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from keepup import scheduler as scheduling
from keepup.db import DatabaseManagerV2
from keepup.plugins.base import BasePlugin
from keepup.plugins.registry import _post_construct
from keepup.schema import init_db

PACKAGE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


@pytest.fixture(autouse=True)
def no_locks_left_over():
    DatabaseManagerV2.execute_commit("DELETE FROM distributed_locks")
    yield
    DatabaseManagerV2.execute_commit("DELETE FROM distributed_locks")


# --- job defaults -----------------------------------------------------------------

def job_options(sched):
    """What a job added to this scheduler gets; the options settle once it runs."""
    sched.start(paused=True)
    try:
        job = sched.add_job(lambda: None, "interval", seconds=60, id="probe")
        return {"coalesce": job.coalesce, "max_instances": job.max_instances,
                "misfire_grace_time": job.misfire_grace_time}
    finally:
        sched.shutdown(wait=False)


async def test_a_new_scheduler_coalesces_runs_one_at_a_time_and_tolerates_lateness():
    assert job_options(scheduling.new_scheduler()) == {
        "coalesce": True, "max_instances": 1, "misfire_grace_time": 60}


@pytest.fixture
def global_scheduler_restored():
    before = scheduling.scheduler
    yield
    scheduling.scheduler = before


async def test_an_application_overrides_one_default_and_keeps_the_rest(global_scheduler_restored):
    sched = scheduling.init_scheduler({"misfire_grace_time": 300})
    assert scheduling.get_scheduler() is sched
    assert job_options(sched) == {"coalesce": True, "max_instances": 1,
                                  "misfire_grace_time": 300}


def test_the_scheduler_exists_before_the_plugins_initialise():
    """A plugin registering jobs while initialising has to find it."""
    source = (PACKAGE / "factory.py").read_text(encoding="utf-8")
    created = source.index("scheduler = init_scheduler(settings.scheduler_job_defaults)")
    assert created < source.index("await initialize_plugins(app, manager")
    assert source.index("await initialize_plugins(app, manager") < source.index("scheduler.start()")


# --- post_construct -----------------------------------------------------------------

class Checking(BasePlugin):
    def __init__(self, once=False, quiet=600):
        super().__init__("checking", "Checking", {})
        self.post_construct_once_per_cluster = once
        self.post_construct_quiet_seconds = quiet
        self.ran = 0

    async def initialize(self):
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}

    async def post_construct(self):
        self.ran += 1
        return "checked"


async def test_by_default_every_replica_runs_its_own():
    first, second = Checking(), Checking()
    await _post_construct("checking", first)
    await _post_construct("checking", second)
    assert (first.ran, second.ran) == (1, 1)


async def test_once_per_cluster_the_second_replica_of_the_rollout_skips():
    first, second = Checking(once=True), Checking(once=True)
    assert await _post_construct("checking", first) == "checked"
    assert await _post_construct("checking", second) is None
    assert (first.ran, second.ran) == (1, 0)


async def test_after_the_quiet_window_a_new_rollout_runs_it_again():
    first, later = Checking(once=True, quiet=60), Checking(once=True, quiet=60)
    await _post_construct("checking", first)
    DatabaseManagerV2.execute_commit(
        "UPDATE distributed_locks SET acquired_at = :then WHERE lock_name = :name",
        {"then": datetime.utcnow() - timedelta(seconds=120), "name": "post_construct_checking"})
    await _post_construct("checking", later)
    assert later.ran == 1
