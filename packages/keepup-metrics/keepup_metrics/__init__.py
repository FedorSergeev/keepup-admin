"""System metrics: the collector, the scrape, the panel view and the snapshots.

This distribution provides `metrics` and declares one plugin, `metrics`, in the
`keepup.plugins` group. It depends on `keepup-admin` for the constructor -- the
descriptor, the catalogue, the service registry and the lifecycle -- on
`keepup-db` for the table it writes, and on `keepup-auth` for the panel's guard.

The collector is what this module answers with, and it is deliberately light:
importing it must not import the API, the web framework or the sign-in
(keepup-22). The plugin -- which needs all three -- is `keepup_metrics.plugin`,
which is where the entry point points.
"""

from keepup_metrics.collector import (
    METRIC_GROUPS,
    SNAPSHOT_METRIC_NAMES,
    SystemMetricsCollector,
    metrics_collector,
    update_metrics_background,
)

__version__ = "0.6.0"

__all__ = [
    "METRIC_GROUPS",
    "SNAPSHOT_METRIC_NAMES",
    "SystemMetricsCollector",
    "metrics_collector",
    "update_metrics_background",
]

#: Names that left the collector for the API (keepup-22). They are answered here
#: for code written against 0.1 -- and looked up on access, so importing the
#: collector does not import the API, which is the split the module exists for.
#: What the entry point asks for. Looked up on access, so the light half of the
#: distribution stays light (keepup-22) and the plugin's own imports -- the web
#: framework, the panel's guard -- are paid for by a deployment that enables it.
_PLUGIN_NAMES = {"MetricsPlugin", "MetricsService", "SERVICE_METRICS"}


def __getattr__(name):
    if name in _PLUGIN_NAMES:
        from keepup_metrics import plugin

        return getattr(plugin, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
