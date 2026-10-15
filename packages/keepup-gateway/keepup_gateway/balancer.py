"""Which address of a route gets the next request.

Round robin over the addresses that have not failed lately. An address that did
not take a connection or did not answer in time is left out for the route's
cooldown. The state is this replica's own, the way a balancer's always is: each
replica finds out for itself whether it reaches an address, and sharing that
through the database would turn every proxied request into a write.
"""

import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

__all__ = ["Balancer"]


class Balancer:
    """The turn and the cooldowns of every route, in this replica.

    Args:
        clock: what tells the time; a test passes its own.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._turn: Dict[str, int] = {}
        self._cooling: Dict[Tuple[str, str], float] = {}
        self._failures: Dict[Tuple[str, str], str] = {}

    def order(self, route_id: str, upstreams: Tuple[str, ...]) -> List[str]:
        """The addresses to try for one request, the next in turn first.

        Addresses that are cooling go after the live ones rather than being
        dropped: when every address failed a moment ago, trying one is better
        than refusing a request that might have passed.
        """
        with self._lock:
            start = self._turn.get(route_id, 0)
            self._turn[route_id] = (start + 1) % max(1, len(upstreams))
            now = self._clock()
            turned = [upstreams[(start + step) % len(upstreams)]
                      for step in range(len(upstreams))]
            live = [address for address in turned
                    if self._cooling.get((route_id, address), 0.0) <= now]
            cooling = [address for address in turned if address not in live]
            return live + cooling

    def failed(self, route_id: str, address: str, cooldown: float, reason: str = "") -> None:
        """Leave an address out of the turn for a while."""
        with self._lock:
            self._cooling[(route_id, address)] = self._clock() + cooldown
            self._failures[(route_id, address)] = reason

    def answered(self, route_id: str, address: str) -> None:
        """An address answered: it is back in the turn at once."""
        with self._lock:
            self._cooling.pop((route_id, address), None)
            self._failures.pop((route_id, address), None)

    def state(self, route_id: str, address: str) -> Dict[str, Optional[object]]:
        """What the administrator's report says about one address."""
        with self._lock:
            until = self._cooling.get((route_id, address))
            left = None if until is None else until - self._clock()
            if left is not None and left <= 0:
                left = None
            return {
                "address": address,
                "state": "live" if left is None else "cooling",
                "cooling_seconds": None if left is None else round(left, 1),
                "last_failure": self._failures.get((route_id, address)) if left else None,
            }
