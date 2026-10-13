"""The metrics capability owns its execution, not only its table (keepup-112, keepup-124).

The collector and the snapshot sweep used to be two `asyncio.create_task` calls
in the kernel's lifespan, so a deployment that never installed the capability
still collected numbers and swept the table. They belong to the plugin now:
started when the capability's application comes up, stopped when it goes down,
and absent when the capability is not enabled at all.

    python3 -m pytest tests/metrics_ownership_tests.py -v
"""

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from keepup import factory
from keepup.settings import KeepupSettings
from keepup_metrics import plugin as metrics_plugin

PACKAGE = Path(__file__).resolve().parents[1]


def test_the_kernel_starts_neither_loop():
    """Where they used to live: the lifespan of the framework itself."""
    text = (PACKAGE / "factory.py").read_text(encoding="utf-8")
    assert "update_metrics_background" not in text, "the kernel collects metrics again"
    assert "metrics_retention_background" not in text, "the kernel sweeps snapshots again"


async def test_the_plugin_starts_its_loops_and_stops_them(monkeypatch):
    """`post_construct` is the start and `cleanup` is the stop: nobody else's job."""
    started = []

    async def forever():
        started.append(True)
        await asyncio.Event().wait()

    monkeypatch.setattr(metrics_plugin, "update_metrics_background", forever)
    monkeypatch.setattr(metrics_plugin, "metrics_retention_background", forever)

    plugin = metrics_plugin.MetricsPlugin()
    await plugin.post_construct()
    await asyncio.sleep(0)
    assert len(started) == 2, "the capability started neither loop"
    running = list(plugin._tasks)
    assert len(running) == 2

    await plugin.cleanup()
    assert plugin._tasks == []
    assert all(task.done() for task in running), "a loop outlived the application"


def test_a_deployment_without_the_capability_runs_none_of_it():
    """The profile is the connection: without one the plugin is not started."""
    app = factory.create_app(KeepupSettings(title="Bare", project_name="bare",
                                            static_mounts=(), plugin_manager=None))
    with TestClient(app):
        state = app.state.kernel.states["metrics"]
        assert getattr(state, "outcome", None) != "initialized", state
