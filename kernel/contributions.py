"""What plugins contribute, and the one consumer each kind has.

A plugin declares routes, sockets, panel sections, tables, jobs, event sinks,
metric collectors, middleware, a transport, settings defaults and the rights its
routes may ask for. Each kind has exactly one consumer, and the list is closed:
adding a kind is a deliberate change to the specification, not a new convention
(``doc/plugin_constructor.md`` section 4.3).

Collection is deliberately tolerant and deliberately loud in two different
places: a getter that raises is recorded against its plugin and the rest of the
plugins still contribute, while a *declaration* the route runtime refuses -- a
mask naming a parameter the handler does not take -- is raised by that runtime
where it belongs, at registration, and stops the start. A plugin that silently
lost half its routes is the failure this replaces.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Tuple

from keepup.kernel.descriptor import KIND_TRANSPORT

logger = logging.getLogger(__name__)

__all__ = [
    "CONTRIBUTION_KINDS",
    "DEFAULT_MIDDLEWARE_ORDER",
    "Contributions",
    "MiddlewareSpec",
    "collect",
    "mount",
]

#: The closed list of things a plugin may contribute.
CONTRIBUTION_KINDS = (
    "routes",
    "sockets",
    "sections",
    "tables",
    "jobs",
    "events",
    "metrics",
    "middleware",
    "transport",
    "settings",
    "permissions",
)

#: The call each kind is collected with. ``transport`` is not a call: it is what
#: a plugin of that kind *is*.
GETTERS = {
    "routes": "get_api_routes",
    "sockets": "get_websocket_routes",
    "sections": "get_panel_sections",
    "tables": "get_declared_tables",
    "jobs": "get_scheduled_jobs",
    "events": "get_event_sinks",
    "metrics": "get_metric_collectors",
    "middleware": "get_middleware",
    "settings": "get_settings_defaults",
    "permissions": "get_route_permissions",
}

#: Kinds whose contribution is a mapping, merged later-wins.
MAPPING_KINDS = ("settings", "permissions")

#: Where a middleware contribution lands when it does not say. Closest to the
#: routes: a layer that must see the registered path, or the body, declares a
#: lower order (section 5.2 of the specification).
DEFAULT_MIDDLEWARE_ORDER = 100


@dataclass(frozen=True)
class MiddlewareSpec:
    """One ASGI layer a plugin adds to the HTTP transport.

    Attributes:
        order: lower is further out; ties keep the order the plugins started in.
        factory: the ASGI middleware class or factory.
        plugin_id: who contributed it, for the report.
        name: what to call it in a log line.
    """

    order: int
    factory: Any
    plugin_id: str = ""
    name: str = ""

    @classmethod
    def of(cls, value: Any, plugin_id: str = "") -> "MiddlewareSpec":
        """Read a middleware contribution in any of its three shapes.

        Args:
            value: a :class:`MiddlewareSpec`, an ``(order, factory)`` pair, or
                the middleware itself.
            plugin_id: who contributed it.

        Returns:
            The specification.

        Raises:
            ValueError: when the contribution is none of the three.
        """
        if isinstance(value, MiddlewareSpec):
            # A plugin that built its own spec may not have named itself; the
            # collector knows who it is asking.
            if value.plugin_id or not plugin_id:
                return value
            return cls(value.order, value.factory, plugin_id, value.name or _name_of(value.factory))
        if isinstance(value, (tuple, list)) and len(value) == 2:
            order, factory = value
            return cls(int(order), factory, plugin_id, _name_of(factory))
        if callable(value):
            return cls(DEFAULT_MIDDLEWARE_ORDER, value, plugin_id, _name_of(value))
        raise ValueError(f"{plugin_id}: middleware is a spec, an (order, factory) pair, or a class")


def _name_of(factory: Any) -> str:
    """What to call a middleware contribution in a log line."""
    return str(getattr(factory, "__name__", None) or type(factory).__name__)


@dataclass
class Contributions:
    """Everything the running plugins contribute, by kind.

    Attributes:
        by_kind: the contributions of each kind, in the order the plugins
            initialised in.
        errors: ``(plugin_id, kind, reason)`` for a plugin whose getter raised.
    """

    by_kind: Dict[str, List[Any]] = field(default_factory=lambda: {kind: [] for kind in CONTRIBUTION_KINDS})
    errors: List[Tuple[str, str, str]] = field(default_factory=list)

    def of(self, kind: str) -> List[Any]:
        """The contributions of one kind.

        Args:
            kind: a name from :data:`CONTRIBUTION_KINDS`.

        Returns:
            The list, empty when nothing contributed.
        """
        return list(self.by_kind.get(kind, []))

    def middleware_in_order(self) -> List[MiddlewareSpec]:
        """The middleware contributions, outermost first.

        Returns:
            The specs, sorted by order; equal orders keep registration order.
        """
        return sorted((MiddlewareSpec.of(item) for item in self.by_kind["middleware"]),
                      key=lambda spec: spec.order)

    def merged(self, kind: str) -> Dict[str, Any]:
        """The mapping kinds, merged in the order the plugins initialised.

        Args:
            kind: ``settings`` or ``permissions``.

        Returns:
            One mapping; a key two plugins both declare is taken from the later
            one and the clash is logged, because a silently overwritten default
            is a deployment nobody can read.
        """
        merged: Dict[str, Any] = {}
        for item in self.by_kind.get(kind, []):
            for key, value in (item.get("values") or {}).items():
                if key in merged:
                    logger.warning(
                        "%s: %s declares %s, already declared by %s",
                        kind, item.get("plugin_id"), key, merged[key]["plugin_id"],
                    )
                merged[key] = {"plugin_id": item.get("plugin_id"), "value": value}
        return {key: entry["value"] for key, entry in merged.items()}

    def counts(self) -> Dict[str, int]:
        """How many contributions of each kind there are."""
        return {kind: len(items) for kind, items in self.by_kind.items() if items}

    def report(self) -> List[Dict[str, Any]]:
        """One row per contributing plugin, for the plugin report."""
        rows: Dict[str, Dict[str, Any]] = {}
        for kind, items in self.by_kind.items():
            for item in items:
                plugin_id = getattr(item, "plugin_id", None) or _plugin_of(item)
                row = rows.setdefault(str(plugin_id), {"plugin_id": plugin_id, "counts": {}})
                row["counts"][kind] = row["counts"].get(kind, 0) + 1
        for plugin_id, kind, reason in self.errors:
            row = rows.setdefault(plugin_id, {"plugin_id": plugin_id, "counts": {}})
            row.setdefault("errors", []).append({"kind": kind, "reason": reason})
        return [rows[key] for key in sorted(rows)]


def _plugin_of(item: Any) -> str:
    """Who contributed a mapping entry, when the entry does not say."""
    if isinstance(item, Mapping):
        return str(item.get("plugin_id") or item.get("id") or "?")
    return "?"


def collect(runtime: Any) -> Contributions:
    """Collect what the initialised plugins of a runtime contribute.

    Args:
        runtime: the running :class:`keepup.kernel.Runtime`.

    Returns:
        The contributions, with the plugins whose getter raised recorded against
        them rather than taking the start down.
    """
    contributions = Contributions()
    for plugin_id, plugin in runtime.loaded_plugins.items():
        descriptor = getattr(plugin, "descriptor", None)
        if descriptor is not None and getattr(descriptor, "kind", "") == KIND_TRANSPORT:
            contributions.by_kind["transport"].append({"plugin_id": plugin_id, "plugin": plugin})
        for kind, getter_name in GETTERS.items():
            getter = getattr(plugin, getter_name, None)
            if not callable(getter):
                continue
            try:
                value = getter()
            except Exception as error:  # noqa: BLE001 - one plugin must not stop the rest
                reason = f"{type(error).__name__}: {error}"
                contributions.errors.append((plugin_id, kind, reason))
                logger.error("Plugin %s failed to declare its %s: %s", plugin_id, kind, reason)
                continue
            _record(contributions, kind, plugin_id, value)
    return contributions


def _record(contributions: Contributions, kind: str, plugin_id: str, value: Any) -> None:
    """Put one plugin's contribution of one kind into the collection."""
    if value is None:
        return
    if kind in MAPPING_KINDS:
        if not isinstance(value, Mapping):
            raise ValueError(f"{plugin_id}: {kind} is a mapping")
        contributions.by_kind[kind].append({"plugin_id": plugin_id, "values": dict(value)})
        return
    if isinstance(value, Mapping):
        value = [value]
    for item in value:
        contributions.by_kind[kind].append(_tagged(item, plugin_id, kind))


def _tagged(item: Any, plugin_id: str, kind: str) -> Any:
    """Remember who contributed a route or a section, for the report."""
    if isinstance(item, dict):
        return {**item, "_plugin_id": item.get("_plugin_id") or plugin_id}
    if kind == "middleware":
        return MiddlewareSpec.of(item, plugin_id)
    return item


def mount(contributions: Contributions, registrars: Mapping[str, Callable], runtime: Any = None) -> Dict[str, int]:
    """Hand each kind to its one consumer.

    Args:
        contributions: what the plugins contributed.
        registrars: a callable per kind; the transport supplies the HTTP ones.
        runtime: passed to a registrar that wants the runtime itself.

    Returns:
        How many contributions of each kind were mounted.

    Raises:
        Exception: whatever a registrar raises -- a declaration the consumer
            refuses (a bad mask, a permission on a public route) is a mistake
            found at the start, not a route quietly missing later.
    """
    mounted: Dict[str, int] = {}
    for kind, registrar in registrars.items():
        if kind not in CONTRIBUTION_KINDS:
            raise ValueError(f"{kind} is not a contribution kind")
        items = contributions.of(kind)
        if not items:
            continue
        registrar(items, runtime)
        mounted[kind] = len(items)
    return mounted
