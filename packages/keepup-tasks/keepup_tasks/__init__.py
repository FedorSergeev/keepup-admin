"""Background tasks: the one scheduler, and the locks the work runs under.

This distribution provides `scheduler` and `locks` and declares one plugin,
`tasks`, in the `keepup.plugins` group. It depends on `keepup-admin` for the
constructor, on `keepup-auth` for the panel guard of its routes -- the temporary
edge the route bridge carries until `keepup-http` mounts what a plugin declares
(keepup-121) -- and on `keepup-db`, because the locks it offers write to a table.
"""

from keepup_tasks.scheduler import (
    JOB_DEFAULTS,
    get_scheduler,
    init_scheduler,
    new_scheduler,
)

__version__ = "0.4.0"

__all__ = ["JOB_DEFAULTS", "get_scheduler", "init_scheduler", "new_scheduler"]

#: What the entry point asks for, looked up on access: a deployment that only
#: wants the scheduler does not pay for the plugin's panel wiring.
_PLUGIN_NAMES = {"TasksPlugin", "SERVICE_SCHEDULER", "SERVICE_LOCKS"}


def __getattr__(name):
    if name in _PLUGIN_NAMES:
        from keepup_tasks import plugin

        return getattr(plugin, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
