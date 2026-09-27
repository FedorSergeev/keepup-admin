"""Small per-replica caches for what every page asks the database for.

The active theme is read on every page load and the panel's section catalogue
on every page of the panel, yet both change only when an administrator changes
them -- a few times in the life of a deployment. So each replica keeps them in
memory:

- **A lifetime bounds staleness.** An entry is read again after
  ``DEFAULT_TTL_SECONDS`` (``KeepupSettings.performance.catalogue_cache_seconds``).
  That is the guarantee, and the only one that needs nothing else to work:
  whatever happens to the other mechanisms, a change reaches every replica
  within that time.
- **The replica that changes something drops the entry at once**, so the
  administrator who just activated a theme sees it on the next page.
- **The others are told** over the replicas' notification bus, when the
  application runs one (``keepup.notification_bus``): they drop the entry on
  receipt. Delivery there is best-effort by design -- a replica that missed the
  message catches up when the entry expires.

Loading runs outside the cache's lock: two threads that miss at the same moment
may both read the database, which costs one extra query and never a stale value.
"""

import asyncio
import logging
import threading
import time
from typing import Any, Callable, Dict, Hashable, Optional

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "DEFAULT_TTL_SECONDS",
    "attach_to_bus",
    "ENVELOPE_KIND",
    "ReplicaCache",
    "get_cache",
    "invalidate_all",
    "invalidate_everywhere",
    "on_envelope",
    "set_ttl",
]

logger = logging.getLogger(__name__)

#: How long an entry lives when the application says nothing.
DEFAULT_TTL_SECONDS = 30.0

#: The notification bus envelope that tells the other replicas to drop a cache.
ENVELOPE_KIND = "keepup.cache.invalidate"

_MISSING = object()


class ReplicaCache:
    """Values by key, each good for ``ttl`` seconds in this process."""

    def __init__(self, name: str, ttl: float = DEFAULT_TTL_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self.name = name
        self.ttl = ttl
        self._clock = clock
        self._entries: Dict[Hashable, tuple] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable, load: Callable[[], Any]) -> Any:
        """The cached value, or what ``load()`` returns -- which is then kept.

        A failing load is not cached: the next call tries again.
        """
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key, _MISSING)
        if entry is not _MISSING and entry[1] > now:
            return entry[0]
        value = load()
        with self._lock:
            self._entries[key] = (value, self._clock() + self.ttl)
        return value

    def invalidate(self, key: Optional[Hashable] = None) -> None:
        """Drop one key, or everything."""
        with self._lock:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)


_caches: Dict[str, ReplicaCache] = {}
_registry_lock = threading.Lock()
_ttl = DEFAULT_TTL_SECONDS
#: The application's loop, remembered when the cache is attached to the bus:
#: most changes arrive in a worker thread (a plain ``def`` endpoint), which has
#: no loop of its own to publish from.
_loop: Optional[asyncio.AbstractEventLoop] = None


def get_cache(name: str) -> ReplicaCache:
    """The cache of this name, created on first use."""
    with _registry_lock:
        cache = _caches.get(name)
        if cache is None:
            cache = _caches[name] = ReplicaCache(name, _ttl)
        return cache


def set_ttl(seconds: Optional[float]) -> None:
    """Set every cache's lifetime (``KeepupSettings.performance``); None keeps the default."""
    global _ttl
    if seconds is None:
        return
    _ttl = float(seconds)
    with _registry_lock:
        for cache in _caches.values():
            cache.ttl = _ttl


def invalidate_all() -> None:
    """Drop every cache of this replica -- after start-up changesets, for instance."""
    with _registry_lock:
        caches = list(_caches.values())
    for cache in caches:
        cache.invalidate()


def invalidate_everywhere(name: str) -> None:
    """Drop a cache here, and ask the other replicas to drop theirs.

    Callable from synchronous code, including a worker thread: the message to
    the other replicas is sent from the running loop when there is one, and
    skipped otherwise -- the lifetime covers them then.
    """
    get_cache(name).invalidate()
    from keepup import notification_bus
    bus = notification_bus.get_notification_bus()
    if bus is None or not getattr(bus, "can_publish", bus.is_running):
        return
    envelope = {"kind": ENVELOPE_KIND, "cache": name}
    loop = _loop
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        running.create_task(_publish(bus, envelope))
    elif loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(_publish(bus, envelope), loop)


async def _publish(bus, envelope) -> None:
    try:
        await bus.publish(envelope)
    except Exception as error:
        logger.warning(f"Could not tell the other replicas to drop cache "
                       f"{envelope.get('cache')}: {error}")


def attach_to_bus(bus) -> None:
    """Listen for the other replicas' changes; called from the running loop at start-up."""
    global _loop
    _loop = asyncio.get_running_loop()
    bus.subscribe(on_envelope)


async def on_envelope(envelope: Dict[str, Any]) -> None:
    """The bus subscriber: another replica changed something this one caches."""
    if envelope.get("kind") == ENVELOPE_KIND and envelope.get("cache") in _caches:
        _caches[envelope["cache"]].invalidate()
