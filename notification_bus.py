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
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional


#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "MAX_ENVELOPE_AGE_SECONDS",
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


#: Fields the seal adds to every envelope on the channel (keepup-66). Any
#: role that can connect to the database can NOTIFY on the application's
#: channel, and the envelopes reach host agents' sockets; an envelope is
#: accepted only under a seal made with a key derived from the application's
#: signing secret, and only while fresh -- a captured one cannot be replayed
#: later. `decode_envelope` checks and strips both.
SEAL_FIELD = "seal"
SENT_FIELD = "sent"

#: How old a sealed envelope may be when it arrives, in seconds. NOTIFY is
#: delivered within milliseconds; the margin is for replicas' clocks.
MAX_ENVELOPE_AGE_SECONDS = 300

_SEAL_CONTEXT = b"keepup.notification_bus"


def _seal_key() -> bytes:
    """A key for this purpose only, derived from the signing secret.

    Derived rather than the secret itself, so a seal says nothing that could be
    turned against the tokens signed with it. Raises SigningKeyUnavailable when
    the application has no secret -- it would not have started without one.
    """
    from keepup_auth.signing_key import resolve_signing_key
    return hmac.new(resolve_signing_key().encode("utf-8"), _SEAL_CONTEXT, hashlib.sha256).digest()


def _canonical(body: Dict[str, Any]) -> bytes:
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")


def _seal(body: Dict[str, Any]) -> str:
    return hmac.new(_seal_key(), _canonical(body), hashlib.sha256).hexdigest()


def encode_envelope(envelope: Dict[str, Any]) -> str:
    """Serialise and seal an envelope for the channel.

    Raises:
        ValueError: when the result exceeds what the channel accepts. An
            envelope is meant to name an object, not to carry it, so hitting
            this means the caller put state in it.
    """
    # Through JSON first, so the body sealed is the body the receiver parses.
    body = json.loads(json.dumps(envelope, ensure_ascii=False, default=str))
    body.pop(SEAL_FIELD, None)
    body[SENT_FIELD] = int(time.time())
    body[SEAL_FIELD] = _seal(body)
    payload = json.dumps(body, ensure_ascii=False)
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

    seal = envelope.pop(SEAL_FIELD, None)
    try:
        expected = _seal(envelope)
    except Exception as error:
        logger.warning(f"Dropping an envelope: cannot check its seal ({error})")
        return None
    if not isinstance(seal, str) or not hmac.compare_digest(seal, expected):
        logger.warning("Dropping an envelope without a valid seal")
        return None
    sent = envelope.pop(SENT_FIELD, None)
    if not isinstance(sent, int) or abs(time.time() - sent) > MAX_ENVELOPE_AGE_SECONDS:
        logger.warning("Dropping a stale envelope")
        return None

    return envelope


def is_own_envelope(envelope: Dict[str, Any], instance_id: str) -> bool:
    """Whether this replica published the envelope it just received.

    Delivery to local sockets happens at publish time, so accepting the echo
    would send the same message to the same participant twice.
    """
    return envelope.get(ORIGIN_FIELD) == instance_id


#: The service a database driver publishes to carry envelopes between replicas.
SERVICE_NOTIFY_TRANSPORT = "notify_transport"


class Transport:
    """How envelopes leave this process, and how the other replicas' arrive.

    The kernel owns the bus -- the envelopes, the seal, the subscribers -- and
    the *driver of the database* owns the delivery, because that is where the
    library that can carry a message between processes lives (keepup-117). A
    deployment whose driver publishes no transport keeps the in-process case: an
    envelope reaches this process and no other.
    """

    @property
    def is_running(self) -> bool:
        """Whether envelopes from the other replicas are arriving."""
        return False

    async def start(self, channel: str, receive: Callable[[str], None]) -> bool:
        """Begin listening; ``receive`` is handed every payload that arrives."""
        return False

    async def stop(self) -> None:
        """Stop listening; called with the application."""

    async def publish(self, channel: str, payload: str) -> bool:
        """Send one payload to the channel. Never raises."""
        return False


class InProcessTransport(Transport):
    """The single-replica case: what is published is delivered here, nowhere else.

    Published by a driver whose database cannot carry a message between
    processes (`keepup-sqlite`), so that a deployment keeps a working bus, and a
    cluster that reports itself instead of one that hears nobody.
    """

    @property
    def is_running(self) -> bool:
        return True

    async def start(self, channel: str, receive: Callable[[str], None]) -> bool:
        return True

    async def publish(self, channel: str, payload: str) -> bool:
        return True


class NotificationBus:
    """Publishes envelopes to the other replicas and receives theirs.

    The bus knows nothing about WebSockets. Subscribers -- in practice a
    WebSocket plugin -- register a handler and decide for themselves whether an
    envelope addresses a connection they hold.

    One channel per application is enough: envelopes are cheap to filter, and a
    channel per kind would multiply connections without splitting real traffic.
    """

    def __init__(self, instance_id: str, channel: str,
                 transport: Optional[Transport] = None):
        if not channel:
            raise ValueError("a notification bus needs the application's channel name")
        self._instance_id = instance_id
        self._channel = channel
        self._handlers: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []
        self._transport = transport
        self._stopping = False
        self._started = False

    def set_transport(self, transport: Optional[Transport]) -> None:
        """Hand the bus the delivery its driver publishes (keepup-117).

        The bus is created before the plugins -- one of them subscribes while it
        initialises -- and the transport is only known once they have all
        registered their services.
        """
        self._transport = transport

    @property
    def is_running(self) -> bool:
        """Whether envelopes from other replicas are currently arriving."""
        return bool(self._transport is not None and self._transport.is_running)

    @property
    def can_publish(self) -> bool:
        """Whether envelopes can be sent to the other replicas.

        Not the same as is_running: sending needs no listening connection, so
        publishing works while the driver's listener is reconnecting.
        """
        return self._started

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

        if not self._started:
            return False

        try:
            payload = encode_envelope(envelope)
        except Exception as e:
            # Oversized, or no secret to seal with: either way nothing is sent.
            logger.error(str(e))
            return False

        if self._transport is None:
            return False
        try:
            return await self._transport.publish(self._channel, payload)
        except Exception as e:
            logger.error(f"Could not publish a notification: {e}")
            return False

    async def start(self) -> bool:
        """Begin listening, through the transport the driver published.

        Returns:
            bool: whether listening began. False without a transport, which is a
            working mode and not a failure: notifications then reach this
            process only.
        """
        if self._transport is None:
            logger.info(
                "Notification bus is off: no driver carries it, and notifications "
                "will reach only the sockets of this process"
            )
            return False

        self._stopping = False
        self._started = True
        return await self._transport.start(self._channel, self._receive)

    def _receive(self, payload: str) -> None:
        """A payload from the transport: decode it, skip our own, hand it on."""
        envelope = decode_envelope(payload)
        if envelope is None or is_own_envelope(envelope, self._instance_id):
            return
        asyncio.create_task(self.dispatch(envelope))

    async def stop(self) -> None:
        """Stop the transport with the application."""
        self._stopping = True
        self._started = False
        if self._transport is not None:
            await self._transport.stop()


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
