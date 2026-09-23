"""Metrics of the process and the machine it runs on.

Two consumers, one collection: Prometheus scrapes `/metrics`, and the panel
reads a table of snapshots written by the same pass. They used to be collected
separately, which is how the table stayed empty while the scrape looked
healthy.

What the collector writes is declared here as names (`METRIC_GROUPS`,
`SNAPSHOT_METRIC_NAMES`), because a second reader needs them: the retention
sweep (`keepup/metrics_retention.py`) has to tell a measurement from another
module's row in the same table.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import psutil
from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response as FastAPIResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, REGISTRY, generate_latest
from pydantic import BaseModel

from keepup.auth.dependencies import get_current_admin
from keepup.db import DatabaseManager, DatabaseManagerV2
from keepup.instance import get_instance_id

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "SystemMetricsCollector",
    "metrics_collector",
    "register_metrics_routes",
    "update_metrics_background",
]

logger = logging.getLogger(__name__)

#: How often metrics are refreshed. Fifteen seconds is what the system lived
#: with and still lives with: that was hard-coded in the background loop. The
#: number is named here because there used to be two of them -- a configurable
#: `collection_interval` nobody read, and a constant in the loop that could not
#: be configured.
DEFAULT_INTERVAL_SECONDS = 15
#: How long to wait after an error: no hammering while the cause is still there.
ERROR_BACKOFF_SECONDS = 30

#: Exactly what the collector writes, by the groups metric exclusion works in
#: (`EXCLUDED_METRIC_GROUPS`). The list lives here rather than on the instance
#: because it now has two readers: the collector itself and the retention
#: sweep, which has to tell a measurement from another module's row in the same
#: table.
METRIC_GROUPS: Dict[str, List[str]] = {
    'cpu': [
        'cpu.percent.total', 'cpu.percent.avg_per_core',
        'cpu.cores.logical', 'cpu.cores.physical', 'process.cpu.percent'
    ],
    'ram': [
        'memory.total', 'memory.available', 'memory.used', 'memory.percent',
        'memory.free', 'swap.total', 'swap.used', 'swap.percent',
        'process.memory.rss', 'process.memory.vms'
    ],
    'disk': [
        'disk.total', 'disk.used', 'disk.free', 'disk.percent',
        'disk.read_bytes', 'disk.write_bytes'
    ],
    'network': ['network.bytes_sent', 'network.bytes_recv'],
    'process': ['process.threads.count', 'process.open_files'],
}

#: The names the collector writes, as one set -- this is what "a snapshot"
#: means to the retention sweep.
SNAPSHOT_METRIC_NAMES: Set[str] = {
    name for names in METRIC_GROUPS.values() for name in names
}


def _registry_names(metric_name: str) -> Set[str]:
    """The names a metric may already be registered under in Prometheus.

    Args:
        metric_name: the name the collector uses.

    Returns:
        Every name to look for, since Counter appends `_total` itself.
    """
    names = {metric_name}
    if metric_name.endswith("_total"):
        names.add(metric_name[: -len("_total")])
    return names


class SystemMetricsCollector:
    """Collects metrics of the process and of the machine.

    An ordinary object, not a singleton. The singleton solved one problem --
    "let there be one collector in the application" -- and solved it in a way
    that silently lost settings: a second call of the constructor returned the
    first instance and threw the arguments away. Asking for a thirty-second
    interval gave you sixty and said nothing about it.

    The application's shared collector is the module-level `metrics_collector`
    below; take it by name. Creating your own gives you your own object and
    does not disturb the shared one.
    """

    _registered_metrics = set()

    def __init__(self,
                 collection_interval: int = DEFAULT_INTERVAL_SECONDS,
                 excluded_metrics: Optional[List[str]] = None):
        self.collection_interval = collection_interval
        self.app_instance = os.getenv('APP_INSTANCE', 'main')
        self.excluded_metrics: Set[str] = set(excluded_metrics or [])

        # Its own copy of the list: excluding a metric edits these lists, and
        # one collector's edit must not reach the retention sweep or the
        # collector next to it.
        self.metric_groups = {group: list(names) for group, names in METRIC_GROUPS.items()}

        self.prometheus_metrics = self._init_prometheus_metrics()

        # The processor load of the pass being collected: read once and shared by
        # everything that wants it (see `_cpu_load`).
        self._cpu_load: Optional[Tuple[float, List[float]]] = None

        logger.info(f"Metrics collector initialized for instance: {self.app_instance}")
        logger.info(f"Excluded metrics: {self.excluded_metrics}")

    def _init_prometheus_metrics(self) -> Dict:
        """Initialise the Prometheus metrics, tolerating already-registered collectors."""
        metrics = {}

        metric_definitions = {
            'cpu_usage_percent': ('cpu_usage_percent', Gauge, 'Current CPU usage percentage', ['instance']),
            'cpu_usage_per_core_percent': (
            'cpu_usage_per_core_percent', Gauge, 'CPU usage percentage per core', ['instance', 'core']),
            'cpu_cores_logical': ('cpu_cores_logical', Gauge, 'Number of logical CPU cores', ['instance']),
            'cpu_cores_physical': ('cpu_cores_physical', Gauge, 'Number of physical CPU cores', ['instance']),
            'memory_total_bytes': ('memory_total_bytes', Gauge, 'Total memory in bytes', ['instance']),
            'memory_available_bytes': ('memory_available_bytes', Gauge, 'Available memory in bytes', ['instance']),
            'memory_used_bytes': ('memory_used_bytes', Gauge, 'Used memory in bytes', ['instance']),
            'memory_usage_percent': ('memory_usage_percent', Gauge, 'Memory usage percentage', ['instance']),
            'memory_free_bytes': ('memory_free_bytes', Gauge, 'Free memory in bytes', ['instance']),
            'swap_total_bytes': ('swap_total_bytes', Gauge, 'Total swap in bytes', ['instance']),
            'swap_used_bytes': ('swap_used_bytes', Gauge, 'Used swap in bytes', ['instance']),
            'swap_usage_percent': ('swap_usage_percent', Gauge, 'Swap usage percentage', ['instance']),
            'disk_total_bytes': ('disk_total_bytes', Gauge, 'Total disk space in bytes', ['instance']),
            'disk_used_bytes': ('disk_used_bytes', Gauge, 'Used disk space in bytes', ['instance']),
            'disk_free_bytes': ('disk_free_bytes', Gauge, 'Free disk space in bytes', ['instance']),
            'disk_usage_percent': ('disk_usage_percent', Gauge, 'Disk usage percentage', ['instance']),
            'disk_read_bytes_total': ('disk_read_bytes_total', Gauge, 'Total bytes read from disk', ['instance']),
            'disk_write_bytes_total': ('disk_write_bytes_total', Gauge, 'Total bytes written to disk', ['instance']),
            'network_bytes_sent_total': (
            'network_bytes_sent_total', Gauge, 'Total bytes sent over network', ['instance']),
            'network_bytes_received_total': (
            'network_bytes_received_total', Gauge, 'Total bytes received over network', ['instance']),
            'process_cpu_seconds_total': (
            'app_process_cpu_seconds_total', Gauge, 'Total CPU time spent by process', ['instance']),
            'process_memory_rss_bytes': (
            'process_memory_rss_bytes', Gauge, 'Resident memory size of process', ['instance']),
            'process_memory_vms_bytes': (
            'process_memory_vms_bytes', Gauge, 'Virtual memory size of process', ['instance']),
            'process_threads_count': ('process_threads_count', Gauge, 'Number of threads in process', ['instance']),
            'process_open_files_count': (
            'process_open_files_count', Gauge, 'Number of open files by process', ['instance']),
            'http_requests_total': (
            'http_requests_total', Counter, 'Total HTTP requests', ['instance', 'method', 'endpoint', 'status']),
            'http_request_duration_seconds': (
            'http_request_duration_seconds', Histogram, 'HTTP request duration in seconds',
            ['instance', 'method', 'endpoint']),
        }

        for key, (metric_name, metric_class, description, labels) in metric_definitions.items():
            try:
                existing_metric = None

                for collector in list(REGISTRY._collector_to_names.keys()):
                    # A counter is registered under the name without
                    # `_total`: Counter appends the suffix itself. Matching on
                    # the full name alone never found the counter that was
                    # already there, creating it a second time failed as a
                    # duplicate, and the fallback path registered a metric with
                    # a numeric suffix in its name -- rubbish in the registry.
                    if hasattr(collector, '_name') and collector._name in _registry_names(metric_name):
                        if isinstance(collector, metric_class):
                            existing_metric = collector
                            break
                        else:
                            # A different collector type under the same name (ProcessCollector instead of
                            # Gauge, say) is dropped from the registry so the right one can be created.
                            logger.warning(
                                f"Found existing collector {metric_name} of wrong type {type(collector)}, removing it")
                            try:
                                REGISTRY.unregister(collector)
                            except:
                                pass

                if existing_metric:
                    metrics[key] = existing_metric
                    logger.debug(f"Using existing metric: {metric_name}")
                else:
                    if metric_class == Gauge:
                        metrics[key] = Gauge(metric_name, description, labels)
                    elif metric_class == Counter:
                        metrics[key] = Counter(metric_name, description, labels)
                    elif metric_class == Histogram:
                        metrics[key] = Histogram(metric_name, description, labels)
                    logger.debug(f"Created new metric: {metric_name}")

            except Exception as e:
                logger.warning(f"Error registering metric {metric_name}: {str(e)}")
                # Fall back to a uniquely suffixed name rather than losing the metric entirely.
                try:
                    unique_name = f"{metric_name}_{id(self)}"
                    if metric_class == Gauge:
                        metrics[key] = Gauge(unique_name, description, labels)
                    elif metric_class == Counter:
                        metrics[key] = Counter(unique_name, description, labels)
                    elif metric_class == Histogram:
                        metrics[key] = Histogram(unique_name, description, labels)
                    logger.debug(f"Created metric with unique name: {unique_name}")
                except:
                    pass

        return metrics

    def exclude_group(self, group_name: str):
        if group_name in self.metric_groups:
            self.excluded_metrics.update(self.metric_groups[group_name])
            logger.info(f"Excluded metric group '{group_name}': {len(self.metric_groups[group_name])} metrics")

    def exclude_metric(self, metric_name: str):
        self.excluded_metrics.add(metric_name)

    def include_group(self, group_name: str):
        if group_name in self.metric_groups:
            for metric in self.metric_groups[group_name]:
                self.excluded_metrics.discard(metric)

    def should_collect(self, metric_name: str) -> bool:
        return metric_name not in self.excluded_metrics

    def sample_cpu(self) -> Tuple[float, List[float]]:
        """The processor load since the previous pass: total, and per core.

        `psutil.cpu_percent(interval=1)` does not measure for a second -- it
        *sleeps* for one, and a coroutine that sleeps like that stops the whole
        event loop with it. A pass asked four times over (here twice, in
        Prometheus twice more), so every collection interval the process stood
        still for four seconds: no requests answered, no sockets served, and, in
        an application with a game in it, every room's step loop stopped dead --
        measured as four-second gaps in rooms that know nothing of each other.

        With no interval psutil compares against its own previous reading, which
        is exactly what a loop that comes back every few seconds wants, and
        returns at once. The very first reading has nothing to compare against
        and is 0.0 by definition; that is one pass of a process's life.

        The reading is taken once and kept for the pass: two readings a
        microsecond apart would compare against each other and measure noise.
        """
        total = psutil.cpu_percent(interval=None)
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        # A machine with one core -- and a stand-in in a test -- answers with a number
        # rather than a list, and a list is what everything downstream averages.
        if not isinstance(per_core, (list, tuple)):
            per_core = [float(per_core or 0.0)]
        self._cpu_load = (total, list(per_core))
        return self._cpu_load

    def cpu_load(self) -> Tuple[float, List[float]]:
        """This pass's reading, taken if the pass has not taken one yet."""
        return self._cpu_load if self._cpu_load is not None else self.sample_cpu()

    def forget_cpu_load(self) -> None:
        """The pass is over: the next one reads the load again."""
        self._cpu_load = None

    async def collect_metrics(self) -> Dict[str, float]:
        try:
            metrics = {}
            total_cpu, per_core = self.sample_cpu()

            if self.should_collect('cpu.percent.total'):
                metrics['cpu.percent.total'] = total_cpu

            if self.should_collect('cpu.percent.avg_per_core'):
                # An average, not a list: the name promises an average, the
                # snapshot table holds a number, and a list failed the whole
                # batch insert -- together with twenty-four sound values. The
                # per-core breakdown goes to Prometheus, where it has a `core`
                # label of its own.
                if isinstance(per_core, (list, tuple)):
                    metrics['cpu.percent.avg_per_core'] = (
                        sum(per_core) / len(per_core) if per_core else 0.0)
                else:
                    # Not a list means it is already a single number.
                    metrics['cpu.percent.avg_per_core'] = float(per_core or 0.0)

            if self.should_collect('cpu.cores.logical'):
                metrics['cpu.cores.logical'] = psutil.cpu_count(logical=True)

            if self.should_collect('cpu.cores.physical'):
                metrics['cpu.cores.physical'] = psutil.cpu_count(logical=False)

            memory = psutil.virtual_memory()
            if self.should_collect('memory.total'):
                metrics['memory.total'] = memory.total
            if self.should_collect('memory.available'):
                metrics['memory.available'] = memory.available
            if self.should_collect('memory.used'):
                metrics['memory.used'] = memory.used
            if self.should_collect('memory.percent'):
                metrics['memory.percent'] = memory.percent
            if self.should_collect('memory.free'):
                metrics['memory.free'] = memory.free

            swap = psutil.swap_memory()
            if self.should_collect('swap.total'):
                metrics['swap.total'] = swap.total
            if self.should_collect('swap.used'):
                metrics['swap.used'] = swap.used
            if self.should_collect('swap.percent'):
                metrics['swap.percent'] = swap.percent

            disk_usage = psutil.disk_usage('/')
            disk_io = psutil.disk_io_counters()

            if self.should_collect('disk.total'):
                metrics['disk.total'] = disk_usage.total
            if self.should_collect('disk.used'):
                metrics['disk.used'] = disk_usage.used
            if self.should_collect('disk.free'):
                metrics['disk.free'] = disk_usage.free
            if self.should_collect('disk.percent'):
                metrics['disk.percent'] = disk_usage.percent
            if self.should_collect('disk.read_bytes') and disk_io:
                metrics['disk.read_bytes'] = disk_io.read_bytes
            if self.should_collect('disk.write_bytes') and disk_io:
                metrics['disk.write_bytes'] = disk_io.write_bytes

            net_io = psutil.net_io_counters()
            if self.should_collect('network.bytes_sent') and net_io:
                metrics['network.bytes_sent'] = net_io.bytes_sent
            if self.should_collect('network.bytes_recv') and net_io:
                metrics['network.bytes_recv'] = net_io.bytes_recv

            process = psutil.Process()
            process_memory = process.memory_info()

            if self.should_collect('process.cpu.percent'):
                metrics['process.cpu.percent'] = process.cpu_percent()
            if self.should_collect('process.memory.rss'):
                metrics['process.memory.rss'] = process_memory.rss
            if self.should_collect('process.memory.vms'):
                metrics['process.memory.vms'] = process_memory.vms
            if self.should_collect('process.threads.count'):
                metrics['process.threads.count'] = process.num_threads()
            if self.should_collect('process.open_files'):
                metrics['process.open_files'] = len(process.open_files()) if hasattr(process, 'open_files') else 0

            logger.debug(f"Collected {len(metrics)} metrics (excluded {len(self.excluded_metrics)})")
            return metrics

        except Exception as e:
            logger.error(f"Error collecting system metrics: {str(e)}")
            return {}

    def save_metrics_to_db(self, metrics: Dict[str, float]):
        """Write a snapshot -- one path for both engines.

        There used to be two: the `DatabaseManagerV2` pool on PostgreSQL, the
        legacy manager with its own connection on SQLite. A second connection
        to the same file ran into "database is locked", which meant that in
        development snapshots were not saved at all, and the message about it
        went into the log and was lost there.

        Named parameters are understood by both engines, so branching bought
        nothing here and cost a second way of doing one thing.

        Args:
            metrics: the values just collected, by metric name.
        """
        try:
            timestamp = datetime.utcnow()
            params_list = [{
                "metric_name": name,
                "metric_value": value,
                "timestamp": timestamp,
                "app_instance": self.app_instance,
            } for name, value in metrics.items()]

            if not params_list:
                return

            DatabaseManagerV2.execute_many(
                "INSERT INTO system_metrics (metric_name, metric_value, timestamp, "
                "app_instance) VALUES (:metric_name, :metric_value, :timestamp, "
                ":app_instance)", params_list)
            logger.debug(f"Saved {len(metrics)} system metrics to database")

        except Exception as e:
            # Metrics are a supporting thing: an application that fell over
            # because it could not record its own load is worse than an
            # application without metrics.
            logger.error(f"Error saving metrics to database: {str(e)}")

    def update_prometheus_metrics(self):
        try:
            # The pass's own reading, not two more seconds of a standing process.
            total_cpu, cpu_per_core = self.cpu_load()
            self.prometheus_metrics['cpu_usage_percent'].labels(instance=self.app_instance).set(
                total_cpu
            )

            for i, value in enumerate(cpu_per_core):
                self.prometheus_metrics['cpu_usage_per_core_percent'].labels(
                    instance=self.app_instance, core=str(i)
                ).set(value)

            self.prometheus_metrics['cpu_cores_logical'].labels(instance=self.app_instance).set(
                psutil.cpu_count(logical=True)
            )
            self.prometheus_metrics['cpu_cores_physical'].labels(instance=self.app_instance).set(
                psutil.cpu_count(logical=False)
            )

            memory = psutil.virtual_memory()
            self.prometheus_metrics['memory_total_bytes'].labels(instance=self.app_instance).set(memory.total)
            self.prometheus_metrics['memory_available_bytes'].labels(instance=self.app_instance).set(memory.available)
            self.prometheus_metrics['memory_used_bytes'].labels(instance=self.app_instance).set(memory.used)
            self.prometheus_metrics['memory_usage_percent'].labels(instance=self.app_instance).set(memory.percent)
            self.prometheus_metrics['memory_free_bytes'].labels(instance=self.app_instance).set(memory.free)

            swap = psutil.swap_memory()
            self.prometheus_metrics['swap_total_bytes'].labels(instance=self.app_instance).set(swap.total)
            self.prometheus_metrics['swap_used_bytes'].labels(instance=self.app_instance).set(swap.used)
            self.prometheus_metrics['swap_usage_percent'].labels(instance=self.app_instance).set(swap.percent)

            disk_usage = psutil.disk_usage('/')
            self.prometheus_metrics['disk_total_bytes'].labels(instance=self.app_instance).set(disk_usage.total)
            self.prometheus_metrics['disk_used_bytes'].labels(instance=self.app_instance).set(disk_usage.used)
            self.prometheus_metrics['disk_free_bytes'].labels(instance=self.app_instance).set(disk_usage.free)
            self.prometheus_metrics['disk_usage_percent'].labels(instance=self.app_instance).set(disk_usage.percent)

            disk_io = psutil.disk_io_counters()
            if disk_io:
                self.prometheus_metrics['disk_read_bytes_total'].labels(instance=self.app_instance).set(
                    disk_io.read_bytes)
                self.prometheus_metrics['disk_write_bytes_total'].labels(instance=self.app_instance).set(
                    disk_io.write_bytes)

            net_io = psutil.net_io_counters()
            if net_io:
                self.prometheus_metrics['network_bytes_sent_total'].labels(instance=self.app_instance).set(
                    net_io.bytes_sent)
                self.prometheus_metrics['network_bytes_received_total'].labels(instance=self.app_instance).set(
                    net_io.bytes_recv)

            process = psutil.Process()
            self.prometheus_metrics['process_cpu_seconds_total'].labels(instance=self.app_instance).set(
                process.cpu_percent() / 100
            )

            process_memory = process.memory_info()
            self.prometheus_metrics['process_memory_rss_bytes'].labels(instance=self.app_instance).set(
                process_memory.rss)
            self.prometheus_metrics['process_memory_vms_bytes'].labels(instance=self.app_instance).set(
                process_memory.vms)
            self.prometheus_metrics['process_threads_count'].labels(instance=self.app_instance).set(
                process.num_threads())

            try:
                self.prometheus_metrics['process_open_files_count'].labels(instance=self.app_instance).set(
                    len(process.open_files())
                )
            except:
                self.prometheus_metrics['process_open_files_count'].labels(instance=self.app_instance).set(0)

            logger.debug(f"Prometheus metrics updated for instance {self.app_instance}")

        except Exception as e:
            logger.error(f"Error updating Prometheus metrics: {str(e)}")


    def get_stats(self) -> Dict:
        return {
            'collection_interval': self.collection_interval,
            'app_instance': self.app_instance,
            'excluded_metrics_count': len(self.excluded_metrics),
            'excluded_metrics': list(self.excluded_metrics)
        }


