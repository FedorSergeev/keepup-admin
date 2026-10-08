"""Incoming calls, the application's events, the administrator's trail and their retention.

This distribution provides `audit, events` and declares one plugin,
`audit`, in the `keepup.plugins` group. It depends on `keepup-admin`
for the constructor -- the descriptor, the catalogue, the service registry and
the lifecycle -- and on nothing of another capability.

The code moves here in keepup-124: until then the plugin lives in the framework's
own `builtin/audit.py` and this package is the home it is moving to.
"""

__version__ = "0.4.0"
