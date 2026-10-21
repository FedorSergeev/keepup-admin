"""The replicas of this deployment: their state, their commands and their panel.

This distribution provides `cluster` and declares one plugin, `cluster`, in the
`keepup.plugins` group. It depends on `keepup-admin` for the constructor, on
`keepup-db` for the two tables the registry keeps, and on `keepup-auth` for the
panel guard of its routes -- the temporary edge the route bridge carries until
`keepup-http` mounts what a plugin declares (keepup-121).

What stays in the kernel: `keepup.instance` (which replica this process is) and
`keepup.notification_bus` (how replicas reach each other). They are facts about
the process, not capabilities, and the kernel's own pieces need them.
"""

from keepup_cluster.cluster import (
    ACTION_RESTART,
    ACTION_START,
    ACTION_STOP,
    STATE_GONE,
    STATE_SERVING,
    STATE_STOPPED,
    ReplicaController,
    current_state,
    get_controller,
    set_controller,
)

__version__ = "0.6.0"

__all__ = [
    "ACTION_RESTART",
    "ACTION_START",
    "ACTION_STOP",
    "STATE_GONE",
    "STATE_SERVING",
    "STATE_STOPPED",
    "ReplicaController",
    "current_state",
    "get_controller",
    "set_controller",
]

_PLUGIN_NAMES = {"ClusterPlugin", "SERVICE_CLUSTER"}


def __getattr__(name):
    if name in _PLUGIN_NAMES:
        from keepup_cluster import plugin

        return getattr(plugin, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
