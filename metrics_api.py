"""How the metrics are handed out: the Prometheus exposition and the panel.

Prometheus scrapes `/metrics` from the registry the collector fills; the admin
panel reads the per-replica snapshots the collector writes to `system_metrics`,
which is how one replica can show the load of all of them.

Separate from the collector (`keepup/metrics.py`, keepup-22): the collector
cares about the names it writes, this module about the summary's fields, its
freshness window and its history period -- and each changes for its own reason.
The retention sweep reads `PANEL_HISTORY_HOURS` from here, since the detailed
window it keeps has to cover the chart the panel draws.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response as FastAPIResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from keepup.auth.dependencies import get_current_admin
from keepup.db import DatabaseManagerV2

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "FRESH_WINDOW_MINUTES",
    "HISTORY_METRICS",
    "PANEL_HISTORY_HOURS",
    "PANEL_METRICS",
    "register_metrics_routes",
]

logger = logging.getLogger(__name__)


#: What the panel shows in the summary and under which names it is stored. The
#: panel used to ask for the names of a disabled plugin (`cpu_usage`,
#: `ram_usage`, `disk_usage`) that nobody wrote -- and showed nothing while
#: collection was running.
#: Summary field -> metric name and the factor to convert by. The factor is
#: here and not in the panel: the field is called `ram_mb` while memory is
#: collected in bytes, and the panel would have shown "6430130176 MB" without a
#: single line of code telling a lie.
PANEL_METRICS = {
    "cpu_percent": ("cpu.percent.total", 1.0),
    "ram_mb": ("memory.used", 1.0 / (1024 * 1024)),
    "disk_usage": ("disk.percent", 1.0),
}

#: The history in the summary: processor and memory over a day.
HISTORY_METRICS = {"cpu": "cpu.percent.total", "ram": "memory.percent"}

#: How many hours of history the summary shows. Named rather than written into
#: the call because it gained a second reader: the retention sweep has to keep
#: its detailed window wider than this period, or a pass would eat the chart.
PANEL_HISTORY_HOURS = 24

#: The freshness window: an instance that has not written metrics for longer
#: than this counts as unreachable.
FRESH_WINDOW_MINUTES = 5


def fresh_since(now: Optional[datetime] = None) -> datetime:
    """The edge of the freshness window.

    Computed here and not in the query: the interval syntax differs between the
    engines, and `CURRENT_TIMESTAMP - INTERVAL '5 minutes'` is a syntax error on
    SQLite -- which meant the summary did not work in development at all.

    Args:
        now: the moment to measure from; defaults to the current time.

    Returns:
        The moment before which an instance counts as stale.
    """
    return (now or datetime.utcnow()) - timedelta(minutes=FRESH_WINDOW_MINUTES)


class SystemMetricsResponse(BaseModel):
    """The summary the panel's metrics section reads."""

    instances: List[Dict[str, Any]]
    historical: Dict[str, List[Dict[str, Any]]]


def get_latest_metric_value(instance_id: str, metric_name: str):
    """An instance's latest value of a metric, or zero.

    Through named parameters: two variants of one query for two engines are two
    places that can drift apart, and they already had.

    Args:
        instance_id: the replica to read.
        metric_name: the metric to read.

    Returns:
        The value, or 0 when there is no row or the read failed.
    """
    try:
        row = DatabaseManagerV2.execute_one(
            "SELECT metric_value FROM system_metrics "
            "WHERE app_instance = :instance AND metric_name = :name "
            "ORDER BY timestamp DESC LIMIT 1",
            {"instance": instance_id, "name": metric_name})
        return float(row["metric_value"]) if row else 0
    except Exception as error:
        # Zero instead of a value: the metrics section must not fail whole
        # because of one row that is not there.
        logger.warning(f"Could not read {metric_name} of {instance_id}: {error}")
        return 0


def get_historical_metrics(metric_name: str, hours: int):
    """A metric's history over a period -- one query for both engines.

    The boundary is computed here: `INTERVAL` and `datetime('now', ...)` are
    the syntax of different engines, and a query written for one does not run
    on the other.

    Args:
        metric_name: the metric to read.
        hours: how far back to read.

    Returns:
        The rows of the history, newest ordering as the query returns them.
    """
    try:
        since = datetime.utcnow() - timedelta(hours=int(hours))
        rows = DatabaseManagerV2.execute(
            "SELECT app_instance, metric_value, timestamp FROM system_metrics "
            "WHERE metric_name = :name AND timestamp > :since ORDER BY timestamp ASC",
            {"name": metric_name, "since": since})
        return [{
            "instance_id": row["app_instance"],
            "metric_value": float(row["metric_value"]),
            "timestamp": row["timestamp"],
        } for row in rows]
    except Exception as error:
        logger.error(f"Error getting historical metrics: {error}")
        return []


