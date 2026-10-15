"""The service registry: what registered itself with the gateway, and until when.

A service behind the gateway -- a keepup application with `keepup-gateway` and an
`announce` section in its config -- tells the gateway its name, the prefix it
answers and the address it is reached at, and repeats that every few seconds. The
record lives as long as its time to live: a service that stops repeating leaves
the table by itself, with no step anybody has to remember.

The records are in the database, because the gateway runs in several replicas and
a registration reaches one of them. Each replica reads the live records again at
most every couple of seconds, so a request does not cost a query.

A registration is signed: HMAC-SHA256 with the secret the gateway shares with its
services, over the action, the moment and a digest of the body. Nobody without
the secret can put an address into the table -- which would otherwise be a way
to receive somebody else's traffic.
"""

import hashlib
import hmac
import json
import logging
import time
from datetime import timedelta
from typing import Any, Callable, Dict, List, Mapping, Optional

from keepup import clock
from keepup_gateway.table import (
    SOURCE_REGISTERED,
    GatewayRoute,
    TableError,
    parse_route,
)

logger = logging.getLogger(__name__)

__all__ = [
    "TABLE",
    "DEFAULT_TTL_SECONDS",
    "MAX_TTL_SECONDS",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "RegistrationError",
    "Registry",
    "declared_table",
    "sign_registration",
    "verify_registration",
]

TABLE = "gateway_service"
DEFAULT_TTL_SECONDS = 30
#: A record nobody repeats for longer than this is a record of a service that is gone.
MAX_TTL_SECONDS = 300
#: How stale a replica's copy of the live records may be.
REFRESH_SECONDS = 2.0
#: How old a signed registration is still accepted.
MAX_AGE_SECONDS = 60

SIGNATURE_HEADER = "X-Gateway-Registration-Signature"
TIMESTAMP_HEADER = "X-Gateway-Registration-Timestamp"
ACTION_REGISTER = "register"
ACTION_DEREGISTER = "deregister"

#: What a registration may say about its routes; the rest is the gateway's.
_ROUTE_KEYS = ("methods", "strip_prefix", "auth", "permission", "timeout",
               "connect_timeout", "max_body_bytes")
#: How many prefixes one service may hold: an application answers a handful of
#: areas, not a route per endpoint.
MAX_PREFIXES = 32


class RegistrationError(ValueError):
    """A registration the gateway does not accept.

    Attributes:
        status: what the HTTP answer is.
    """

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def declared_table():
    """The registry's table (`tables.py`), read only when the registry is on: the
    rest of the gateway does not need the database library loaded."""
    from keepup_gateway.tables import GATEWAY_SERVICE

    return GATEWAY_SERVICE


# --- signing ------------------------------------------------------------------------------

def sign_registration(secret: str, action: str, body: bytes,
                      now: Optional[float] = None) -> Dict[str, str]:
    """The headers that prove a registration came from a holder of the secret."""
    moment = str(int(time.time() if now is None else now))
    return {TIMESTAMP_HEADER: moment,
            SIGNATURE_HEADER: _signature(secret, action, moment, body)}


def verify_registration(secret: str, action: str, headers: Mapping[str, str], body: bytes,
                        now: Optional[float] = None) -> None:
    """Refuse a registration that is not signed with the secret, or is old.

    Raises:
        RegistrationError: 401.
    """
    lowered = {str(key).lower(): value for key, value in dict(headers).items()}
    moment = lowered.get(TIMESTAMP_HEADER.lower(), "")
    given = lowered.get(SIGNATURE_HEADER.lower(), "")
    if not secret or not moment or not given:
        raise RegistrationError("the registration is not signed", 401)
    if not hmac.compare_digest(_signature(secret, action, moment, body), given):
        raise RegistrationError("the registration's signature does not match", 401)
    try:
        age = (time.time() if now is None else now) - int(moment)
    except ValueError:
        raise RegistrationError("the registration's timestamp is not a number", 401)
    if abs(age) > MAX_AGE_SECONDS:
        raise RegistrationError("the registration is too old", 401)


def _signature(secret: str, action: str, moment: str, body: bytes) -> str:
    material = "\n".join([action, moment, hashlib.sha256(body).hexdigest()]).encode()
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


# --- the records --------------------------------------------------------------------------

def route_of(name: str, address: str, prefix: str,
             settings: Mapping[str, Any]) -> GatewayRoute:
    """The route one prefix of a service stands for, at one of its addresses."""
    entry = {key: settings[key] for key in _ROUTE_KEYS if key in settings}
    entry.update({"id": f"service:{name}:{prefix}", "prefix": prefix,
                  "upstreams": [address]})
    route = parse_route(entry)
    from dataclasses import replace

    return replace(route, application=True, source=SOURCE_REGISTERED, group=name)


def prefixes_of(record: Mapping[str, Any]) -> List[str]:
    """The prefixes a registration names: ``prefixes``, or the one ``prefix``.

    Raises:
        RegistrationError: for none, too many, or one named twice.
    """
    given = record.get("prefixes", record.get("prefix"))
    if isinstance(given, str):
        given = [given]
    if not isinstance(given, list) or not given or not all(isinstance(item, str) for item in given):
        raise RegistrationError("'prefixes' must be a non-empty list of paths")
    if len(given) > MAX_PREFIXES:
        raise RegistrationError(f"at most {MAX_PREFIXES} prefixes per service")
    cleaned = [item.strip().rstrip("/") or "/" for item in given]
    if len(set(cleaned)) != len(cleaned):
        raise RegistrationError("a prefix is named twice")
    return cleaned


