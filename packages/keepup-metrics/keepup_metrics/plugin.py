"""System metrics as a capability: a scrape, a panel view and a table.

Task keepup-112. Collecting this replica's numbers, answering Prometheus and
showing the panel what the fleet is doing used to be three modules of the kernel
that every deployment paid for whether or not it wanted a metrics collector. The
plugin here owns all three, and it is the second capability assembled from the
constructor -- the one that needed the two route keys keepup-102 added:

* ``/metrics`` answers text, not JSON (``response_media_type``), and it is asked
  every fifteen seconds by every replica, so it is not written to the
  incoming-request audit (``audit: False``). A row per scrape per replica is not
  an audit.
* the replica list comes from the table itself -- ``system_metrics.app_instance``
  within a freshness window -- rather than from the cluster registry, so metrics
  do not fail to start for want of a registry they are supposed to report on.
  The cluster is a soft requirement (``wants``): with it, the panel names the
  replicas it knows; without it, the ones it has heard from.

The collector, the exposition and the snapshot sweep live in this distribution
(`keepup_metrics.collector`, `.api`, `.retention`), and the plugin starts and
stops them: they used to be tasks of the kernel's lifespan, which collected for
a deployment that never installed the capability.

The application still registers the routes the plugin declares, because the
transport that mounts what a capability declares is `keepup-http` and does not
exist yet (keepup-121); the declarations themselves are already here.
"""

import asyncio
import logging
import warnings
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.plugins.base import BasePlugin
from keepup_metrics.api import PANEL_METRICS, get_latest_metric_value
from keepup_metrics.collector import metrics_collector, update_metrics_background
from keepup_metrics.retention import metrics_retention_background
from keepup_metrics.tables import SYSTEM_METRICS

logger = logging.getLogger(__name__)

__all__ = ["MetricsPlugin", "MetricsService", "SERVICE_METRICS"]

#: What another plugin asks for to publish numbers of its own.
SERVICE_METRICS = "metrics"

#: A replica is "live" if it wrote a snapshot this recently.
FRESH_WINDOW_MINUTES = 5
#: How much history the panel shows.
PANEL_HISTORY_HOURS = 24
#: The metrics the panel draws a line for.
HISTORY_METRICS = ("cpu.percent.total", "memory.percent")


class MetricsService:
    """What the panel asks of this replica's numbers, and what plugins publish."""

    def __init__(self, datasource: Any):
        self.datasource = datasource
        self.collectors: List[Any] = []

    def collect(self, collector: Any) -> None:
        """Let a plugin publish numbers of its own.

        Args:
            collector: a callable answering ``[(name, value), ...]``.
        """
        self.collectors.append(collector)

    async def latest(self, fresh_minutes: int = FRESH_WINDOW_MINUTES) -> Dict[str, Any]:
        """The newest value of every metric, per replica.

        Args:
            fresh_minutes: how recently a replica must have written to count.

        Returns:
            The replicas seen and the metrics each last reported.
        """
        since = _ago(minutes=fresh_minutes)
        rows = await self.datasource.execute(
            "SELECT metric_name, metric_value, app_instance, timestamp"
            " FROM system_metrics WHERE timestamp >= :since"
            " ORDER BY timestamp DESC",
            {"since": since},
        )
        instances: Dict[str, Dict[str, Any]] = {}
        for row in rows or []:
            row = dict(row)
            instance = instances.setdefault(row.get("app_instance") or "main",
                                            {"instance": row.get("app_instance") or "main",
                                             "metrics": {}})
            instance["metrics"].setdefault(row["metric_name"], {
                "value": row.get("metric_value"), "at": _as_text(row.get("timestamp")),
            })
        return {"fresh_since": since, "instances": list(instances.values())}

    async def history(self, hours: int = PANEL_HISTORY_HOURS) -> Dict[str, Any]:
        """The values of a few metrics over the last hours, oldest first.

        The names are written as one named parameter each: the layer that runs
        the statement does not expand a list into `IN` -- `metric_name IN :names`
        reached the driver as one bind and failed on a real database, which a
        stub data source had hidden (keepup-113).
        """
        since = _ago(hours=hours)
        placeholders = ", ".join(f":name_{index}" for index in range(len(HISTORY_METRICS)))
        params: Dict[str, Any] = {"since": since}
        params.update({f"name_{index}": name for index, name in enumerate(HISTORY_METRICS)})
        rows = await self.datasource.execute(
            "SELECT metric_name, metric_value, app_instance, timestamp"
            " FROM system_metrics"
            f" WHERE timestamp >= :since AND metric_name IN ({placeholders})"
            " ORDER BY timestamp ASC",
            params,
        )
        return {
            "hours": int(hours),
            "since": since,
            "metrics": list(HISTORY_METRICS),
            "points": [dict(row) for row in (rows or [])],
        }

    def scrape(self) -> str:
        """The Prometheus text of this replica's registry.

        Returns:
            Whatever the registry holds; an empty document when there is no
            registry to read, because a scrape that answers nothing is a scrape
            Prometheus can live with, and a 500 is not.
        """
        try:
            from prometheus_client import generate_latest

            payload = generate_latest()
            if isinstance(payload, bytes):
                return payload.decode("utf-8", "replace")
            return str(payload)
        except Exception as error:  # noqa: BLE001 - a scrape must not break a deployment
            logger.warning("Could not render the metrics registry: %s", error)
            return "# no metrics registry in this deployment\n"


