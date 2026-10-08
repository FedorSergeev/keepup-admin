"""The application's event log.

Not the request audit (`keepup/audit.py`) and not the application log
(`keepup/logging_setup.py`): this records things that happened in the product's
own terms -- somebody signed in, an order was accepted, a job failed -- so that
they can be read back by type and by actor long after the log files rotated.

Which types exist is the application's business and arrives through
KeepupSettings: the framework holds the table, the API and the retention, and
knows none of the names. See `doc/event_manager.md`.

This module is the journal -- the table, writing, reading, retention. Its HTTP
routes are `keepup/events_api.py` (keepup-23): the journal is written from
everywhere in an application, the routes are one reader of it among others,
and a module that only emits events has no reason to load FastAPI.
"""

import asyncio
import logging
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy import Index

from keepup import retention
from keepup_db import tables
from keepup_db import DatabaseManagerV2
from keepup.instance import get_instance_id, get_instance_name

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "DEFAULT_EVENTS_RETENTION_DAYS",
    "emit_event",
    "events_retention_background",
    "init_event_manager",
]

# Declared by the audit capability, which keeps the events (keepup-124).
from keepup_audit.tables import APP_EVENTS  # noqa: E402

logger = logging.getLogger(__name__)

# Newest first, as the listing pages; the type-and-date pair was only ever
# created on PostgreSQL.
Index("idx_app_events_created_at", APP_EVENTS.c.created_at.desc())
Index("idx_app_events_type_date", APP_EVENTS.c.event_type,
      APP_EVENTS.c.created_at.desc()).ddl_if(dialect="postgresql")


