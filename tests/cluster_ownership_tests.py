"""The cluster is a capability: its registry, its state and its gate (keepup-117).

`cluster.py` lived in the kernel, so every deployment -- one replica or ten --
created the registry's tables, and the kernel's lifespan built and launched this
replica's controller itself. The capability does both now, while `instance` and
the notification bus stay kernel infrastructure: they are facts about the
process, not capabilities.

    python3 -m pytest tests/cluster_ownership_tests.py -v
"""

from pathlib import Path

from fastapi.testclient import TestClient

from keepup import factory
from keepup.settings import KeepupSettings
from keepup_cluster import cluster as registry
from keepup_cluster.plugin import ClusterPlugin, SERVICE_CLUSTER

PACKAGE = Path(__file__).resolve().parents[1]


class Services:
    """The registry, reduced to what the plugin's register() uses."""

    def __init__(self):
        self.published = {}

    def has(self, name):
        return True

    def provide(self, name, instance=None, **options):
        self.published[name] = instance


def test_the_kernel_neither_builds_nor_launches_the_controller():
    """Where it used to live: the application's lifespan."""
    source = (PACKAGE / "factory.py").read_text(encoding="utf-8")
    assert "ReplicaController(" not in source
    assert "controller.launch()" not in source
    assert "cluster.set_controller(" not in source

    plugin = (PACKAGE / "packages" / "keepup-cluster" / "keepup_cluster"
              / "plugin.py").read_text(encoding="utf-8")
    assert "ReplicaController(" in plugin and "self.controller.launch()" in plugin


def test_the_capability_declares_the_registry_the_section_and_the_gate():
    """Tables, the panel's section and the middleware that refuses a stopped replica."""
    plugin = ClusterPlugin()

    assert [table.name for table in plugin.get_declared_tables()] == [
        "cluster_members", "cluster_commands"], "the registry's tables are not declared here"
    assert [section["id"] for section in plugin.get_panel_sections()] == ["cluster"]
    gate = plugin.get_middleware()
    assert [spec.name for spec in gate] == ["Stopped replica gate"], (
        "the stopped-replica gate is not a middleware contribution")


def test_the_capability_publishes_the_registry():
    """What the panel and the plugin report ask for instead of importing the kernel."""
    services = Services()
    ClusterPlugin().register(services)

    assert services.published[SERVICE_CLUSTER] is registry


def test_an_application_with_the_profile_brings_the_cluster_up():
    """The controller is built, launched and reachable where the capability runs."""
    app = factory.create_app(KeepupSettings(title="Cluster", static_mounts=(),
                                            plugin_manager=None, profile="panel",
                                            plugins_config_path=None))
    try:
        with TestClient(app):
            kernel = app.state.kernel
            assert kernel.services.has("cluster"), "the capability did not come up"
            assert registry.get_controller() is not None, "no controller was launched"
            assert registry.current_state() == registry.STATE_SERVING
    finally:
        assert registry.get_controller() is None, "the controller outlived the application"


def test_the_delivery_between_replicas_is_the_driver_s():
    """The bus is the kernel's; LISTEN/NOTIFY is the driver's (keepup-117)."""
    kernel = (PACKAGE / "notification_bus.py").read_text(encoding="utf-8")
    assert "asyncpg" not in kernel, "the kernel carries PostgreSQL's delivery again"
    assert "pg_notify" not in kernel, "the kernel sends NOTIFY again"
    assert "class Transport" in kernel and "class InProcessTransport" in kernel

    driver = (PACKAGE / "packages" / "keepup-postgres" / "keepup_postgres"
              / "transport.py").read_text(encoding="utf-8")
    assert "pg_notify" in driver and "asyncpg" in driver, (
        "the driver does not own the delivery its database provides")

    sqlite = (PACKAGE / "builtin" / "sqlite.py").read_text(encoding="utf-8")
    assert "InProcessTransport" in sqlite, (
        "a driver that cannot carry a message between processes does not say so")
