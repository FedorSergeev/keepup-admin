"""Signed-in sockets end with their session (keepup-65).

A socket is signed in once, at the handshake, and then stays open for as long
as the peer likes: a logout, a password change or a block revoked the session
row, and every *request* after that was refused, but a socket opened with the
same token went on receiving and sending. A stolen token was therefore good
for as long as the thief kept one socket open.

Each replica keeps the sockets it signed in -- the ones that went through
`authenticate_websocket`, which includes every `require_auth` route -- together
with the session each one belongs to. A sweeper reads, in one query, which of
those sessions are still live and closes the rest with 1008. It runs on a
period, and it is woken early when this replica revokes a session and when the
replicas' bus says another one did. The wake-up carries no names: the database
decides which sockets close, so a lost or forged notification costs one query
and never closes a socket that is still entitled to be open. That is also why
the period is not optional -- NOTIFY is best-effort, and without the bus
(SQLite) the period is the only thing another replica's revocation reaches.

Only revocation ends a socket, not the session's expiry: an agent keeps one
socket for the whole of a rental and renews its token beside it, which moves
the end of the same session row.
"""

import asyncio
import logging
import os
import weakref
from typing import Any, Dict, Iterable, Optional, Set

from keepup.db import DatabaseManagerV2

logger = logging.getLogger(__name__)

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "RECHECK_ENV",
    "hold",
    "held_count",
    "sweep",
    "wake",
]

#: Seconds between two checks of the held sockets' sessions. It bounds how long
#: a socket outlives a revocation made on another replica when the bus is down.
RECHECK_ENV = "KEEPUP_SOCKET_SESSION_RECHECK_SECONDS"
DEFAULT_RECHECK_SECONDS = 30.0

ENVELOPE_KIND = "keepup.sessions.revoked"
CLOSE_CODE = 1008
CLOSE_REASON = "Session ended"

#: socket -> session id. Weak, so a socket whose handler returned is dropped
#: without anyone having to remember to release it.
_held: "weakref.WeakKeyDictionary[Any, str]" = weakref.WeakKeyDictionary()
_loop: Optional[asyncio.AbstractEventLoop] = None
_woken: Optional[asyncio.Event] = None
_bus = None


def recheck_seconds() -> float:
    try:
        return max(1.0, float(os.getenv(RECHECK_ENV, DEFAULT_RECHECK_SECONDS)))
    except ValueError:
        return DEFAULT_RECHECK_SECONDS


def hold(websocket, user: Dict[str, Any]) -> None:
    """Remember a socket signed in as ``user`` until its session is revoked."""
    sid = (user or {}).get("session_id")
    if sid:
        _held[websocket] = sid


def held_count() -> int:
    return len(_held)


def _live(sids: Iterable[str]) -> Set[str]:
    sids = sorted(set(sids))
    if not sids:
        return set()
    names = {f"s{i}": sid for i, sid in enumerate(sids)}
    rows = DatabaseManagerV2.execute(
        "SELECT sid FROM auth_session WHERE revoked_at IS NULL AND sid IN ("
        + ", ".join(f":{name}" for name in names) + ")", names)
    return {row["sid"] for row in rows}


async def sweep() -> int:
    """Close every held socket whose session is no longer live; the number closed."""
    held = list(_held.items())
    if not held:
        return 0
    live = await asyncio.to_thread(_live, (sid for _, sid in held))
    closed = 0
    for websocket, sid in held:
        if sid in live:
            continue
        _held.pop(websocket, None)
        try:
            await websocket.close(code=CLOSE_CODE, reason=CLOSE_REASON)
        except Exception:
            # Already closed by the peer, or mid-close: either way it is gone.
            pass
        closed += 1
    if closed:
        logger.info(f"Closed {closed} socket(s) whose session was revoked")
    return closed


def wake(tell_others: bool = True) -> None:
    """Check the held sockets now rather than at the end of the period.

    Called by `panel_session.revoke*` from whatever thread revoked, so it only
    schedules; with ``tell_others`` the other replicas are woken through the bus.
    """
    loop = _loop
    if loop is None or loop.is_closed():
        return
    loop.call_soon_threadsafe(_set_and_publish, tell_others)


def _set_and_publish(tell_others: bool) -> None:
    if _woken is not None:
        _woken.set()
    bus = _bus
    if tell_others and bus is not None and bus.can_publish:
        asyncio.ensure_future(_publish(bus))


async def _publish(bus) -> None:
    try:
        await bus.publish({"kind": ENVELOPE_KIND})
    except Exception as error:
        logger.warning(f"Could not tell the other replicas a session was revoked: {error}")


async def on_envelope(envelope: Dict[str, Any]) -> None:
    """The bus subscriber: another replica revoked a session."""
    if envelope.get("kind") == ENVELOPE_KIND and _woken is not None:
        _woken.set()


def attach_to_bus(bus) -> None:
    """Hear the other replicas' revocations; called from the running loop at start-up."""
    global _bus
    _bus = bus
    bus.subscribe(on_envelope)


def detach_from_bus() -> None:
    global _bus
    _bus = None


async def run_forever() -> None:
    """The sweeper: one per replica, started and cancelled by the application's lifespan."""
    global _loop, _woken
    _loop = asyncio.get_running_loop()
    _woken = asyncio.Event()
    try:
        while True:
            try:
                await asyncio.wait_for(_woken.wait(), timeout=recheck_seconds())
            except asyncio.TimeoutError:
                pass
            _woken.clear()
            try:
                await sweep()
            except Exception as error:
                logger.warning(f"Could not check the sessions of open sockets: {error}")
    finally:
        _loop = None
        _woken = None