def register_metrics_routes(app, public=False):
    """Register the metrics endpoints on the application.

    Args:
        app: the FastAPI application.
        public: whether /metrics answers without credentials. False by default:
            the collection carries the load of the host, the names of the
            replicas and the shape of the traffic, and a package installed on a
            public address should not hand that out because nobody said not to.
            A deployment whose collector cannot present credentials opens it
            deliberately.
    """

    if public:
        @app.get("/metrics")
        async def metrics():
            """Prometheus metrics endpoint, scraped by Prometheus."""
            return FastAPIResponse(
                content=generate_latest(),
                media_type=CONTENT_TYPE_LATEST
            )
    else:
        @app.get("/metrics")
        async def metrics(admin: dict = Depends(get_current_admin)):
            """Prometheus metrics endpoint, for a collector that signs in."""
            return FastAPIResponse(
                content=generate_latest(),
                media_type=CONTENT_TYPE_LATEST
            )
    @app.get("/api/admin/metrics/system", response_model=SystemMetricsResponse)
    def get_system_metrics(admin: dict = Depends(get_current_admin)):
        """Return the current system metrics of every instance."""
        try:
            since = fresh_since()
            instances = DatabaseManagerV2.execute(
                "SELECT DISTINCT app_instance FROM system_metrics "
                "WHERE timestamp > :since ORDER BY app_instance", {"since": since})

            instances_data = []
            for instance in instances:
                instance_id = instance['app_instance']

                latest = DatabaseManagerV2.execute_one(
                    "SELECT metric_name, metric_value, timestamp FROM system_metrics "
                    "WHERE app_instance = :instance AND timestamp > :since "
                    "ORDER BY timestamp DESC", {"instance": instance_id, "since": since})

                instance_data = {
                    "instance_id": instance_id,
                    "is_online": bool(latest),
                    "last_update": latest['timestamp'] if latest else None,
                    # The names come from one list: the panel and the
                    # collector have to call the same thing by the same name,
                    # or the summary is empty while the table is full.
                    "current_metrics": {
                        field: get_latest_metric_value(instance_id, name) * scale
                        for field, (name, scale) in PANEL_METRICS.items()
                    } if latest else None
                }
                instances_data.append(instance_data)

            historical_data = {
                field: get_historical_metrics(name, PANEL_HISTORY_HOURS)
                for field, name in HISTORY_METRICS.items()
            }

            return SystemMetricsResponse(
                instances=instances_data,
                historical=historical_data
            )

        except Exception as e:
            logger.error(f"Error getting system metrics: {str(e)}")
            raise HTTPException(status_code=500, detail="Error retrieving system metrics")
    @app.get("/api/admin/metrics/history")
    def get_historical_metrics_endpoint(
            cpu_hours: int = Query(24, description="Hours of CPU metrics"),
            ram_hours: int = Query(24, description="Hours of RAM metrics"),
            admin: dict = Depends(get_current_admin)
    ):
        """Return the historical values of a metric."""
        try:
            return {
                "cpu": get_historical_metrics("cpu_usage", cpu_hours),
                "ram": get_historical_metrics("ram_usage", ram_hours)
            }
        except Exception as e:
            logger.error(f"Error getting historical metrics: {str(e)}")
            raise HTTPException(status_code=500, detail="Error retrieving historical metrics")
    @app.get("/api/admin/instances/{instance_id}")
    def get_instance_details(instance_id: str, admin: dict = Depends(get_current_admin)):
        """Return the details of one instance."""
        try:
            instance_info = {
                "instance_id": instance_id,
                "is_online": False,
                "current_metrics": {},
                "recent_events": [],
                "uptime": "Unknown"
            }

            latest_metric = DatabaseManagerV2.execute_one(
                "SELECT timestamp FROM system_metrics WHERE app_instance = :instance "
                "ORDER BY timestamp DESC LIMIT 1", {"instance": instance_id})

            if latest_metric:
                instance_info["is_online"] = True
                instance_info["last_update"] = latest_metric['timestamp']
                # The same names as the summary uses: one list for both
                # doors, or they will one day drift apart and the only one to
                # notice will be the person who sees one of them empty and the
                # other full.
                instance_info["current_metrics"] = {
                    field: get_latest_metric_value(instance_id, name) * scale
                    for field, (name, scale) in PANEL_METRICS.items()
                }

            return instance_info

        except Exception as e:
            logger.error(f"Error getting instance details: {str(e)}")
            raise HTTPException(status_code=500, detail="Error retrieving instance details")
    @app.post("/api/admin/instances/{instance_id}/restart")
    async def restart_instance(instance_id: str, admin: dict = Depends(get_current_admin)):
        """Restart one replica: the cluster's restart command, recorded with its author.

        This answered "sent" and did nothing but write a metric (keepup-67); the
        cluster registry is what actually carries a command to a replica, and it
        refuses one that cannot be carried out with the reason.
        """
        from keepup import cluster
        return await cluster.give_command(instance_id, {"action": cluster.ACTION_RESTART}, admin)
