"""The transport contract: a way into the application that is not a route.

A transport is a plugin of kind ``transport``, and what it contributes is a
server rather than a route. It is handed the runtime, finds the routes the other
plugins declared -- as data, through ``keepup.kernel.call`` -- and answers in its
own vocabulary: HTTP status codes for HTTP, a status for gRPC, a line for
whatever comes next.

The kernel never learns which one is running, and a deployment that enables none
is a worker on purpose rather than a mistake
(``doc/plugin_constructor.md`` section 5).
"""

import asyncio
import logging
from typing import Any, Dict, List, Sequence, Tuple

from keepup.kernel.call import RouteSpec
from keepup.plugins.base import BasePlugin

logger = logging.getLogger(__name__)

__all__ = [
    "TransportPlugin",
    "route_specs_of",
    "serve_all",
    "transports_of",
]


class TransportPlugin(BasePlugin):
    """A way into the application: HTTP, a socket, a command line.

    A transport declares no routes of its own and answers with whatever its
    protocol answers with. Everything between the wire and a handler -- the
    mask, the caller, the body, the right -- is :func:`keepup.kernel.call.invoke`.
    """

    async def initialize(self) -> bool:
        """A transport has nothing to prepare before it serves; override to.

        Returns:
            True: a transport that cannot come up says so by raising, not by
            answering false here -- what it cannot do is discovered when it
            tries to open its socket.
        """
        return True

    def get_api_routes(self) -> List[Dict]:
        """A transport contributes no routes: it carries them."""
        return []

    def get_websocket_routes(self) -> List[Dict]:
        """A transport contributes no sockets of its own."""
        return []

    def get_handlers(self) -> Dict[str, Any]:
        """A transport offers other plugins nothing but a way in."""
        return {}

    async def serve(self, runtime: Any) -> None:
        """Serve until stopped.

        Args:
            runtime: the running kernel -- its ``route_specs()`` are the routes
                every plugin declared, and ``stop()`` will be called after this
                returns.

        Raises:
            NotImplementedError: a transport that does not serve is not one.
        """
        raise NotImplementedError(f"{self.name} does not serve anything")


def transports_of(runtime: Any) -> List[Tuple[str, Any]]:
    """The initialised transport plugins of a runtime, in start order.

    Args:
        runtime: the running kernel.

    Returns:
        ``(plugin_id, plugin)`` pairs.
    """
    found = []
    for plugin_id, plugin in runtime.loaded_plugins.items():
        descriptor = getattr(plugin, "descriptor", None)
        if descriptor is not None and descriptor.is_transport:
            found.append((plugin_id, plugin))
    return found


def route_specs_of(runtime: Any) -> List[RouteSpec]:
    """Every route the plugins declared, as transport-neutral specifications.

    Args:
        runtime: the running kernel, with its contributions collected.

    Returns:
        The specifications, in the order the plugins initialised.
    """
    specs = []
    for route in runtime.contributions.of("routes"):
        specs.append(RouteSpec.of(route, plugin_id=str(route.get("_plugin_id") or "")))
    return specs


async def serve_all(runtime: Any, transports: Sequence[Tuple[str, Any]] = None) -> None:
    """Serve on every transport at once, and return when they have all stopped.

    Args:
        runtime: the running kernel.
        transports: the transports to serve on; the runtime's own when None.
    """
    pairs = list(transports if transports is not None else transports_of(runtime))
    if not pairs:
        logger.info("No transport is enabled: this deployment serves nothing")
        return
    logger.info("Serving on: %s", ", ".join(plugin_id for plugin_id, _ in pairs))
    await asyncio.gather(*(plugin.serve(runtime) for _, plugin in pairs))
