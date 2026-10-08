"""The panel: the shell, its sections, its themes and the static files it serves.

This distribution provides `ui` and declares one plugin,
`ui`, in the `keepup.plugins` group. It depends on `keepup-admin`
for the constructor -- the descriptor, the catalogue, the service registry and
the lifecycle -- and on nothing of another capability.

The code moves here in keepup-124: until then the plugin lives in the framework's
own `builtin/ui.py` and this package is the home it is moving to.
"""

__version__ = "0.4.0"
