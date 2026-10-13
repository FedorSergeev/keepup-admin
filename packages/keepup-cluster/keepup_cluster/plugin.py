"""The cluster as a capability: the replicas, their state and their commands (keepup-117).

`cluster.py` lived in the kernel, so every deployment -- one replica or ten --
carried the registry, the table of commands and the panel that shows them, and
the kernel's lifespan built and launched the controller itself.

The capability owns all of that now:

* it starts this replica's controller when the application comes up and stops it
  with the application;
* it declares the cluster's two tables, its panel section and the gate that
  refuses the API of a replica an administrator stopped -- the gate as a
  `middleware` contribution, because in a runtime without HTTP there is nowhere
  to put it (the application still installs it until `keepup-http` mounts what a
  plugin declares, keepup-121);
* it publishes the `cluster` service, which is how the panel and the plugin
  report reach the registry.

`keepup.instance` and `keepup.notification_bus` stay kernel infrastructure: who
this process is, and how replicas reach each other, are facts about the process.
Delivery *between* replicas is a service -- `notify_transport` -- that the
database driver provides; until every driver publishes it, this capability
*wants* it rather than requiring it, so a deployment whose database cannot carry
a message reports itself degraded instead of keeping a registry nobody hears.
"""

import logging
from typing import Any, Callable, Dict, List

from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.plugins.base import BasePlugin

logger = logging.getLogger(__name__)

__all__ = ["ClusterPlugin", "SERVICE_CLUSTER"]

#: What the panel and the plugin report ask for to see the replicas.
SERVICE_CLUSTER = "cluster"

#: The panel's section: the replicas, their plugins and their state.
SECTION = {
    "id": "cluster",
    "name": "Cluster",
    "description": ("Application replicas, their plugins and state; stop, start "
                    "and restart a replica from the panel"),
    "js": "/keepup-static/modules/js/cluster.js",
    "css": "/keepup-static/modules/css/cluster.css",
    "initFunction": "initCluster",
    "icon": "server",
    "version": "1.0.0",
}


class ClusterPlugin(BasePlugin):
    """This replica's side of the cluster."""

    descriptor = PluginDescriptor(
        id="cluster",
        name="Cluster",
        version="0.4.0",
        distribution="keepup-cluster",
        kind=KIND_OPTIONAL,
        priority=85,
        provides=(f"{SERVICE_CLUSTER}>=1",),
        requires=("datasource>=1",),
        wants=("notify_transport>=1", "scheduler>=1"),
        contributions=("routes", "sections", "tables", "middleware"),
    )

    def __init__(self, config=None):
        super().__init__("cluster", "Cluster", config)
        self.controller = None

    def register(self, services):
        """Offer the registry to whoever asks for it."""
        from keepup_cluster import cluster as registry

        services.provide(SERVICE_CLUSTER, registry, version=1, plugin_id="cluster")

    async def initialize(self):
        """Build this replica's controller and launch its heartbeat."""
        from keepup_cluster import cluster as registry

        self.controller = registry.ReplicaController(plugins=self._plugin_picture(),
                                                     build=build_label())
        registry.set_controller(self.controller)
        self.controller.launch()
        logger.info("Cluster: replica %s is %s", self.controller.instance_id,
                    self.controller.state)
        return True

    async def cleanup(self):
        """Stop the heartbeat and forget the controller with the application."""
        from keepup_cluster import cluster as registry

        if self.controller is not None:
            await self.controller.shutdown()
            registry.set_controller(None)
            self.controller = None

    def _plugin_picture(self) -> Callable[[], Any]:
        """A callable the controller asks for what runs, from the plugin report."""
        from keepup.plugins.registry import get_plugins_status
        from keepup_cluster import cluster as registry

        manager = self.plugin_manager

        async def picture():
            if manager is None:
                return registry.PluginPicture([], [])
            try:
                rows = (await get_plugins_status(manager)).get("plugins", [])
            except Exception as error:  # noqa: BLE001 - a report that cannot be read
                logger.warning("Cluster: plugin report unavailable: %s", error)
                rows = []
            return registry.PluginPicture(
                running=[row["id"] for row in rows if row.get("initialized")],
                pending=[row["id"] for row in rows if row.get("pending_restart")])

        return picture

    def get_declared_tables(self) -> List[Any]:
        """The registry's two tables, declared by the capability that owns them."""
        from keepup_cluster.tables import CLUSTER_COMMANDS, CLUSTER_MEMBERS

        return [CLUSTER_MEMBERS, CLUSTER_COMMANDS]

    def get_panel_sections(self) -> List[Dict[str, Any]]:
        """The section, declared as data; the file is the seed it is imported from."""
        return [dict(SECTION)]

    def get_middleware(self) -> List[Any]:
        """The gate of a stopped replica, as the transport's contribution."""
        from keepup.kernel.contributions import MiddlewareSpec
        from keepup_cluster.cluster import StoppedReplicaGate

        return [MiddlewareSpec(order=10, factory=StoppedReplicaGate,
                               name="Stopped replica gate")]

    def get_api_routes(self) -> List[Dict[str, Any]]:
        """No routes yet: the application registers them through the bridge."""
        return []

    def get_handlers(self) -> Dict[str, Any]:
        from keepup_cluster import cluster as registry

        return {"registry": registry, "controller": self.controller}


def build_label() -> Any:
    """The build this replica runs, from the version file build_image.sh writes."""
    import json
    import os

    from keepup import web

    try:
        with open(os.path.join(web.STATIC_DIR, web.VERSION_FILE), encoding="utf-8") as handle:
            version = json.load(handle)
    except (OSError, ValueError):
        return None
    label = str(version.get("version") or "")
    if version.get("build_number"):
        label += f" #{version['build_number']}"
    return label or None
