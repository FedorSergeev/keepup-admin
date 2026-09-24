"""Fan-out of notifications between replicas of one application.

A WebSocket connection is held by whichever replica the participant connected
to, and the registry of those connections is a set of dictionaries in that
process. An event about a participant, however, is born wherever its source
arrived: the builder's callback lands on any replica, an agent's report on the
one holding its socket, an order transition on the one that served the request.
With the three replicas the k8s manifest declares, the chance that the event and
the socket share a replica is about one in three.

This module carries the event to the other replicas. Every replica listens on
one PostgreSQL channel; a publisher writes an envelope, every replica receives
it and delivers to the sockets *it* holds. Nothing keeps a registry of which
replica holds what: that would be a second source of truth about something each
replica already knows about its own sockets, and it would go stale exactly when
it is relied upon -- when a connection drops and when a replica dies.

Delivery is best-effort by construction. NOTIFY does not store and does not
replay, so a replica disconnected at the moment of publication never learns of
the event. That is why an envelope names an object rather than carrying its
state: a missed notification costs one extra read, and the panel re-reads its
objects whenever its socket reconnects. Anything that must not be missed does
not belong here.

Without PostgreSQL -- SQLite is the development mode -- the bus does not start
and publishing is a no-op. Local delivery still works, which for a single
replica is indistinguishable from full delivery.

The channel is the application's: the framework has none of its own. An
application names it in `KeepupSettings.notification_channel`, and the
framework then creates the bus before the plugins initialise and stops it after
they are cleaned up (`keepup/factory.py`).
"""

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from keepup.db import db_config

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "MAX_PAYLOAD_BYTES",
    "NotificationBus",
    "ORIGIN_FIELD",
    "RECONNECT_DELAY_INITIAL",
    "RECONNECT_DELAY_MAX",
    "decode_envelope",
    "deliver",
    "encode_envelope",
    "get_notification_bus",
    "is_own_envelope",
    "publish",
    "set_notification_bus",
]

logger = logging.getLogger(__name__)

#: PostgreSQL caps a NOTIFY payload at 8000 bytes and refuses a longer one at
#: publish time. The cap is enforced here instead, so an oversized envelope is a
#: logged fault of the publisher rather than a failed transaction in the caller.
MAX_PAYLOAD_BYTES = 7500

#: Reconnect backoff for the listener, in seconds. It grows so that a database
#: that is down for minutes is not asked once a second for all of them, and it
#: stops growing before the delay itself becomes the outage.
RECONNECT_DELAY_INITIAL = 1.0
RECONNECT_DELAY_MAX = 30.0

#: Field every envelope carries so a replica can recognise its own. Without it a
#: publisher would deliver locally and then again on receipt, and the
#: participant would get the message twice.
ORIGIN_FIELD = "origin"


def encode_envelope(envelope: Dict[str, Any]) -> str:
    """Serialise an envelope for the channel.

    Raises:
        ValueError: when the result exceeds what the channel accepts. An
            envelope is meant to name an object, not to carry it, so hitting
            this means the caller put state in it.
    """
    payload = json.dumps(envelope, ensure_ascii=False, default=str)
    size = len(payload.encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"notification envelope is {size} bytes, over the {MAX_PAYLOAD_BYTES} "
            f"limit: an envelope names an object, it does not carry its state"
        )
    return payload


def decode_envelope(payload: str) -> Optional[Dict[str, Any]]:
    """Parse an envelope off the channel, or None when it is not one.

    Anything unparseable is dropped rather than raised: the channel is shared,
    and one malformed payload must not take the listener down with it.
    """
    try:
        envelope = json.loads(payload)
    except (TypeError, ValueError):
        logger.warning("Dropping an unparseable envelope")
        return None

    if not isinstance(envelope, dict):
        logger.warning("Dropping a non-object envelope")
        return None

    return envelope


def is_own_envelope(envelope: Dict[str, Any], instance_id: str) -> bool:
    """Whether this replica published the envelope it just received.

    Delivery to local sockets happens at publish time, so accepting the echo
    would send the same message to the same participant twice.
    """
    return envelope.get(ORIGIN_FIELD) == instance_id


