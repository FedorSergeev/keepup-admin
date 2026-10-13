"""The framework's background loops belong to the capabilities that own them (keepup-109).

The audit buffer, the audit sweep, the event log's sweep and the socket sweep
were four `asyncio.create_task` calls in the kernel's lifespan: a deployment
that installed none of those capabilities still flushed, swept and listened.
Each one starts with its own plugin now, and the kernel starts none of them.

    python3 -m pytest tests/loops_ownership_tests.py -v
"""

from pathlib import Path

from keepup import locks

PACKAGE = Path(__file__).resolve().parents[1]

#: What the kernel used to start, by the name of the callable.
KERNEL_LOOPS = (
    "background_buffer_flusher",
    "audit_retention_background",
    "events_retention_background",
    "socket_sessions.run_forever",
)


def test_the_kernel_starts_none_of_the_loops():
    """Where they used to live: the application's lifespan."""
    source = (PACKAGE / "packages" / "keepup-http" / "keepup_http" / "factory.py").read_text(encoding="utf-8")
    for name in KERNEL_LOOPS:
        assert name not in source, f"the kernel starts {name} again"
    assert "flush_remaining_logs" not in source, "the kernel flushes the audit buffer again"


def test_the_audit_capability_starts_its_three_loops_and_flushes():
    """The buffer, the audit sweep and the event log's sweep are one capability's."""
    plugin = (PACKAGE / "builtin" / "audit.py").read_text(encoding="utf-8")
    for name in ("background_buffer_flusher", "audit_retention_background",
                 "events_retention_background"):
        assert f"asyncio.create_task({name}())" in plugin, f"{name} is not started by its owner"
    assert "flush_buffer(include_in_flight=True)" in plugin, (
        "the buffer is not emptied by the capability that holds it")


def test_the_sign_in_capability_starts_the_socket_sweep():
    """A socket is signed in by this capability, so it is ended by it too."""
    plugin = (PACKAGE / "builtin" / "auth.py").read_text(encoding="utf-8")
    assert "asyncio.create_task(socket_sessions.run_forever())" in plugin


def test_the_lock_machinery_writes_into_no_other_capability_s_table():
    """`system_metrics` is the metrics capability's; a lock is the kernel's."""
    source = (PACKAGE / "locks.py").read_text(encoding="utf-8")
    assert "system_metrics" not in source.split("def samples()")[0].replace(
        "# never written into `system_metrics`", "")


def test_a_force_release_is_counted_and_offered_as_a_sample():
    """The number an administrator's action produced, read by a collector."""
    before = locks.force_released_total()
    assert locks.samples() == [("locks.force_released", float(before))]
    assert isinstance(before, int)


def test_the_locks_are_published_as_a_service():
    """The tasks capability offers them; the machinery stays the kernel's."""
    from keepup_tasks.plugin import SERVICE_LOCKS, TasksPlugin

    class Services:
        def __init__(self):
            self.published = {}
            self.collected = []

        def has(self, name):
            return name in ("datasource>=1", "datasource", "metrics")

        def provide(self, name, instance=None, **options):
            self.published[name] = instance

        def require(self, name):
            return self.published.get(name) or self

        def collect(self, collector):
            self.collected.append(collector)

    services = Services()
    plugin = TasksPlugin()
    plugin.register(services)
    assert services.published[SERVICE_LOCKS] is locks
    assert services.collected == [locks.samples], "the locks' samples were not offered"