class EventManager:
    """Application event manager."""

    def __init__(self):
        self.instance_id = get_instance_id()
        self.instance_name = get_instance_name()
        self._initialized = False

    def init_table(self):
        """Create the app_events table."""
        try:
            tables.ensure_tables(APP_EVENTS)
            self._initialized = True
            logger.info(f"App events table initialized successfully (instance: {self.instance_name})")

        except Exception as e:
            logger.error(f"Error initializing app events table: {str(e)}")
            raise

    async def create_event(
            self,
            event_type: str,
            event_text: str,
            event_data: Optional[Dict[str, Any]] = None,
            instance_id: Optional[str] = None,
            instance_name: Optional[str] = None
    ) -> int:
        """Create an event.

        Args:
            event_type: event type
            event_text: event text
            event_data: optional extra payload
            instance_id: instance id, defaults to the current instance
            instance_name: instance name, defaults to the current instance

        Returns:
            int: id of the created event.
        """
        if not self._initialized:
            await asyncio.to_thread(self.init_table)

        try:
            event_instance_id = instance_id or self.instance_id
            event_instance_name = instance_name or self.instance_name

            event_data_json = json.dumps(event_data, ensure_ascii=False) if event_data else None

            row = await DatabaseManagerV2.execute_commit_returning_async('''
            INSERT INTO app_events (event_type, event_text, event_data, instance_id, instance_name)
            VALUES (:event_type, :event_text, :event_data, :instance_id, :instance_name)
            ''', {"event_type": event_type, "event_text": event_text,
                  "event_data": event_data_json, "instance_id": event_instance_id,
                  "instance_name": event_instance_name}, "id")
            logger.debug(f"Event created: {event_type} - {event_text[:50]} (instance: {event_instance_name})")
            return row["id"] if row else None

        except Exception as e:
            logger.error(f"Error creating event: {str(e)}")
            raise

    async def get_events(
            self,
            event_type: Optional[str] = None,
            instance_id: Optional[str] = None,
            instance_name: Optional[str] = None,
            start_date: Optional[datetime] = None,
            end_date: Optional[datetime] = None,
            page: int = 1,
            page_size: int = 50
    ) -> Dict[str, Any]:
        """Return events, filtered and paginated.

        Args:
            event_type: filter by event type
            instance_id: filter by instance id
            instance_name: filter by instance name
            start_date: lower date bound
            end_date: upper date bound
            page: page number, starting at 1
            page_size: page size

        Returns:
            Dict with the events and their metadata.
        """
        if not self._initialized:
            await asyncio.to_thread(self.init_table)

        try:
            conditions, params = [], {}
            for column, operator, name, value in (
                    ("event_type", "=", "event_type", event_type),
                    ("instance_id", "=", "instance_id", instance_id),
                    ("instance_name", "=", "instance_name", instance_name),
                    ("created_at", ">=", "start_date", start_date),
                    ("created_at", "<=", "end_date", end_date)):
                if value:
                    conditions.append(f"{column} {operator} :{name}")
                    params[name] = value

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            total_result = await DatabaseManagerV2.execute_one_async(
                f"SELECT COUNT(*) as total FROM app_events {where_clause}", params)
            total = total_result['total'] if total_result else 0

            offset = (page - 1) * page_size
            rows = await DatabaseManagerV2.execute_async(f'''
            SELECT id, event_type, event_text, event_data, instance_id, instance_name, created_at
            FROM app_events
            {where_clause}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            ''', {**params, "limit": page_size, "offset": offset})

            events = []
            for row in rows:
                event = {
                    'id': row['id'],
                    'event_type': row['event_type'],
                    'event_text': row['event_text'],
                    'instance_id': row['instance_id'],
                    'instance_name': row['instance_name'] if len(row) > 5 else None,
                    'created_at': row['created_at'].isoformat() if len(row) > 6 and hasattr(row['created_at'], 'isoformat') else str(
                        row['created_at']) if len(row) > 6 else None
                }

                # Deprecated: `event_data` has always carried the *text*, parsed
                # as JSON when it happened to be JSON, and the event's own data
                # was not in the answer at all (keepup-57). Kept as it was for
                # whoever reads it; goes in the next major release.
                if len(row) > 3 and row['event_text']:
                    try:
                        if isinstance(row['event_text'], str):
                            event['event_data'] = json.loads(row['event_text'])
                        else:
                            event['event_data'] = row['event_text']
                    except (TypeError, ValueError):
                        event['event_data'] = row['event_text']

                event['data'] = _stored_data(row['event_data'])

                events.append(event)

            total_pages = (total + page_size - 1) // page_size

            return {
                'events': events,
                'total': total,
                'page': page,
                'page_size': page_size,
                'total_pages': total_pages
            }

        except Exception as e:
            logger.error(f"Error getting events: {str(e)}")
            raise

    async def get_event_types(self) -> List[str]:
        """Return every distinct event type.

        Returns:
            List[str]: the event types.
        """
        if not self._initialized:
            await asyncio.to_thread(self.init_table)

        try:
            rows = await DatabaseManagerV2.execute_async(
                "SELECT DISTINCT event_type FROM app_events ORDER BY event_type")
            return [row['event_type'] for row in rows]

        except Exception as e:
            logger.error(f"Error getting event types: {str(e)}")
            return []

    async def get_instances(self) -> List[Dict[str, str]]:
        """Return every instance that has produced events.

        Returns:
            List[Dict]: instances with their id and name.
        """
        if not self._initialized:
            await asyncio.to_thread(self.init_table)

        try:
            rows = await DatabaseManagerV2.execute_async('''
            SELECT DISTINCT instance_id, instance_name 
            FROM app_events 
            WHERE instance_name IS NOT NULL
            ORDER BY instance_name
            ''')
            return [{'instance_id': row['instance_id'], 'instance_name': row['instance_name']}
                    for row in rows]

        except Exception as e:
            logger.error(f"Error getting instances: {str(e)}")
            return []

    async def delete_old_events(self, days: int = 30) -> int:
        """Delete events older than the given number of days.

        Args:
            days: age threshold in days, 30 by default

        Returns:
            int: number of deleted events.
        """
        if not self._initialized:
            await asyncio.to_thread(self.init_table)

        try:
            # The boundary is computed here rather than written into the
            # statement. It used to be `INTERVAL '%s days'` -- the placeholder
            # inside a string literal, where the driver's quoting does not
            # reach: a string argument would have left the literal. Nothing
            # exploited it only because the one caller passes an int from a
            # bounded query parameter, and this is a public method (keepup-15).
            cutoff = datetime.utcnow() - timedelta(days=int(days))
            deleted_count = await DatabaseManagerV2.execute_commit_async(
                "DELETE FROM app_events WHERE created_at < :cutoff", {"cutoff": cutoff})
            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} old events (older than {days} days)")
            return deleted_count

        except Exception as e:
            logger.error(f"Error deleting old events: {str(e)}")
            return 0

    async def get_events_stats(self) -> Dict[str, Any]:
        """Return event statistics grouped by instance.

        Returns:
            Dict: the statistics.
        """
        if not self._initialized:
            await asyncio.to_thread(self.init_table)

        try:
            # One statement for both dialects; rows are read by column name,
            # which is what a PostgreSQL row is (it has no positions).
            type_rows = await DatabaseManagerV2.execute_async('''
            SELECT event_type, COUNT(*) as count,
                   MIN(created_at) as first_event, MAX(created_at) as last_event
            FROM app_events
            GROUP BY event_type
            ORDER BY count DESC
            ''')
            instance_rows = await DatabaseManagerV2.execute_async('''
            SELECT instance_id, instance_name, COUNT(*) as event_count,
                   MIN(created_at) as first_event, MAX(created_at) as last_event
            FROM app_events
            GROUP BY instance_id, instance_name
            ORDER BY event_count DESC
            ''')

            def moment(value):
                return value.isoformat() if hasattr(value, 'isoformat') else str(value)

            type_stats = [{
                'event_type': row['event_type'],
                'count': row['count'],
                'first_event': moment(row['first_event']),
                'last_event': moment(row['last_event']),
            } for row in type_rows]
            instance_stats = [{
                'instance_id': row['instance_id'],
                'instance_name': row['instance_name'],
                'event_count': row['event_count'],
                'first_event': moment(row['first_event']),
                'last_event': moment(row['last_event']),
            } for row in instance_rows]

            return {
                "total_events": sum(stat['count'] for stat in type_stats),
                "unique_event_types": len(type_stats),
                "unique_instances": len(instance_stats),
                "event_types": type_stats,
                "instances": instance_stats,
                "current_instance": {
                    "id": self.instance_id,
                    "name": self.instance_name
                }
            }

        except Exception as e:
            logger.error(f"Error getting events stats: {str(e)}")
            return {
                "total_events": 0,
                "unique_event_types": 0,
                "unique_instances": 0,
                "event_types": [],
                "instances": [],
                "current_instance": {
                    "id": self.instance_id,
                    "name": self.instance_name
                }
            }