metrics_collector = SystemMetricsCollector()


def configure_metrics_collector_from_env(collector: Optional[SystemMetricsCollector] = None):
    """Apply the environment to a collector. Repeatable, no module reload.

    Configuration by call rather than as a side effect of import: reloading the
    module to reconfigure it rebuilds the classes and discards whatever was
    already taken from it -- patches in tests, references to the previous
    instance, all at once.

    Args:
        collector: the collector to configure; the shared one when omitted.
    """
    collector = collector if collector is not None else metrics_collector

    excluded_env = os.getenv('EXCLUDED_METRICS', '')
    if excluded_env:
        excluded_list = [m.strip() for m in excluded_env.split(',')]
        collector.excluded_metrics.update(excluded_list)

    excluded_groups_env = os.getenv('EXCLUDED_METRIC_GROUPS', '')
    if excluded_groups_env:
        groups = [g.strip() for g in excluded_groups_env.split(',')]
        for group in groups:
            if group in collector.metric_groups:
                collector.exclude_group(group)

    interval = os.getenv('METRICS_COLLECTION_INTERVAL')
    if interval and interval.isdigit():
        collector.collection_interval = int(interval)

    return collector


configure_metrics_collector_from_env()


async def update_metrics_background(collector: Optional[SystemMetricsCollector] = None):
    """Collect metrics for as long as the application lives: read, store, publish.

    One pass and one interval. Collection used to be spread over three places:
    this loop updated Prometheus only, writing to the database was done by a
    method nobody called, and what did write to it was a disabled plugin using
    names of its own. The upshot was an empty snapshot table and an empty
    metrics section in a panel served by a healthy process.

    The interval comes from the collector: two loops with two intervals are two
    places where collection can stop unnoticed.

    Args:
        collector: the collector to run; the shared one when omitted.
    """
    collector = collector if collector is not None else metrics_collector
    while True:
        try:
            metrics = await collector.collect_metrics()
            collector.save_metrics_to_db(metrics)
            collector.update_prometheus_metrics()
            collector.forget_cpu_load()
            await asyncio.sleep(collector.collection_interval)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error in metrics background update: {str(e)}")
            await asyncio.sleep(ERROR_BACKOFF_SECONDS)


# --- HTTP surface -------------------------------------------------------------
#
# Prometheus scrapes /metrics; the admin panel reads the per-instance figures
# this module writes to system_metrics, which is how one replica can show the
# load of all of them.


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
    async def get_system_metrics(admin: dict = Depends(get_current_admin)):
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
    async def get_historical_metrics_endpoint(
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
    async def get_instance_details(instance_id: str, admin: dict = Depends(get_current_admin)):
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
        """TODO: restart an instance -- not actually implemented."""
        logger.info(f"Restart requested for instance: {instance_id} by admin: {admin['username']}")

        DatabaseManager.execute_commit_only('''
        INSERT INTO system_metrics (metric_name, metric_value, app_instance, tags)
        VALUES (?, ?, ?, ?)
        ''', (
            "instance_restart",
            1,
            get_instance_id(),
            f"target:{instance_id},admin:{admin['username']}"
        ))

        return {"success": True, "message": f"Restart command sent for instance {instance_id}"}
