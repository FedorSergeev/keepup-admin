"""Calls this application makes to outside systems, and the section that shows them.

This distribution provides `integration_log` and declares one plugin,
`integration_logs`, in the `keepup.plugins` group. It depends on `keepup-admin`
for the constructor -- the descriptor, the catalogue, the service registry and
the lifecycle -- and on nothing of another capability.

The plugin moved here from the framework's `builtin/integration_logs.py` in
keepup-121: the base no longer carries a capability's routes.
"""

__version__ = "0.6.0"


_PLUGIN_NAMES = {"Integration_logsPlugin", "SERVICE_INTEGRATION_LOG"}


def __getattr__(name):
    if name in _PLUGIN_NAMES:
        from keepup_integration_log import plugin

        return getattr(plugin, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
