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
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects import postgresql

from keepup import tables
from keepup.db import DatabaseManager, DatabaseManagerV2, db_config
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
            if len(incoming_requests_buffer) >= BUFFER_MAX_SIZE:
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
    async def flush_buffer() -> int:
        """Write the buffered requests to the database."""

        async with incoming_requests_lock:
            if not incoming_requests_buffer:
                return 0

            requests_to_insert = list(incoming_requests_buffer.values())
            inserted_count = 0

            try:
                values = []
                for req in requests_to_insert:
                    values.append((
                        req['instance_id'],
                        req['method'],
                        req['endpoint'],
                        req['host'],
                        json.dumps(req['request_data']) if req['request_data'] else None,
                        req['request_start_at'],
                        req.get('request_end_at'),
                        req.get('duration_ms'),
                        req.get('http_status'),
                        json.dumps(req.get('response_data')) if req.get('response_data') else None,
                        req.get('error_message'),
                        req['created_at']
                    ))

                if values:
                    if db_config.is_postgres():
                        query = '''
                        INSERT INTO incoming_requests 
                        (instance_id, method, endpoint, host, request_data, request_start_at, 
                         request_end_at, duration_ms, http_status, response_data, error_message, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (instance_id, method, endpoint, request_start_at) 
                        DO NOTHING
                        '''
                    else:
                        query = '''
                        INSERT OR IGNORE INTO incoming_requests 
                        (instance_id, method, endpoint, host, request_data, request_start_at, 
                         request_end_at, duration_ms, http_status, response_data, error_message, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        '''

                    if db_config.is_postgres():
                        conn = DatabaseManager.get_connection()
                        cursor = conn.cursor()
                        try:
                            cursor.executemany(query, values)
                            inserted_count = cursor.rowcount
                            conn.commit()
                        finally:
                            cursor.close()
                            conn.close()
                    else:
                        conn = DatabaseManager.get_connection()
                        cursor = conn.cursor()
                        try:
                            cursor.executemany(query, values)
                            inserted_count = cursor.rowcount
                            conn.commit()
                        finally:
                            cursor.close()
                            conn.close()

                    if inserted_count > 0:
                        inserted_ids = []
                        for req_id, req_data in incoming_requests_buffer.items():
                            for v in values:
                                if (req_data['instance_id'] == v[0] and
                                        req_data['method'] == v[1] and
                                        req_data['endpoint'] == v[2] and
                                        req_data['request_start_at'] == v[5]):
                                    inserted_ids.append(req_id)
                                    break

                        for req_id in inserted_ids:
                            incoming_requests_buffer.pop(req_id, None)

                        logger.info(f"Flushed {inserted_count} incoming requests to database")

            except Exception as e:
                logger.error(f"Error flushing incoming requests buffer: {str(e)}")

            return inserted_count


#: What PostgreSQL enforces with named constraints, SQLite held as a unique
#: index of the same name and no checks at all; each dialect keeps what it had.
# --- how long a row lives ----------------------------------------------------

#: How many days an audit row is kept. The table used to have no sweep at all,
#: while metric snapshots and application events both had one: it grew for as
#: long as the deployment ran, and every row in it was a second place a value
#: from a query string lived.
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
    """How many days rows are kept, from the environment or the default.

    Returns:
        A positive number of days; rubbish in the variable means the default
        and a line in the log, because a sweep that switched itself off
        silently would be worse than one that swept too much.
    """
    raw = os.getenv("AUDIT_RETENTION_DAYS")
    if raw in (None, ""):
        return DEFAULT_AUDIT_RETENTION_DAYS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("AUDIT_RETENTION_DAYS=%r is not a number, using %s",
                       raw, DEFAULT_AUDIT_RETENTION_DAYS)
        return DEFAULT_AUDIT_RETENTION_DAYS
    if value <= 0:
        logger.warning("AUDIT_RETENTION_DAYS=%s must be positive, using %s",
                       value, DEFAULT_AUDIT_RETENTION_DAYS)
        return DEFAULT_AUDIT_RETENTION_DAYS
    return value


def purge_old_requests(days=None, now=None):
    """Delete audit rows older than the retention period.

    The boundary is computed here rather than in the statement: the interval
    syntax differs between the engines, and a query written for one does not
    run on the other.

    Args:
        days: how many days to keep; read from the environment when omitted.
        now: the moment to measure from; defaults to the current time.

    Returns:
        How many rows were deleted.
    """
    from datetime import timedelta

    keep_days = days if days is not None else retention_days()
    cutoff = (now or datetime.utcnow()) - timedelta(days=keep_days)

    removed = 0
    for _ in range(AUDIT_MAX_CHUNKS_PER_PASS):
        rows = DatabaseManagerV2.execute(
            "SELECT id FROM incoming_requests WHERE created_at < :cutoff "
            "ORDER BY created_at ASC LIMIT :limit",
            {"cutoff": cutoff, "limit": AUDIT_DELETE_CHUNK_ROWS})
        if not rows:
            break
        ids = {f"i{index}": row["id"] for index, row in enumerate(rows)}
        placeholders = ", ".join(f":{key}" for key in ids)
        DatabaseManagerV2.execute_commit(
            f"DELETE FROM incoming_requests WHERE id IN ({placeholders})", ids)
        removed += len(rows)
        if len(rows) < AUDIT_DELETE_CHUNK_ROWS:
            break
    return removed


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