class Registry:
    """The live records, in the database, with a short copy per replica.

    Args:
        datasource: the `datasource` service.
        reserved: what the registry may not hand out -- returns the owner's name
            for a prefix that the config or a local service already holds.
        clock_seconds: what tells the time for the copy; a test passes its own.
    """

    def __init__(self, datasource: Any, reserved: Callable[[str], Optional[str]],
                 clock_seconds: Callable[[], float] = time.monotonic):
        self.datasource = datasource
        self.reserved = reserved
        self._clock = clock_seconds
        self._copy: List[GatewayRoute] = []
        self._read_at: Optional[float] = None

    async def register(self, record: Mapping[str, Any], instance: str = "") -> Dict[str, Any]:
        """Accept one address of a service with all its prefixes, or none of them.

        Args:
            record: ``name``, ``address``, ``prefixes`` (or one ``prefix``), the
                routes' own keys and ``ttl`` in seconds.
            instance: the replica that accepted it, for the report.

        Raises:
            RegistrationError: 400 for a record that cannot be served, 409 for a
                prefix that belongs to somebody else.
        """
        name, address = _text(record, "name", 100), _text(record, "address", 500)
        routes = []
        for prefix in prefixes_of(record):
            try:
                routes.append(route_of(name, address, prefix, record))
            except TableError as error:
                raise RegistrationError(str(error))
        ttl = record.get("ttl", DEFAULT_TTL_SECONDS)
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not 0 < ttl <= MAX_TTL_SECONDS:
            raise RegistrationError(f"ttl must be a number of seconds up to {MAX_TTL_SECONDS}")
        now = clock.utcnow()
        for route in routes:
            owner = self.reserved(route.prefix)
            if owner is not None:
                raise RegistrationError(f"the prefix {route.prefix} is taken by {owner}", 409)
            taken = await self.datasource.execute(
                f"SELECT name FROM {TABLE} WHERE prefix = :prefix AND name <> :name "
                f"AND expires_at > :now", {"prefix": route.prefix, "name": name, "now": now})
            if taken:
                raise RegistrationError(f"the prefix {route.prefix} is taken by service "
                                        f"'{dict(taken[0])['name']}'", 409)
        settings = json.dumps({key: record[key] for key in _ROUTE_KEYS if key in record},
                              sort_keys=True)
        held = [route.prefix for route in routes]
        # The address's prefixes are what this registration names: one it named
        # before and does not name now is no longer its.
        current = await self.datasource.execute(
            f"SELECT prefix FROM {TABLE} WHERE name = :name AND address = :address",
            {"name": name, "address": address})
        for row in current or []:
            if dict(row)["prefix"] not in held:
                await self.datasource.execute_commit(
                    f"DELETE FROM {TABLE} WHERE name = :name AND address = :address"
                    f" AND prefix = :prefix",
                    {"name": name, "address": address, "prefix": dict(row)["prefix"]})
        for prefix in held:
            await self.datasource.execute_commit(
                f"INSERT INTO {TABLE} (name, address, prefix, route, instance, ttl,"
                f" registered_at, expires_at) VALUES (:name, :address, :prefix, :route,"
                f" :instance, :ttl, :now, :expires) ON CONFLICT (name, address, prefix)"
                f" DO UPDATE SET route = excluded.route, instance = excluded.instance,"
                f" ttl = excluded.ttl, expires_at = excluded.expires_at",
                {"name": name, "address": address, "prefix": prefix, "route": settings,
                 "instance": instance, "ttl": float(ttl), "now": now,
                 "expires": now + timedelta(seconds=float(ttl))})
        self._read_at = None
        return {"name": name, "address": address, "prefixes": held, "ttl": ttl}

    async def deregister(self, record: Mapping[str, Any]) -> bool:
        """Forget one address of a service, all its prefixes; forgetting what is not
        there is not an error."""
        name, address = _text(record, "name", 100), _text(record, "address", 500)
        removed = await self.datasource.execute_commit(
            f"DELETE FROM {TABLE} WHERE name = :name AND address = :address",
            {"name": name, "address": address})
        self._read_at = None
        return bool(removed)

    async def routes(self) -> List[GatewayRoute]:
        """The live services: one route per name and prefix, with all its addresses."""
        now = self._clock()
        if self._read_at is not None and now - self._read_at < REFRESH_SECONDS:
            return self._copy
        try:
            rows = await self.datasource.execute(
                f"SELECT name, address, prefix, route FROM {TABLE} WHERE expires_at > :now"
                f" ORDER BY name, prefix, address", {"now": clock.utcnow()})
        except Exception as error:  # noqa: BLE001 - the config's routes must keep working
            logger.warning("Gateway registry not read: %s", error)
            return self._copy
        by_route: Dict[str, GatewayRoute] = {}
        for row in rows or []:
            row = dict(row)
            try:
                route = route_of(row["name"], row["address"], row["prefix"],
                                 json.loads(row["route"]))
            except (TableError, ValueError) as error:
                logger.warning("Gateway registry: record %s skipped: %s", row["name"], error)
                continue
            known = by_route.get(route.id)
            if known is None:
                by_route[route.id] = route
            else:
                from dataclasses import replace

                by_route[route.id] = replace(known, upstreams=known.upstreams + route.upstreams)
        self._copy = list(by_route.values())
        self._read_at = now
        return self._copy

    async def purge(self) -> int:
        """Delete what expired: a record that is not live is only noise in the table."""
        return await self.datasource.execute_commit(
            f"DELETE FROM {TABLE} WHERE expires_at <= :now", {"now": clock.utcnow()}) or 0


def _text(record: Mapping[str, Any], key: str, limit: int) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise RegistrationError(f"'{key}' must be a non-empty string of at most {limit} characters")
    return value.strip()
