"""The HTTP transport as a plugin: the application and how it is served (keepup-121).

The kernel resolves the catalogue, runs the lifecycle, collects what plugins
contribute and offers a seam for a transport. This capability is the transport
for HTTP: it builds the ASGI application, mounts the routes, sockets and
middleware the other plugins declare, serves the panel shell and answers
`/metrics`.

It is being filled in one slice at a time. Today it carries the documentation
pages and the event log's routes; the application assembly, the panel shell and
the route wrapper still live in the base and are reached through the
compatibility layer (keepup-122), which is what this plugin replaces.
"""

import logging
from typing import Any, Dict

from keepup.kernel.descriptor import KIND_TRANSPORT, PluginDescriptor
from keepup.kernel.transports import TransportPlugin

logger = logging.getLogger(__name__)

__all__ = ["HttpPlugin", "SERVICE_HTTP"]

#: What another plugin asks for to reach the application this transport built.
SERVICE_HTTP = "http"


class HttpPlugin(TransportPlugin):
    """The application, and the way in."""

    descriptor = PluginDescriptor(
        id="http",
        name="HTTP transport",
        version="0.5.0",
        distribution="keepup-http",
        kind=KIND_TRANSPORT,
        priority=5,
        provides=(f"{SERVICE_HTTP}>=1",),
        contributions=("routes", "sockets", "middleware", "transport"),
    )

    def register(self, services):
        """Offer the application factory to whoever asks for it."""
        services.provide(SERVICE_HTTP, self, version=1, plugin_id="http")

    def get_handlers(self) -> Dict[str, Any]:
        """What another plugin may call: the documentation and event registrars."""
        from keepup_http.api_docs import register_api_documentation
        from keepup_http.events_api import register_event_api_routes

        return {"register_api_documentation": register_api_documentation,
                "register_event_api_routes": register_event_api_routes}
