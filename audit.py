"""The audit of incoming requests.

Every call that reaches a plugin route is written to ``incoming_requests``:
who called, what was asked, what came back and how long it took. Writing a row
per request would put the audit on the request path, so rows are buffered in
memory and flushed by size or by age -- which is also why the process flushes
what is left before it exits.

What counts as a secret is the application's to say: ``configure()`` takes the
redaction function, because the framework does not know which field of which
plugin carries a key. What it does know is that it should not guess in the
permissive direction: without a function, values are replaced by a marker and
only the field names are kept. An application that genuinely wants the contents
recorded says so by name -- ``configure(redaction=keep_as_is)``.
"""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects import postgresql

from keepup import retention, tables
from keepup.db import DatabaseManagerV2, db_config
from keepup.instance import get_instance_id

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "BUFFER_FLUSH_INTERVAL",
    "BUFFER_MAX_SIZE",
    "HIDDEN",
    "IncomingRequestLogger",
    "audit_retention_background",
    "background_buffer_flusher",
    "configure",
    "incoming_requests_buffer",
    "init_incoming_requests_table",
    "keep_as_is",
    "log_api_request",
    "names_only",
    "purge_old_requests",
    "retention_days",
]

logger = logging.getLogger(__name__)

incoming_requests_buffer: Dict[str, Dict[str, Any]] = {}
incoming_requests_lock = asyncio.Lock()

BUFFER_FLUSH_INTERVAL = 125
BUFFER_MAX_SIZE = 100


#: What a value is replaced by when the application named no redaction.
HIDDEN = "<hidden>"


def keep_as_is(value):
    """Record everything, values included.

    Named and public because it is a decision an application has to be able to
    state: passing it says "nothing here is a secret", which is different from
    saying nothing at all.

    Args:
        value: the request or response body.

    Returns:
        The same value.
    """
    return value


