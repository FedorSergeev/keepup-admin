"""Calls this application makes to outside systems, and the section that shows them.

This distribution provides `integration_log` and declares one plugin,
`integration_logs`, in the `keepup.plugins` group. It depends on `keepup-admin`
for the constructor -- the descriptor, the catalogue, the service registry and
the lifecycle -- and on nothing of another capability.

The code moves here in keepup-124: until then the plugin lives in the framework's
own `builtin/integration_logs.py` and this package is the home it is moving to.
"""

__version__ = "0.4.0"