event_manager = EventManager()


# --- how long an event lives --------------------------------------------------

#: How many days an event is kept (``EVENTS_RETENTION_DAYS``). The log was never
#: swept on its own -- only a manual route deleted old events -- so it grew for as
#: long as the deployment ran. Longer than the request audit: an event is the
#: product's own record of what happened (a sign-in, a blocked account), read
#: back by an administrator weeks later.
DEFAULT_EVENTS_RETENTION_DAYS = 90
#: How often the sweep runs, and how much one pass may delete.
EVENTS_SWEEP_INTERVAL_SECONDS = 3600
EVENTS_DELETE_CHUNK_ROWS = 5000
EVENTS_MAX_CHUNKS_PER_PASS = 20
#: How long to wait after a failed pass.
EVENTS_ERROR_BACKOFF_SECONDS = 60

EVENTS_RETENTION_LOCK = "app_events_retention"


def _stored_data(value):
    """The event's data as it was written: a mapping, or None when there is none.

    Stored as JSON text; a value that does not parse is handed back as it is
    rather than dropped, so nothing written is lost from the answer.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def events_retention_days() -> int:
    """How many days events are kept (``EVENTS_RETENTION_DAYS``, 90 by default)."""
    return retention.retention_days("EVENTS_RETENTION_DAYS", DEFAULT_EVENTS_RETENTION_DAYS)


def purge_old_events(days: Optional[int] = None, now: Optional[datetime] = None) -> int:
    """Delete events older than the retention period, in chunks; how many went."""
    return retention.purge_older_than(
        "app_events", "created_at",
        days if days is not None else events_retention_days(),
        EVENTS_DELETE_CHUNK_ROWS, EVENTS_MAX_CHUNKS_PER_PASS, now=now)


async def events_retention_background():
    """Sweep the event log once per interval, under a distributed lock.

    Locked because the work is the deployment's, not the replica's. A failed pass
    costs nothing: writing events runs on its own path.
    """
    from keepup.locks import distributed_lock

    while True:
        try:
            async with distributed_lock(EVENTS_RETENTION_LOCK, timeout=5,
                                        max_lock_time=EVENTS_SWEEP_INTERVAL_SECONDS):
                removed = await asyncio.to_thread(purge_old_events)
            if removed:
                logger.info("Events expired: %s", removed)
            await asyncio.sleep(EVENTS_SWEEP_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("Error in the event log retention sweep: %s", error)
            await asyncio.sleep(EVENTS_ERROR_BACKOFF_SECONDS)

async def emit_event(
        event_type: str,
        event_text: str,
        event_data: Optional[Dict[str, Any]] = None,
        instance_id: Optional[str] = None,
        instance_name: Optional[str] = None
) -> int:
    """Emit an event from anywhere in the application.

    Args:
        event_type: event type
        event_text: event text
        event_data: optional extra payload
        instance_id: instance id
        instance_name: instance name

    Returns:
        int: id of the created event.
    """
    return await event_manager.create_event(
        event_type=event_type,
        event_text=event_text,
        event_data=event_data,
        instance_id=instance_id,
        instance_name=instance_name
    )


async def get_events_paginated(
        event_type: Optional[str] = None,
        instance_id: Optional[str] = None,
        instance_name: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 50
) -> Dict[str, Any]:
    """Return events with pagination."""
    return await event_manager.get_events(
        event_type=event_type,
        instance_id=instance_id,
        instance_name=instance_name,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size
    )


def init_event_manager():
    """Initialise the event manager at application start."""
    try:
        event_manager.init_table()
        logger.info(f"Event manager initialized successfully (instance: {event_manager.instance_name})")
        return event_manager
    except Exception as e:
        logger.error(f"Failed to initialize event manager: {str(e)}")
        return None


#: Names that moved to keepup/events_api.py (keepup-23). Still answered here for
#: code written against 0.1, with a warning naming the new place; looked up on
#: access, so the journal does not import its API.
_MOVED_TO_API = {"register_event_api_routes", "AppEvent", "EventCreate", "EventListResponse"}


def __getattr__(name):
    if name in _MOVED_TO_API:
        import warnings
        from keepup import events_api
        warnings.warn(f"keepup.events.{name} moved to keepup.events_api.{name}",
                      DeprecationWarning, stacklevel=2)
        return getattr(events_api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