class NotificationBus:
    """Publishes envelopes to the other replicas and receives theirs.

    The bus knows nothing about WebSockets. Subscribers -- in practice a
    WebSocket plugin -- register a handler and decide for themselves whether an
    envelope addresses a connection they hold.

    One channel per application is enough: envelopes are cheap to filter, and a
    channel per kind would multiply connections without splitting real traffic.
    """

    def __init__(self, instance_id: str, channel: str):
        if not channel:
            raise ValueError("a notification bus needs the application's channel name")
        self._instance_id = instance_id
        self._channel = channel
        self._handlers: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []
        self._connection = None
        self._listener_task: Optional[asyncio.Task] = None
        self._stopping = False

    @property
    def is_running(self) -> bool:
        """Whether envelopes from other replicas are currently arriving."""
        return self._connection is not None

    @property
    def instance_id(self) -> str:
        return self._instance_id

    def subscribe(self, handler: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        """Register a coroutine to be called with every foreign envelope."""
        self._handlers.append(handler)

    async def dispatch(self, envelope: Dict[str, Any]) -> None:
        """Hand an envelope to every subscriber.

        Public because it is also the local delivery path: publishing delivers
        through the same call, so the two paths cannot drift apart.

        One failing handler must not stop the others: an envelope is usually
        addressed to a connection only one of them holds.
        """
        for handler in self._handlers:
            try:
                await handler(envelope)
            except Exception as e:
                logger.error(f"Notification handler failed: {e}", exc_info=True)

    async def publish(self, envelope: Dict[str, Any]) -> bool:
        """Send an envelope to the other replicas.

        Never raises: the caller is in the middle of a status change, an agent
        report or a console command, and none of those should fail because
        nobody was listening.

        Returns:
            bool: whether the envelope reached the channel. False also means
            "the bus is not running", which is the normal state on SQLite.
        """
        envelope = dict(envelope)
        envelope[ORIGIN_FIELD] = self._instance_id

        if not self.is_running:
            return False

        try:
            payload = encode_envelope(envelope)
        except ValueError as e:
            logger.error(str(e))
            return False

        try:
            await self._connection.execute(
                "SELECT pg_notify($1, $2)", self._channel, payload
            )
            return True
        except Exception as e:
            logger.error(f"Could not publish a notification: {e}")
            return False

    async def start(self) -> bool:
        """Open the listening connection and keep it open.

        Returns:
            bool: whether listening began. False on a database that has no
            such mechanism, which is a working mode and not a failure.
        """
        if not db_config.is_postgres():
            logger.info(
                "Notification bus is off: it needs PostgreSQL, and notifications "
                "will reach only the sockets of this process"
            )
            return False

        self._stopping = False
        self._listener_task = asyncio.create_task(self._listen_forever())
        return True

    async def stop(self) -> None:
        """Close the listening connection and stop reconnecting."""
        self._stopping = True

        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, Exception):
                pass
            self._listener_task = None

        await self._close_connection()

    async def _close_connection(self) -> None:
        if self._connection is None:
            return
        connection, self._connection = self._connection, None
        try:
            await connection.close()
        except Exception as e:
            logger.warning(f"Could not close the notification connection: {e}")

    async def _connect(self):
        """Open a connection dedicated to listening.

        Deliberately not from the SQLAlchemy pool: LISTEN occupies a connection
        for as long as it listens, and a pooled connection handed back would
        stop delivering while still looking reusable.
        """
        import asyncpg

        return await asyncpg.connect(
            host=db_config.db_host,
            port=int(db_config.db_port),
            user=db_config.db_user,
            password=db_config.db_password,
            database=db_config.db_name,
        )

    def _on_notify(self, connection, pid, channel, payload) -> None:
        """asyncpg calls this from the connection's reader task."""
        envelope = decode_envelope(payload)
        if envelope is None:
            return
        if is_own_envelope(envelope, self._instance_id):
            return
        asyncio.create_task(self.dispatch(envelope))

    async def _listen_forever(self) -> None:
        """Hold the listening connection, reopening it after a break.

        The break is logged on both sides -- lost and restored -- because
        delivery has no replay: what happened while this was down is not
        recoverable from the bus, and a silent gap would be indistinguishable
        from nothing having happened.
        """
        delay = RECONNECT_DELAY_INITIAL

        while not self._stopping:
            try:
                self._connection = await self._connect()
                await self._connection.add_listener(self._channel, self._on_notify)
                logger.info(f"Notification bus listening on '{self._channel}'")
                delay = RECONNECT_DELAY_INITIAL

                while not self._stopping and self._connection is not None:
                    if self._connection.is_closed():
                        raise ConnectionError("the listening connection was closed")
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self._close_connection()
                if self._stopping:
                    return
                logger.warning(
                    f"Notification bus lost its connection ({e}); "
                    f"reconnecting in {delay:.0f}s"
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_DELAY_MAX)


#: The application's bus. Created at startup so that a module importing this one
#: does not open a database connection as a side effect of the import.
_bus: Optional[NotificationBus] = None


def get_notification_bus() -> Optional[NotificationBus]:
    """The running bus, or None before startup and after shutdown."""
    return _bus


def set_notification_bus(bus: Optional[NotificationBus]) -> None:
    """Install the bus. Called at startup, and by tests with a stand-in."""
    global _bus
    _bus = bus


async def publish(envelope: Dict[str, Any]) -> bool:
    """Publish through the application's bus, if there is one.

    A call before startup, after shutdown or without PostgreSQL is not an
    error: it means nobody outside this process will hear, which is exactly
    the single-replica case.
    """
    bus = get_notification_bus()
    if bus is None:
        return False
    return await bus.publish(envelope)


async def deliver(envelope: Dict[str, Any]) -> bool:
    """Send an envelope to the other replicas and to this one.

    This is what call sites use. They do not look in their own registries
    first: a sender that skipped the bus whenever the addressee happened to be
    local would exercise the bus only by luck -- never at all on one replica,
    and for the first time on the day a second one is deployed.

    Returns:
        bool: whether the envelope reached the other replicas. Local delivery
        happens either way, so False is not a failure to deliver.
    """
    published = await publish(envelope)

    bus = get_notification_bus()
    if bus is not None:
        await bus.dispatch(envelope)

    return published
