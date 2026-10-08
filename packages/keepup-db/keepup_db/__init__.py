"""The abstraction that holds SQLAlchemy: the table language, the pool, the dialect registry and the creation of declared tables.

This distribution provides `datasource` and declares one plugin,
`db`, in the `keepup.plugins` group. It depends on `keepup-admin`
for the constructor -- the descriptor, the catalogue, the service registry and
the lifecycle -- and on nothing of another capability.

The code moves here in keepup-124: until then the plugin lives in the framework's
own `builtin/db.py` and this package is the home it is moving to.
"""

__version__ = "0.4.0"
