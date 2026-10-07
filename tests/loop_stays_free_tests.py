"""A query to the database never holds the event loop (keepup-44).

Every query of DatabaseManagerV2 blocks its thread until the database answers,
and in a coroutine that thread is the loop's: while it waits the process serves
nobody. So async code awaits the ``*_async`` variants (a worker thread), async
endpoints that only do blocking work are plain ``def`` (FastAPI runs those in
its thread pool), and the rest hands its blocking helpers to
``asyncio.to_thread``.

Checked two ways: statically -- no coroutine in the framework calls a blocking
query method directly -- and by behaviour: with the database made slow, a
second task on the same loop keeps running.

    python3 -m pytest keepup/tests/loop_stays_free_tests.py -v
"""

import ast
import asyncio
import time
from datetime import datetime
from pathlib import Path

import pytest

from keepup import audit, cluster, locks
from keepup.tests.repository import is_not_the_package
from keepup.db import DatabaseManagerV2
from keepup.events import event_manager
from keepup.schema import init_db

PACKAGE = Path(__file__).resolve().parents[1]
BLOCKING = {"execute", "execute_one", "execute_commit", "execute_many",
            "execute_commit_returning"}
SLOW = 0.3


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


# --- statically -------------------------------------------------------------------

def blocking_calls_in_coroutines(path: Path):
    """(line, coroutine, call) for every blocking query called directly in a coroutine."""
    found = []

    def walk(node, coroutine):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AsyncFunctionDef):
                walk(child, child.name)
            elif isinstance(child, (ast.FunctionDef, ast.Lambda)):
                walk(child, None)
            else:
                if coroutine and isinstance(child, ast.Call) \
                        and isinstance(child.func, ast.Attribute) \
                        and ast.unparse(child.func.value) == "DatabaseManagerV2" \
                        and child.func.attr in BLOCKING:
                    found.append((child.lineno, coroutine, child.func.attr))
                walk(child, coroutine)

    walk(ast.parse(path.read_text(encoding="utf-8")), None)
    return found


def test_no_coroutine_of_the_framework_queries_the_database_on_the_loop():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "tests" in path.parts or is_not_the_package(path):
            continue
        offenders += [f"{path.relative_to(PACKAGE)}:{line} {name}() calls {call}"
                      for line, name, call in blocking_calls_in_coroutines(path)]
    assert offenders == []


def test_the_guard_does_find_one():
    sample = PACKAGE / "tests" / "_blocking_sample.py"
    sample.write_text("async def f():\n    DatabaseManagerV2.execute('SELECT 1')\n")
    try:
        assert blocking_calls_in_coroutines(sample) == [(2, "f", "execute")]
    finally:
        sample.unlink()


# --- by behaviour -----------------------------------------------------------------

@pytest.fixture
def slow_database(monkeypatch):
    """Every blocking query takes SLOW seconds longer."""
    for name in BLOCKING:
        real = getattr(DatabaseManagerV2, name)

        def slowed(*args, _real=real, **kwargs):
            time.sleep(SLOW)
            return _real(*args, **kwargs)
        monkeypatch.setattr(DatabaseManagerV2, name, slowed)


async def runs_beside(work):
    """How long a second task on the loop waited between its steps while work ran."""
    gaps = []

    async def other():
        last = time.monotonic()
        for _ in range(40):
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    # The other task is running before the work starts, so a block anywhere in
    # the work shows up as a gap between two of its steps.
    watcher = asyncio.create_task(other())
    await asyncio.sleep(0.015)
    await work
    await watcher
    return max(gaps)


async def test_an_event_is_written_without_holding_the_loop(slow_database):
    assert await runs_beside(event_manager.create_event("loop_check", "text")) < SLOW / 2


async def test_a_lock_is_taken_and_released_without_holding_the_loop(slow_database):
    lock = locks.DatabaseLock("loop-check")

    async def take_and_release():
        assert await lock.acquire()
        await lock.release()
    assert await runs_beside(take_and_release()) < SLOW / 2


