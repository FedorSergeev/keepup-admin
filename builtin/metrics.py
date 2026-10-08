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

The collector and the table's declaration still live in the kernel
(`keepup/metrics.py`, `keepup/schema.py`) and travel into this capability's own
distribution in keepup-124.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.plugins.base import BasePlugin
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
        """The values of a few metrics over the last hours, oldest first."""
        since = _ago(hours=hours)
        rows = await self.datasource.execute(
            "SELECT metric_name, metric_value, app_instance, timestamp"
            " FROM system_metrics"
            " WHERE timestamp >= :since AND metric_name IN :names"
            " ORDER BY timestamp ASC",
            {"since": since, "names": tuple(HISTORY_METRICS)},
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
        return True

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
        """A scrape for Prometheus, and two views for the panel."""
        return [
            {"path": "/metrics", "methods": ["GET"], "handler": self.scrape,
             "require_auth": False, "audit": False, "response_media_type": "text/plain"},
            {"path": "/api/admin/metrics/system", "methods": ["GET"],
             "handler": self.system,
             "params": {"fresh_minutes": {"type": "int", "in": "query", "min": 1, "max": 1440}}},
            {"path": "/api/admin/metrics/history", "methods": ["GET"],
             "handler": self.history_over_time,
             "params": {"hours": {"type": "int", "in": "query", "min": 1, "max": 720}}},
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


def _ago(minutes: int = 0, hours: int = 0) -> str:
    """A moment this far back, as the table stores moments."""
    moment = datetime.utcnow() - timedelta(minutes=int(minutes), hours=int(hours))
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _as_text(value: Any) -> Optional[str]:
    """A timestamp as the panel reads it."""
    return value.strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "strftime") else value
