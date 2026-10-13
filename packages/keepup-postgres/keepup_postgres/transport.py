"""Carrying envelopes between replicas over PostgreSQL's NOTIFY (keepup-117).

The bus is the kernel's: the envelopes, the seal, the subscribers. The delivery
is the driver's, because holding a LISTEN connection and sending `pg_notify` is
what `asyncpg` does -- and `asyncpg` is what this distribution brings. A
deployment on a database that cannot carry a message between processes publishes
the in-process transport instead, and says so in its report.
"""

import asyncio
import logging
from typing import Any, Callable, Optional

from keepup.notification_bus import (
    RECONNECT_DELAY_INITIAL,
    RECONNECT_DELAY_MAX,
    Transport,
)

logger = logging.getLogger(__name__)

__all__ = ["PostgresTransport"]


class PostgresTransport(Transport):
    """LISTEN/NOTIFY: one connection listens for as long as it listens."""

    def __init__(self):
        self._connection: Optional[Any] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._stopping = False
        self._channel = ""
        self._receive: Optional[Callable[[str], None]] = None

    @property
    def is_running(self) -> bool:
        """Whether the listening connection is up."""
        return self._connection is not None

    async def start(self, channel: str, receive: Callable[[str], None]) -> bool:
        """Listen on the application's channel, reopening the connection after a break."""
        from keepup.db import db_config

        if not db_config.is_postgres():
            logger.info("Notifications need PostgreSQL; this deployment carries them "
                        "only inside its own process")
            return False

        self._channel, self._receive = channel, receive
        self._stopping = False
        self._listener_task = asyncio.create_task(self._listen_forever())
        return True

    async def stop(self) -> None:
        """Stop reconnecting and close the listening connection."""
        self._stopping = True
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, Exception):
                pass
            self._listener_task = None
        await self._close_connection()

    async def publish(self, channel: str, payload: str) -> bool:
        """Send one payload from the pool, never on the listening connection.

        asyncpg runs one operation at a time on a connection: two envelopes
        published there made the second fail with "another operation is in
        progress", returned False and were lost without a trace, and every
        publish held up the listener's reading (keepup-50). `pg_notify` needs no
        LISTEN on the connection that sends it.
        """
        from keepup.db import DatabaseManagerV2

        try:
            await DatabaseManagerV2.execute_commit_async(
                "SELECT pg_notify(:channel, :payload)",
                {"channel": channel, "payload": payload})
            return True
        except Exception as error:
            logger.error("Could not publish a notification: %s", error)
            return False

    async def _close_connection(self) -> None:
        if self._connection is None:
            return
        connection, self._connection = self._connection, None
        try:
            await connection.close()
        except Exception as error:
            logger.warning("Could not close the notification connection: %s", error)

    async def _connect(self):
        """Open a connection dedicated to listening.

        Deliberately not from the SQLAlchemy pool: LISTEN occupies a connection
        for as long as it listens, and a pooled connection handed back would stop
        delivering while still looking reusable.
        """
        from keepup.db import db_config
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
        if self._receive is not None:
            self._receive(payload)

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
                # Published only once it listens: is_running answers "envelopes
                # from other replicas are arriving", and a connection still
                # attaching its listener does not deliver yet.
                connection = await self._connect()
                try:
                    await connection.add_listener(self._channel, self._on_notify)
                except BaseException:
                    await connection.close()
                    raise
                self._connection = connection
                logger.info("Notification bus listening on '%s'", self._channel)
                delay = RECONNECT_DELAY_INITIAL

                while not self._stopping and self._connection is not None:
                    if self._connection.is_closed():
                        raise ConnectionError("the listening connection was closed")
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self._close_connection()
                if self._stopping:
                    return
                logger.warning(
                    "Notification bus lost its connection (%s); reconnecting in %.0fs",
                    error, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_DELAY_MAX)