async def test_the_audit_flush_writes_without_holding_the_buffer(monkeypatch):
    """The buffer lock is taken by every request; the write is done without it."""
    now = datetime.utcnow()
    audit.incoming_requests_buffer.clear()
    audit.incoming_requests_buffer["done"] = {
        "instance_id": "i", "method": "GET", "endpoint": "/x", "host": "h",
        "request_data": None, "request_start_at": now, "request_end_at": now,
        "duration_ms": 1, "http_status": 200, "created_at": now}
    written = asyncio.Event()

    def slow_write(query, rows):
        time.sleep(SLOW)
        written.set()
        return len(rows)
    monkeypatch.setattr(DatabaseManagerV2, "execute_many", slow_write)

    async def a_request_meanwhile():
        await asyncio.sleep(0.02)
        started = time.monotonic()
        async with audit.incoming_requests_lock:
            waited = time.monotonic() - started
        assert not written.is_set()          # the write is still going on
        return waited

    flushed, waited = await asyncio.gather(
        audit.IncomingRequestLogger.flush_buffer(), a_request_meanwhile())
    assert flushed == 1 and waited < SLOW / 2
    assert "done" not in audit.incoming_requests_buffer


async def test_a_failed_flush_puts_the_rows_back(monkeypatch):
    now = datetime.utcnow()
    audit.incoming_requests_buffer.clear()
    audit.incoming_requests_buffer["kept"] = {
        "instance_id": "i", "method": "GET", "endpoint": "/y", "host": "h",
        "request_data": None, "request_start_at": now, "request_end_at": now,
        "created_at": now}

    def broken(query, rows):
        raise RuntimeError("database is down")
    monkeypatch.setattr(DatabaseManagerV2, "execute_many", broken)
    assert await audit.IncomingRequestLogger.flush_buffer() == 0
    assert "kept" in audit.incoming_requests_buffer
    audit.incoming_requests_buffer.clear()


async def test_the_cluster_heartbeat_does_not_hold_the_loop(monkeypatch):
    for name in ("publish_member", "expire_commands", "prune_members"):
        monkeypatch.setattr(cluster, name, lambda *a, **k: time.sleep(SLOW / 3))
    monkeypatch.setattr(cluster, "pending_commands_for", lambda *a: [])
    controller = cluster.ReplicaController.__new__(cluster.ReplicaController)
    controller._lock = asyncio.Lock()
    controller.instance_id = "loop-check"

    async def plugins():
        return cluster.PluginPicture(running=[], pending=[])
    controller._plugins = plugins
    controller._clock = datetime.utcnow
    controller.as_member = lambda picture, now: {}
    assert await runs_beside(controller.tick()) < SLOW / 3


# --- a cancelled lock holder stays cancelled ---------------------------------------

async def test_a_holder_cancelled_while_the_renewal_winds_down_is_not_revived():
    """The window this change widened: more awaits, more places a cancel lands.

    A background loop holding a lock is cancelled at shutdown just as its body
    ends, while the lock waits for its renewal task to stop. The lock used to
    swallow that cancellation with the renewal's, and the loop went back to its
    sleep -- asyncio.run then waited for it forever.
    """
    rounds = []

    async def background_loop():
        while True:
            async with locks.distributed_lock("cancel-window", timeout=5, max_lock_time=60):
                rounds.append(1)
                # The cancel arrives on the next turn of the loop: while the
                # lock's exit is waiting for the renewal to wind down.
                asyncio.get_running_loop().call_soon(holder.cancel)
            await asyncio.sleep(0.05)

    holder = asyncio.create_task(background_loop())
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(holder, timeout=5)
    assert rounds == [1]
    assert DatabaseManagerV2.execute(
        "SELECT * FROM distributed_locks WHERE lock_name = :name",
        {"name": "cancel-window"}) == []
