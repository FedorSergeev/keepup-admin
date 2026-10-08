"""SQLite behind the abstraction, and the two things it cannot do: RETURNING and messages between replicas.

This distribution provides `datasource_driver` and declares one plugin,
`sqlite`, in the `keepup.plugins` group. It depends on `keepup-admin`
for the constructor -- the descriptor, the catalogue, the service registry and
the lifecycle -- and on nothing of another capability.

The code moves here in keepup-124: until then the plugin lives in the framework's
own `builtin/sqlite.py` and this package is the home it is moving to.
"""

__version__ = "0.4.0"
