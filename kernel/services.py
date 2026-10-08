"""The services plugins publish, and how they are handed to their consumers.

A plugin never imports another plugin. It publishes a service under a name the
kernel owns, and a consumer declares that name and asks for it while
initialising -- so what a capability is implemented by is a matter of what is
installed and enabled, not of what somebody imported.

Three things are deliberately not here: no container, no autowiring and no
scope. A service is an object under a name with a major version, and how the
provider built that object is its own business
(``doc/plugin_constructor.md`` sections 4.4 and 14).
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "DuplicateProvider",
    "Provider",
    "ServiceError",
    "ServiceRegistry",
    "UnsatisfiedRequirement",
]


class ServiceError(Exception):
    """Something about a service is wrong enough to stop the start."""


class UnsatisfiedRequirement(ServiceError):
    """A plugin needs a service that no provider offers at a high enough version."""


class DuplicateProvider(ServiceError):
    """Two enabled plugins publish the same required service."""


@dataclass
class Provider:
    """One plugin's answer to one service name.

    Attributes:
        name: the service name.
        version: the major version of the contract the provider implements.
        plugin_id: who publishes it, for the report.
        instance: the object, when it is ready.
        factory: how to build the object, for a lazy provider.
        lazy: whether the factory is called on the first requirement.
    """

    name: str
    version: int
    plugin_id: str
    instance: Any = None
    factory: Optional[Callable[[], Any]] = None
    lazy: bool = False


class ServiceRegistry:
    """What the plugins of one runtime publish, and what they ask for.

    The registry belongs to a runtime and not to the module: two applications in
    one process publish, require and report separately.
    """

    def __init__(self) -> None:
        self._providers: Dict[str, List[Provider]] = {}
        self._built: Dict[str, Any] = {}
        self._required: set = set()
        self._frozen = False

    # --- publishing ---------------------------------------------------------

    def provide(
        self,
        name: str,
        instance: Any = None,
        *,
        factory: Optional[Callable[[], Any]] = None,
        version: int = 1,
        lazy: bool = False,
        plugin_id: str = "",
    ) -> Provider:
        """Publish a service.

        Args:
            name: the service name, a noun the kernel owns.
            instance: a ready object; mutually exclusive with ``factory``.
            factory: how to build the object later.
            version: the major version of the contract.
            lazy: call the factory on the first requirement rather than now.
            plugin_id: who publishes it, for the report.

        Returns:
            The recorded provider.

        Raises:
            ServiceError: when the name is empty, or both an instance and a
                factory are given.
        """
        if self._frozen:
            raise ServiceError(f"{name}: services are frozen; {plugin_id} came too late")
        if not name:
            raise ServiceError("a service is published under a name")
        if instance is not None and factory is not None:
            raise ServiceError(f"{name}: publish an instance or a factory, not both")
        provider = Provider(
            name=name,
            version=int(version),
            plugin_id=plugin_id,
            instance=instance,
            factory=factory,
            lazy=bool(lazy and factory is not None),
        )
        providers = self._providers.setdefault(name, [])
        if providers:
            logger.warning(
                "%s: %s shadows %s", name, plugin_id or "?", providers[0].plugin_id or "?"
            )
        providers.append(provider)
        return provider

    def mark_required(self, name: str) -> None:
        """Remember that some enabled plugin cannot run without this service."""
        self._required.add(name)

    # --- consuming ----------------------------------------------------------

    def has(self, name: str, minimum: int = 0) -> bool:
        """Whether any provider offers this service at this version or higher.

        Args:
            name: the service name.
            minimum: the lowest acceptable major version.

        Returns:
            True when a provider satisfies the requirement.
        """
        return self._best(name, minimum) is not None

    def version(self, name: str) -> Optional[int]:
        """Return the highest published major version of a service, or None."""
        best = self._best(name, 0)
        return best.version if best is not None else None

    def require(self, name: str, minimum: int = 0) -> Any:
        """Hand a consumer the object behind a service name.

        Args:
            name: the service name.
            minimum: the lowest acceptable major version.

        Returns:
            The published object.

        Raises:
            UnsatisfiedRequirement: when no provider is new enough.
        """
        provider = self._best(name, minimum)
        if provider is None:
            published = self.version(name)
            detail = (
                f"published at version {published}" if published is not None else "not published"
            )
            raise UnsatisfiedRequirement(f"{name}>={minimum}: {detail}")
        return self._object(provider)

    def _object(self, provider: Provider) -> Any:
        """The object of a provider, building a lazy one on first use."""
        if provider.instance is not None:
            return provider.instance
        if provider.name in self._built:
            return self._built[provider.name]
        if provider.factory is None:
            raise UnsatisfiedRequirement(f"{provider.name}: no instance and no factory")
        built = provider.factory()
        self._built[provider.name] = built
        return built

    def _best(self, name: str, minimum: int) -> Optional[Provider]:
        """The newest provider of a name that satisfies a minimum version."""
        candidates = [p for p in self._providers.get(name, []) if p.version >= minimum]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.version)

    # --- lifecycle ----------------------------------------------------------

    def check_duplicates(self) -> None:
        """Refuse two providers of one required service.

        Raises:
            DuplicateProvider: when a service some enabled plugin requires is
                published by more than one plugin.
        """
        clashes = []
        for name in sorted(self._required):
            providers = self._providers.get(name, [])
            if len(providers) > 1:
                who = ", ".join(sorted(p.plugin_id or "?" for p in providers))
                clashes.append(f"{name} is published by {who}")
        if clashes:
            raise DuplicateProvider(
                "exactly one provider per required service: " + "; ".join(clashes)
            )

    def freeze(self) -> None:
        """Close the registry: nothing may publish after the start is over.

        Raises:
            DuplicateProvider: when a required service has more than one provider.
        """
        self.check_duplicates()
        self._frozen = True

    def close(self) -> None:
        """Stop what providers built for themselves, newest first.

        A provider that published an object with ``stop`` or ``close`` owns its
        shutdown; a provider that published a bare value has nothing to stop.
        """
        for name, instance in reversed(list(self._built.items())):
            stop = getattr(instance, "stop", None) or getattr(instance, "close", None)
            if not callable(stop):
                continue
            try:
                stop()
            except Exception as error:  # noqa: BLE001 - one provider must not stop the rest
                logger.error("%s: stopping the service failed: %s", name, error)
        for providers in self._providers.values():
            for provider in providers:
                stop = getattr(provider.instance, "stop", None)
                if callable(stop):
                    try:
                        stop()
                    except Exception as error:  # noqa: BLE001
                        logger.error("%s: stopping the service failed: %s", provider.name, error)
        self._built.clear()

    # --- reporting ----------------------------------------------------------

    def report(self) -> List[Dict[str, Any]]:
        """One row per published service, for the plugin report."""
        rows = []
        for name in sorted(self._providers):
            for provider in self._providers[name]:
                rows.append(
                    {
                        "service": name,
                        "version": provider.version,
                        "plugin_id": provider.plugin_id,
                        "required": name in self._required,
                        "lazy": provider.lazy,
                        "built": provider.instance is not None or name in self._built,
                    }
                )
        return rows