def names_only(value):
    """Keep the shape and the field names, replace every value.

    The default. A framework that does not know which field carries a key must
    not guess that none of them does: a one-time link, an invitation or reset
    token, an API key in a query string -- all of them used to be written to the
    table in full and kept there. The names are what makes a row useful for
    reading an incident afterwards; the values are what makes it a second place
    the secret lives.

    Args:
        value: the request or response body.

    Returns:
        The same structure with every scalar replaced by HIDDEN.
    """
    if isinstance(value, dict):
        return {key: names_only(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [names_only(item) for item in value]
    if value is None or isinstance(value, bool):
        # Absence and a flag are kept. Nothing can hide in two values, and a
        # row that says only "a boolean was here" stops being any use for
        # reading an incident -- which is the whole reason the table exists.
        return value
    return HIDDEN


redact = names_only


#: Told apart from None so that the settings of the last application built in
#: the process do not go on applying to the next one. "This application hides
#: nothing" is expressible too, but by name -- configure(redaction=keep_as_is) --
#: rather than by an absence that looks like every other absence.
_UNSET = object()


def configure(redaction=_UNSET, flush_interval=_UNSET, max_size=_UNSET):
    """Supply the application's audit values."""
    global redact, BUFFER_FLUSH_INTERVAL, BUFFER_MAX_SIZE
    if redaction is not _UNSET:
        redact = redaction or names_only
    if flush_interval is not _UNSET and flush_interval is not None:
        BUFFER_FLUSH_INTERVAL = flush_interval
    if max_size is not _UNSET and max_size is not None:
        BUFFER_MAX_SIZE = max_size


incoming_requests_buffer: Dict[str, Dict[str, Any]] = {}
incoming_requests_lock = asyncio.Lock()

BUFFER_FLUSH_INTERVAL = 125
BUFFER_MAX_SIZE = 100
#: The most requests the buffer holds, whatever happens to the database
#: (keepup-68). A failed flush puts its rows back, so with the database away the
#: buffer used to grow with every request; past this the oldest go.
BUFFER_HARD_LIMIT = 10_000
#: After a failed flush, requests do not start another one for this long: the
#: timed flusher still retries, but not every request on its own.
FLUSH_RETRY_PAUSE_SECONDS = 5.0

_flush_lock = asyncio.Lock()
_flush_paused_until = 0.0
dropped_requests = 0
#: A request running this long is written without its outcome (keepup-41): the
#: process that would have finished it has most likely gone.
IN_FLIGHT_STALE_SECONDS = 3600


def _as_datetime(value) -> datetime:
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace('Z', ''))
    return value


class IncomingRequestLogger:
    """Records incoming API requests."""

    @staticmethod
    async def start_request(
            instance_id: str,
            method: str,
            endpoint: str,
            host: str,
            request_data: Optional[Dict[str, Any]] = None,
            request_start_at: Optional[datetime] = None
    ) -> str:
        """Begin recording a request and return its id."""
        from uuid import uuid4

        request_id = str(uuid4())
        request_start = request_start_at or datetime.utcnow()

        async with incoming_requests_lock:
            incoming_requests_buffer[request_id] = {
                'instance_id': instance_id,
                'method': method,
                'endpoint': endpoint,
                'host': host,
                'request_data': redact(request_data),
                'request_start_at': request_start,
                'created_at': datetime.utcnow()
            }
            global dropped_requests
            while len(incoming_requests_buffer) > BUFFER_HARD_LIMIT:
                # Insertion order: the first key is the oldest request.
                incoming_requests_buffer.pop(next(iter(incoming_requests_buffer)))
                dropped_requests += 1
            if (len(incoming_requests_buffer) >= BUFFER_MAX_SIZE and not _flush_lock.locked()
                    and time.monotonic() >= _flush_paused_until):
                asyncio.create_task(IncomingRequestLogger.flush_buffer())

        return request_id

    @staticmethod
    async def end_request(
            request_id: str,
            duration_ms: Optional[int] = None,
            http_status: Optional[int] = None,
            response_data: Optional[Dict[str, Any]] = None,
            error_message: Optional[str] = None
    ) -> bool:
        """Finish recording a request."""
        async with incoming_requests_lock:
            if request_id not in incoming_requests_buffer:
                logger.warning(f"Request {request_id} not found in buffer")
                return False

            request = incoming_requests_buffer[request_id]
            request['request_end_at'] = datetime.utcnow()

            if duration_ms is None:
                start_time = request['request_start_at']
                if isinstance(start_time, str):
                    start_time = datetime.fromisoformat(start_time.replace('Z', ''))
                duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            # Only what this call was told, and never over an outcome already
            # recorded. log_api_request() calls this twice -- once with the
            # outcome, then again from its finally -- and an unconditional
            # update meant the second call wrote http_status, response_data and
            # error_message back to None. Every row of the table carried an
            # empty outcome, so a wave of 401s and 500s was indistinguishable
            # from a wave of successful calls (task keepup-11).
            request['duration_ms'] = duration_ms
            if http_status is not None:
                request['http_status'] = http_status
            if response_data is not None:
                # The values people prove rights with never reach the table:
                # node tokens, one-time tokens, volume and image keys all passed
                # through here, and one row was enough to hold the lot.
                request['response_data'] = redact(response_data)
            if error_message is not None:
                request['error_message'] = error_message

            return True

    @staticmethod
    async def flush_buffer(include_in_flight: bool = False, now: Optional[datetime] = None) -> int:
        """Write the finished requests of the buffer to the database.

        A request still running stays in the buffer (keepup-41): written by a
        timed flush and dropped from the buffer, it could no longer receive its
        outcome -- end_request found nothing, logged "not found", and the table
        kept a request with no status. Two exceptions: a request running longer
        than IN_FLIGHT_STALE_SECONDS is written without an outcome, since the
        process that would finish it has most likely gone and the buffer must
        not grow forever; and at shutdown (``include_in_flight``) everything is
        written -- nothing will finish afterwards.
        """
        # One flush at a time: a full buffer asked for one on every request.
        async with _flush_lock:
            return await IncomingRequestLogger._flush_buffer(include_in_flight, now)

    @staticmethod
    async def _flush_buffer(include_in_flight: bool, now: Optional[datetime]) -> int:
        global _flush_paused_until, dropped_requests
        now = now or datetime.utcnow()
        # The buffer lock is taken by the start and the end of every request, so
        # it is held only to pick the rows and take them out -- never across the
        # write, which runs in a worker thread and may take as long as the
        # database does (keepup-44).
        async with incoming_requests_lock:
            chosen = {
                request_id: req for request_id, req in incoming_requests_buffer.items()
                if include_in_flight or 'request_end_at' in req
                or (now - _as_datetime(req['request_start_at'])).total_seconds()
                >= IN_FLIGHT_STALE_SECONDS
            }
            for request_id in chosen:
                incoming_requests_buffer.pop(request_id, None)
        if not chosen:
            return 0

        rows = [{
            'instance_id': req['instance_id'],
            'method': req['method'],
            'endpoint': req['endpoint'],
            'host': req['host'],
            'request_data': json.dumps(req['request_data']) if req['request_data'] else None,
            'request_start_at': req['request_start_at'],
            'request_end_at': req.get('request_end_at'),
            'duration_ms': req.get('duration_ms'),
            'http_status': req.get('http_status'),
            'response_data': (json.dumps(req.get('response_data'))
                              if req.get('response_data') else None),
            'error_message': req.get('error_message'),
            'created_at': req['created_at'],
        } for req in chosen.values()]
        columns = ("instance_id, method, endpoint, host, request_data, request_start_at, "
                   "request_end_at, duration_ms, http_status, response_data, error_message, "
                   "created_at")
        placeholders = ", ".join(":" + c.strip() for c in columns.split(","))
        if db_config.is_postgres():
            query = (f"INSERT INTO incoming_requests ({columns}) VALUES ({placeholders}) "
                     f"ON CONFLICT (instance_id, method, endpoint, request_start_at) DO NOTHING")
        else:
            query = f"INSERT OR IGNORE INTO incoming_requests ({columns}) VALUES ({placeholders})"

        try:
            inserted_count = await DatabaseManagerV2.execute_many_async(query, rows)
        except Exception as e:
            # Back into the buffer: the next flush tries again. A request that
            # somehow reappeared meanwhile keeps its newer entry.
            logger.error(f"Error flushing incoming requests buffer: {str(e)}")
            _flush_paused_until = time.monotonic() + FLUSH_RETRY_PAUSE_SECONDS
            async with incoming_requests_lock:
                for request_id, req in chosen.items():
                    incoming_requests_buffer.setdefault(request_id, req)
            return 0

        # Every chosen request is gone from the buffer, written or ignored as a
        # duplicate alike: an ignored one is already in the table.
        if inserted_count:
            logger.info(f"Flushed {inserted_count} incoming requests to database")
        if dropped_requests:
            logger.warning(f"{dropped_requests} incoming requests were dropped from the "
                           f"audit while it could not be written")
            dropped_requests = 0
        return inserted_count


#: What PostgreSQL enforces with named constraints, SQLite held as a unique
#: index of the same name and no checks at all; each dialect keeps what it had.
# --- how long a row lives ----------------------------------------------------

#: How many days an audit row is kept. The table used to have no sweep at all:
#: it grew for as long as the deployment ran, and every row in it was a second
#: place a value from a query string lived. The event log is swept the same way
#: (keepup/retention.py).
DEFAULT_AUDIT_RETENTION_DAYS = 30
#: How often the sweep runs.
AUDIT_SWEEP_INTERVAL_SECONDS = 3600
#: How many rows one statement removes. Deleting a month of traffic in a single
#: statement would hold up the flush of the buffer behind it.
AUDIT_DELETE_CHUNK_ROWS = 5000
#: How many statements one pass makes. This is what turns clearing a backlog
#: into several short passes instead of one long one.
AUDIT_MAX_CHUNKS_PER_PASS = 20
#: How long to wait after a failed pass.
AUDIT_ERROR_BACKOFF_SECONDS = 60

AUDIT_RETENTION_LOCK = "incoming_requests_retention"


def retention_days():
    """How many days audit rows are kept (``AUDIT_RETENTION_DAYS``, 30 by default)."""
    return retention.retention_days("AUDIT_RETENTION_DAYS", DEFAULT_AUDIT_RETENTION_DAYS)


def purge_old_requests(days=None, now=None):
    """Delete audit rows older than the retention period, in chunks.

    Args:
        days: how many days to keep; read from the environment when omitted.
        now: the moment to measure from; defaults to the current time.

    Returns:
        How many rows were deleted.
    """
    return retention.purge_older_than(
        "incoming_requests", "created_at",
        days if days is not None else retention_days(),
        AUDIT_DELETE_CHUNK_ROWS, AUDIT_MAX_CHUNKS_PER_PASS, now=now)


async def audit_retention_background():
    """Sweep the audit table once per interval, under a distributed lock.

    Locked because the work is shared across replicas: three of them deleting
    the same rows at once make three times the statements and not one row
    fewer. A failed pass costs nothing -- recording requests runs on its own
    path and knows nothing of the sweep.
    """
    from keepup.locks import distributed_lock

    while True:
        try:
            async with distributed_lock(AUDIT_RETENTION_LOCK, timeout=5,
                                        max_lock_time=AUDIT_SWEEP_INTERVAL_SECONDS):
                removed = await asyncio.to_thread(purge_old_requests)
            if removed:
                logger.info("Audit rows expired: %s", removed)
            await asyncio.sleep(AUDIT_SWEEP_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("Error in the audit retention sweep: %s", error)
            await asyncio.sleep(AUDIT_ERROR_BACKOFF_SECONDS)


INCOMING_REQUESTS = tables.table(
    "incoming_requests",
    tables.auto_id(),
    Column("instance_id", Text, nullable=False),
    Column("method", Text, nullable=False),
    Column("endpoint", Text, nullable=False),
    Column("host", Text, nullable=False),
    Column("request_data", postgresql.JSONB().with_variant(Text(), "sqlite")),
    Column("request_start_at", DateTime, nullable=False),
    Column("request_end_at", DateTime),
    Column("duration_ms", Integer),
    Column("http_status", Integer),
    Column("response_data", postgresql.JSONB().with_variant(Text(), "sqlite")),
    Column("error_message", Text),
    Column("created_at", DateTime, server_default=tables.NOW),
    UniqueConstraint("instance_id", "method", "endpoint", "request_start_at",
                     name="incoming_requests_instance_method_endpoint_unique").ddl_if(dialect="postgresql"),
    CheckConstraint("duration_ms >= 0",
                    name="incoming_requests_check_duration").ddl_if(dialect="postgresql"),
    CheckConstraint("(http_status IS NULL) OR (http_status >= 100 AND http_status <= 599)",
                    name="incoming_requests_check_http_status").ddl_if(dialect="postgresql"),
    Index("incoming_requests_instance_method_endpoint_unique",
          "instance_id", "method", "endpoint", "request_start_at", unique=True).ddl_if(dialect="sqlite"),
    Index("idx_incoming_requests_instance_id", "instance_id"),
    Index("idx_incoming_requests_endpoint", "endpoint"),
    Index("idx_incoming_requests_created_at", "created_at"),
)


def init_incoming_requests_table():
    """Create the table holding incoming request logs."""
    tables.ensure_tables(INCOMING_REQUESTS)
    logger.info("Incoming requests table initialized successfully")


async def background_buffer_flusher():
    """Background task flushing the request buffer to the database."""
    while True:
        try:
            await asyncio.sleep(BUFFER_FLUSH_INTERVAL)
            await IncomingRequestLogger.flush_buffer()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in background buffer flusher: {str(e)}")
            await asyncio.sleep(10)


@asynccontextmanager
async def log_api_request(
        method: str,
        endpoint: str,
        host: str = "plugin_api",
        request_data: Optional[Dict[str, Any]] = None
):
    """Async context manager recording one API request."""
    request_id = None
    instance_id = get_instance_id()
    start_time = time.time()

    try:
        request_id = await IncomingRequestLogger.start_request(
            instance_id=instance_id,
            method=method,
            endpoint=endpoint,
            host=host,
            request_data=request_data,
            request_start_at=datetime.utcnow()
        )

        yield request_id

    except Exception as e:
        if request_id:
            await IncomingRequestLogger.end_request(
                request_id=request_id,
                duration_ms=int((time.time() - start_time) * 1000),
                error_message=str(e)
            )
        raise

    finally:
        if request_id:
            await IncomingRequestLogger.end_request(
                request_id=request_id,
                duration_ms=int((time.time() - start_time) * 1000)
            )
