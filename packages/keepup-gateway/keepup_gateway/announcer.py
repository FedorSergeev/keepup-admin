"""A service telling the gateway it is here, and repeating it while it is.

The application behind the gateway installs `keepup-gateway` too, and its
`announce` section says which gateway, under what name and prefix, and at which
address it is reached. It registers at the start and again every `interval`
seconds, well inside the record's time to live; on a normal stop it takes its
address back, and on a crash the record runs out by itself.

The loop is an asyncio task of the plugin, not a job of the scheduler: a headless
subsystem need not install `keepup-tasks` to be found.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Mapping, Optional

import httpx

from keepup_gateway.registry import (
    ACTION_DEREGISTER,
    ACTION_REGISTER,
    DEFAULT_TTL_SECONDS,
    sign_registration,
)
from keepup_gateway.table import TableError

logger = logging.getLogger(__name__)

__all__ = ["REGISTER_PATH", "Announcer", "parse_announce"]

REGISTER_PATH = "/api/gateway/services"
#: The route keys an announcement may carry to the gateway.
_ROUTE_KEYS = ("methods", "strip_prefix", "auth", "permission", "timeout",
               "connect_timeout", "max_body_bytes")


def parse_announce(section: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """The ``announce`` section, checked; None when there is none.

    Raises:
        TableError: naming what is missing.
    """
    if not section:
        return None
    if not isinstance(section, Mapping):
        raise TableError("gateway: announce must be an object")
    for key in ("gateway", "name", "prefix", "address"):
        if not isinstance(section.get(key), str) or not section[key].strip():
            raise TableError(f"gateway: announce.{key} is required")
    ttl = section.get("ttl", DEFAULT_TTL_SECONDS)
    interval = section.get("interval", ttl / 3)
    for key, value in (("ttl", ttl), ("interval", interval)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise TableError(f"gateway: announce.{key} must be a positive number")
    if interval >= ttl:
        raise TableError("gateway: announce.interval must be shorter than announce.ttl, "
                         "or the record runs out between two announcements")
    record = {"name": section["name"].strip(), "prefix": section["prefix"].strip(),
              "address": section["address"].strip().rstrip("/"), "ttl": ttl}
    record.update({key: section[key] for key in _ROUTE_KEYS if key in section})
    return {"gateway": section["gateway"].strip().rstrip("/"), "interval": float(interval),
            "record": record}


class Announcer:
    """Registers one service with one gateway, over and over.

    Args:
        settings: what :func:`parse_announce` returned.
        secret: the secret shared with the gateway.
        transport: an httpx transport, for a test.
    """

    def __init__(self, settings: Mapping[str, Any], secret: str,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self.gateway = settings["gateway"]
        self.interval = settings["interval"]
        self.record = dict(settings["record"])
        self.secret = secret
        self._client = httpx.AsyncClient(transport=transport, timeout=5.0)
        self._task: Optional[asyncio.Task] = None
        self.last_error: Optional[str] = None
        self.registered = False

    def start(self) -> None:
        """Begin announcing, in the background."""
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        """Stop announcing and take the address back."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.send(ACTION_DEREGISTER)
        await self._client.aclose()

    async def _loop(self) -> None:
        while True:
            await self.send(ACTION_REGISTER)
            await asyncio.sleep(self.interval)

    async def send(self, action: str) -> bool:
        """One registration or one withdrawal; a failure is logged and retried later."""
        record = self.record if action == ACTION_REGISTER else {
            "name": self.record["name"], "address": self.record["address"]}
        body = json.dumps(record, sort_keys=True).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   **sign_registration(self.secret, action, body)}
        method = "POST" if action == ACTION_REGISTER else "DELETE"
        try:
            answer = await self._client.request(method, self.gateway + REGISTER_PATH,
                                                content=body, headers=headers)
        except httpx.HTTPError as error:
            return self._failed(f"the gateway did not answer: {error}")
        if answer.status_code >= 300:
            return self._failed(f"the gateway answered {answer.status_code}: "
                                f"{answer.text[:200]}")
        if self.last_error is not None or not self.registered:
            logger.info("Gateway: %s %s at %s", action, self.record["name"], self.gateway)
        self.last_error = None
        self.registered = action == ACTION_REGISTER
        return True

    def _failed(self, reason: str) -> bool:
        if reason != self.last_error:
            logger.warning("Gateway announcement of %s: %s", self.record["name"], reason)
        self.last_error = reason
        return False