class MetricsPlugin(BasePlugin):
    """The capability: a table, a scrape, a panel view and a fresh list of replicas."""

    descriptor = PluginDescriptor(
        id="metrics",
        name="System metrics",
        version="0.4.0",
        distribution="keepup-metrics",
        kind=KIND_OPTIONAL,
        priority=40,
        provides=("metrics>=1",),
        requires=("datasource>=1",),
        wants=("cluster>=1", "scheduler>=1"),
        contributions=("routes", "sections", "tables", "metrics"),
    )

    def __init__(self, config=None):
        super().__init__("metrics", "System metrics", config)

    def register(self, services):
        """Publish the service when there is a data source to read numbers from.

        Args:
            services: the runtime's registry.
        """
        if services.has(SERVICE_DATASOURCE):
            self.service = MetricsService(services.require(SERVICE_DATASOURCE))
            services.provide(SERVICE_METRICS, self.service, version=1, plugin_id="metrics")

    async def initialize(self):
        """Build the service on the data source, or keep the one already published.

        The service was published in ``register()`` -- that is when other plugins
        may ask for it -- so initialising must not replace it with a second one:
        a collector published into the first would disappear from the second.
        """
        if getattr(self, "service", None) is None:
            self.service = MetricsService(self.services.require(SERVICE_DATASOURCE))
        interval = self.config.get("collection_interval")
        if interval is None:
            interval = self._interval_from_the_old_setting()
        if interval is not None:
            metrics_collector.collection_interval = int(interval)
        return True

    def _interval_from_the_old_setting(self) -> Optional[int]:
        """``KeepupSettings.performance.metrics_interval``, until it goes away.

        The interval belongs to this capability now -- ``collection_interval`` in
        the plugin's own config -- and the kernel's field is read here, with a
        warning, so a deployment that set it keeps its numbers until 0.6.0.
        """
        settings = getattr(self.plugin_manager, "settings", None)
        value = getattr(getattr(settings, "performance", None), "metrics_interval", None)
        if value is not None:
            warnings.warn(
                "KeepupSettings.performance.metrics_interval moved to the metrics plugin's "
                "own setting (collection_interval) and goes away in 0.6.0",
                DeprecationWarning,
                stacklevel=2,
            )
        return value

    async def post_construct(self):
        """Start the two loops this capability is: collection and snapshot lifetime.

        They used to be ``asyncio.create_task`` calls in the kernel's lifespan,
        which is why a deployment that never installed this distribution still
        collected numbers and swept the table (keepup-112, keepup-124).
        """
        self._tasks = [
            asyncio.create_task(update_metrics_background()),
            asyncio.create_task(metrics_retention_background()),
        ]

    async def cleanup(self):
        """Stop them; the runtime calls this on shutdown."""
        tasks, self._tasks = getattr(self, "_tasks", []), []
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    def get_declared_tables(self):
        """The table this capability owns, declared once, until keepup-124."""
        return [SYSTEM_METRICS]

    def get_metric_collectors(self):
        """What this plugin itself contributes to the registry."""
        return list(self.service.collectors)

    def get_panel_sections(self):
        """The panel's metrics section, as data."""
        return [{
            "id": "metrics",
            "name": "System metrics",
            "description": "CPU and memory load per replica",
            "js": "/keepup-static/modules/js/metrics.js",
            "css": "/keepup-static/modules/css/metrics.css",
            "initFunction": "renderMetrics",
            "icon": "activity",
            "version": "1.0.0",
        }]

    def get_api_routes(self):
        """A scrape for Prometheus, two views for the panel, and one replica's own."""
        public = bool(getattr(getattr(self.plugin_manager, "settings", None), "metrics_public", False))
        return [
            {"path": "/metrics", "methods": ["GET"], "handler": self.scrape,
             "require_auth": not public, "audit": False, "response_media_type": "text/plain"},
            {"path": "/api/admin/metrics/system", "methods": ["GET"],
             "handler": self.system,
             "params": {"fresh_minutes": {"type": "int", "in": "query", "min": 1, "max": 1440}}},
            {"path": "/api/admin/metrics/history", "methods": ["GET"],
             "handler": self.history_over_time,
             "params": {"hours": {"type": "int", "in": "query", "min": 1, "max": 720}}},
            {"path": "/api/admin/instances/{instance_id}", "methods": ["GET"],
             "handler": self.instance},
            {"path": "/api/admin/instances/{instance_id}/restart", "methods": ["POST"],
             "handler": self.restart},
        ]

    def get_handlers(self):
        """What another plugin may call instead of importing this one."""
        return {"service": self.service, "collect": self.service.collect}

    async def scrape(self):
        """The Prometheus text of this replica."""
        return self.service.scrape()

    async def system(self, current_user=None, fresh_minutes: int = FRESH_WINDOW_MINUTES):
        """The newest value of every metric, per replica."""
        return await self.service.latest(fresh_minutes)

    async def history_over_time(self, current_user=None, hours: int = PANEL_HISTORY_HOURS):
        """A few metrics over the last hours."""
        return await self.service.history(hours)

    async def instance(self, instance_id: str, current_user=None):
        """One replica's latest numbers, as the panel's detail view asks for them."""
        rows = await self.service.datasource.execute(
            "SELECT timestamp FROM system_metrics WHERE app_instance = :instance"
            " ORDER BY timestamp DESC LIMIT 1",
            {"instance": instance_id},
        )
        details = {"instance_id": instance_id, "is_online": False,
                   "current_metrics": {}, "recent_events": [], "uptime": "Unknown"}
        if not rows:
            return details
        details["is_online"] = True
        details["last_update"] = _as_text(dict(rows[0]).get("timestamp"))
        # The same names the summary uses: one list for both doors, or they
        # drift apart and the only one to notice is the person who sees one of
        # them empty and the other full.
        details["current_metrics"] = {
            field: get_latest_metric_value(instance_id, name) * scale
            for field, (name, scale) in PANEL_METRICS.items()
        }
        return details

    async def restart(self, instance_id: str, current_user=None):
        """The cluster's restart command for one replica, recorded with its author."""
        from keepup import cluster

        return await cluster.give_command(instance_id, {"action": cluster.ACTION_RESTART},
                                          current_user)


def _ago(minutes: int = 0, hours: int = 0) -> str:
    """A moment this far back, as the table stores moments."""
    moment = datetime.utcnow() - timedelta(minutes=int(minutes), hours=int(hours))
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _as_text(value: Any) -> Optional[str]:
    """A timestamp as the panel reads it."""
    return value.strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "strftime") else value
