"""The way into the application over HTTP (keepup-121).

This distribution carries what the kernel does not need: the ASGI application,
the routes, the middleware, the panel shell and the documentation pages. It
declares one plugin, `http`, of the transport kind, and depends on `keepup-admin`
for the constructor it is loaded by -- the base itself imports no web framework.

The modules here were the base's `api_docs.py`, `events_api.py`, `web.py` and the
route wrapper; they move in one slice at a time, and the base answers their old
names through the compatibility layer for one release (keepup-122).
"""

__version__ = "0.5.0"

__all__ = ["HttpPlugin", "SERVICE_HTTP", "register_api_documentation",
           "register_event_api_routes"]

#: What a caller asks for to reach the application the transport builds.
SERVICE_HTTP = "http"

_FROM_PLUGIN = {"HttpPlugin", "SERVICE_HTTP"}
_FROM_MODULES = {
    "register_api_documentation": "api_docs",
    "register_event_api_routes": "events_api",
}


def __getattr__(name):
    if name in _FROM_PLUGIN:
        from keepup_http import plugin

        return getattr(plugin, name)
    if name in _FROM_MODULES:
        import importlib

        module = importlib.import_module(f"keepup_http.{_FROM_MODULES[name]}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
