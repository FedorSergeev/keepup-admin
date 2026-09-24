"""The application's event log.

Not the request audit (`keepup/audit.py`) and not the application log
(`keepup/logging_setup.py`): this records things that happened in the product's
own terms -- somebody signed in, an order was accepted, a job failed -- so that
they can be read back by type and by actor long after the log files rotated.

Which types exist is the application's business and arrives through
KeepupSettings: the framework holds the table, the API and the retention, and
knows none of the names. See `doc/event_manager.md`.
"""

import logging
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from fastapi import HTTPException, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Index, String, Text
from sqlalchemy.dialects import postgresql

from keepup import tables
from keepup.db import DatabaseManager
from keepup.db import db_config
from keepup.instance import get_instance_id, get_instance_name
from keepup.auth.dependencies import get_current_admin

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "emit_event",
    "init_event_manager",
    "register_event_api_routes",
]

logger = logging.getLogger(__name__)

APP_EVENTS = tables.table(
    "app_events",
    tables.big_auto_id(),
    Column("event_type", String(100), nullable=False),
    Column("event_text", Text, nullable=False),
    Column("event_data", postgresql.JSONB().with_variant(Text(), "sqlite")),
    Column("instance_id", String(100), nullable=False),
    Column("instance_name", String(100)),
    Column("created_at", DateTime, server_default=tables.NOW),
    Index("idx_app_events_event_type", "event_type"),
    Index("idx_app_events_instance_id", "instance_id"),
    Index("idx_app_events_instance_name", "instance_name"),
)
# Newest first, as the listing pages; the type-and-date pair was only ever
# created on PostgreSQL.
Index("idx_app_events_created_at", APP_EVENTS.c.created_at.desc())
Index("idx_app_events_type_date", APP_EVENTS.c.event_type,
      APP_EVENTS.c.created_at.desc()).ddl_if(dialect="postgresql")


class AppEvent(BaseModel):
    """Application event."""
    id: Optional[int] = None
    event_type: str = Field(..., description="Event type, for example 'user_login', 'payment_success', 'error'")
    event_text: str = Field(..., description="Event text")
    event_data: Optional[Dict[str, Any]] = Field(None, description="Extra event payload, as JSON")
    instance_id: Optional[str] = Field(None, description="Id of the instance that produced the event")
    instance_name: Optional[str] = Field(None, description="Name of the instance that produced the event")
    created_at: Optional[datetime] = Field(None, description="When the event was created")


class EventCreate(BaseModel):
    """Payload for creating an event."""
    event_type: str = Field(..., min_length=1, max_length=100, description="Event type")
    event_text: str = Field(..., min_length=1, description="Event text")
    event_data: Optional[Dict[str, Any]] = Field(None, description="Extra payload")


class EventListResponse(BaseModel):
    """Response carrying a list of events."""
    events: List[Dict[str, Any]]
    total: int
    page: int
    page_size: int
    total_pages: int


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
            self.init_table()

        try:
            event_instance_id = instance_id or self.instance_id
            event_instance_name = instance_name or self.instance_name

            event_data_json = json.dumps(event_data, ensure_ascii=False) if event_data else None

            if db_config.is_postgres():
                query = '''
                INSERT INTO app_events (event_type, event_text, event_data, instance_id, instance_name)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                '''
                params = (event_type, event_text, event_data_json, event_instance_id, event_instance_name)
            else:
                query = '''
                INSERT INTO app_events (event_type, event_text, event_data, instance_id, instance_name)
                VALUES (?, ?, ?, ?, ?)
                '''
                params = (event_type, event_text, event_data_json, event_instance_id, event_instance_name)

            conn = DatabaseManager.get_connection()
            cursor = conn.cursor()

            try:
                cursor.execute(query, params)
                conn.commit()

                if db_config.is_postgres():
                    event_id = cursor.fetchone()["id"]
                else:
                    event_id = cursor.lastrowid

                logger.debug(f"Event created: {event_type} - {event_text[:50]} (instance: {event_instance_name})")
                return event_id

            finally:
                cursor.close()
                conn.close()

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
            self.init_table()

        try:
            conditions = []
            params = []

            if event_type:
                conditions.append("event_type = ?" if not db_config.is_postgres() else "event_type = %s")
                params.append(event_type)

            if instance_id:
                conditions.append("instance_id = ?" if not db_config.is_postgres() else "instance_id = %s")
                params.append(instance_id)

            if instance_name:
                conditions.append("instance_name = ?" if not db_config.is_postgres() else "instance_name = %s")
                params.append(instance_name)

            if start_date:
                conditions.append("created_at >= ?" if not db_config.is_postgres() else "created_at >= %s")
                params.append(start_date)

            if end_date:
                conditions.append("created_at <= ?" if not db_config.is_postgres() else "created_at <= %s")
                params.append(end_date)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            count_query = f"SELECT COUNT(*) as total FROM app_events {where_clause}"

            if db_config.is_postgres():
                count_query = count_query.replace('?', '%s')

            conn = DatabaseManager.get_connection()
            cursor = conn.cursor()

            try:
                cursor.execute(count_query, params)
                total_result = cursor.fetchone()
                total = total_result['total'] if total_result else 0

                offset = (page - 1) * page_size

                select_query = f'''
                SELECT id, event_type, event_text, event_data, instance_id, instance_name, created_at
                FROM app_events
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                ''' if not db_config.is_postgres() else f'''
                SELECT id, event_type, event_text, event_data, instance_id, instance_name, created_at
                FROM app_events
                {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                '''

                query_params = params + [page_size, offset]
                if db_config.is_postgres():
                    select_query = select_query.replace('?', '%s')

                cursor.execute(select_query, query_params)
                rows = cursor.fetchall()

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

                    if len(row) > 3 and row['event_text']:
                        try:
                            if isinstance(row['event_text'], str):
                                event['event_data'] = json.loads(row['event_text'])
                            else:
                                event['event_data'] = row['event_text']
                        except:
                            event['event_data'] = row['event_text']

                    events.append(event)

                total_pages = (total + page_size - 1) // page_size

                return {
                    'events': events,
                    'total': total,
                    'page': page,
                    'page_size': page_size,
                    'total_pages': total_pages
                }

            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            logger.error(f"Error getting events: {str(e)}")
            raise

    async def get_event_types(self) -> List[str]:
        """Return every distinct event type.

        Returns:
            List[str]: the event types.
        """
        if not self._initialized:
            self.init_table()

        try:
            query = '''
            SELECT DISTINCT event_type 
            FROM app_events 
            ORDER BY event_type
            '''

            if db_config.is_postgres():
                query = query.replace('?', '%s')

            conn = DatabaseManager.get_connection()
            cursor = conn.cursor()

            try:
                cursor.execute(query)
                rows = cursor.fetchall()
                return [row['event_type'] for row in rows]
            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            logger.error(f"Error getting event types: {str(e)}")
            return []

    async def get_instances(self) -> List[Dict[str, str]]:
        """Return every instance that has produced events.

        Returns:
            List[Dict]: instances with their id and name.
        """
        if not self._initialized:
            self.init_table()

        try:
            query = '''
            SELECT DISTINCT instance_id, instance_name 
            FROM app_events 
            WHERE instance_name IS NOT NULL
            ORDER BY instance_name
            '''

            if db_config.is_postgres():
                query = query.replace('?', '%s')

            conn = DatabaseManager.get_connection()
            cursor = conn.cursor()

            try:
                cursor.execute(query)
                rows = cursor.fetchall()
                return [
                    {
                        'instance_id': row['instance_id'],
                        'instance_name': row['instance_name']
                    }
                    for row in rows
                ]
            finally:
                cursor.close()
                conn.close()

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
            self.init_table()

        try:
            # The boundary is computed here rather than written into the
            # statement. It used to be `INTERVAL '%s days'` -- the placeholder
            # inside a string literal, where the driver's quoting does not
            # reach: a string argument would have left the literal. Nothing
            # exploited it only because the one caller passes an int from a
            # bounded query parameter, and this is a public method (keepup-15).
            cutoff = datetime.utcnow() - timedelta(days=int(days))
            if db_config.is_postgres():
                query = '''
                DELETE FROM app_events
                WHERE created_at < %s
                RETURNING id
                '''
                params = (cutoff,)
            else:
                query = '''
                DELETE FROM app_events
                WHERE created_at < ?
                '''
                params = (cutoff,)

            conn = DatabaseManager.get_connection()
            cursor = conn.cursor()

            try:
                cursor.execute(query, params)
                conn.commit()

                if db_config.is_postgres():
                    deleted_count = cursor.rowcount
                else:
                    deleted_count = cursor.rowcount

                if deleted_count > 0:
                    logger.info(f"Deleted {deleted_count} old events (older than {days} days)")

                return deleted_count

            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            logger.error(f"Error deleting old events: {str(e)}")
            return 0

    async def get_events_stats(self) -> Dict[str, Any]:
        """Return event statistics grouped by instance.

        Returns:
            Dict: the statistics.
        """
        if not self._initialized:
            self.init_table()

        try:
            if db_config.is_postgres():
                type_stats_query = '''
                SELECT 
                    event_type,
                    COUNT(*) as count,
                    MIN(created_at) as first_event,
                    MAX(created_at) as last_event
                FROM app_events
                GROUP BY event_type
                ORDER BY count DESC
                '''

                instance_stats_query = '''
                SELECT 
                    instance_id,
                    instance_name,
                    COUNT(*) as event_count,
                    MIN(created_at) as first_event,
                    MAX(created_at) as last_event
                FROM app_events
                GROUP BY instance_id, instance_name
                ORDER BY event_count DESC
                '''
            else:
                type_stats_query = '''
                SELECT 
                    event_type,
                    COUNT(*) as count,
                    MIN(created_at) as first_event,
                    MAX(created_at) as last_event
                FROM app_events
                GROUP BY event_type
                ORDER BY count DESC
                '''

                instance_stats_query = '''
                SELECT 
                    instance_id,
                    instance_name,
                    COUNT(*) as event_count,
                    MIN(created_at) as first_event,
                    MAX(created_at) as last_event
                FROM app_events
                GROUP BY instance_id, instance_name
                ORDER BY event_count DESC
                '''

            conn = DatabaseManager.get_connection()
            cursor = conn.cursor()

            try:
                cursor.execute(type_stats_query)
                type_rows = cursor.fetchall()

                type_stats = []
                for row in type_rows:
                    type_stats.append({
                        'event_type': row[0],
                        'count': row[1],
                        'first_event': row[2].isoformat() if hasattr(row[2], 'isoformat') else str(row[2]),
                        'last_event': row[3].isoformat() if hasattr(row[3], 'isoformat') else str(row[3])
                    })

                cursor.execute(instance_stats_query)
                instance_rows = cursor.fetchall()

                instance_stats = []
                for row in instance_rows:
                    instance_stats.append({
                        'instance_id': row[0],
                        'instance_name': row[1],
                        'event_count': row[2],
                        'first_event': row[3].isoformat() if hasattr(row[3], 'isoformat') else str(row[3]),
                        'last_event': row[4].isoformat() if hasattr(row[4], 'isoformat') else str(row[4])
                    })

                total_count = sum(stat['count'] for stat in type_stats)

                return {
                    "total_events": total_count,
                    "unique_event_types": len(type_stats),
                    "unique_instances": len(instance_stats),
                    "event_types": type_stats,
                    "instances": instance_stats,
                    "current_instance": {
                        "id": self.instance_id,
                        "name": self.instance_name
                    }
                }

            finally:
                cursor.close()
                conn.close()

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


def register_event_api_routes(app, declared_event_types=None):
    """Register the event API routes on the application.

    ``declared_event_types`` is supplied by the application: a callable
    returning the event types it declares in code but may never have emitted.
    The framework has no catalogue of its own -- which types exist is a
    property of the application, and reaching into it from here would point
    the dependency the wrong way.
    """

    @app.post("/api/events", response_model=Dict[str, Any])
    async def create_event_endpoint(
            event: EventCreate,
            current_user: dict = Depends(get_current_admin)
    ):
        """Create an event.

        Administrators only. Audit events are written by the server itself at
        the point the action happens (the application's audit helper), never
        accepted over HTTP: a journal the watched party can fill is not
        evidence. The event is attached to the current instance.
        """
        try:
            event_id = await event_manager.create_event(
                event_type=event.event_type,
                event_text=event.event_text,
                event_data=event.event_data
            )

            return {
                "success": True,
                "event_id": event_id,
                "message": "Event created successfully",
                "instance": {
                    "id": event_manager.instance_id,
                    "name": event_manager.instance_name
                }
            }
        except Exception as e:
            logger.error(f"Error in create_event_endpoint: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/events", response_model=EventListResponse)
    async def get_events_endpoint(
            event_type: Optional[str] = Query(None, description="Filter by event type"),
            instance_id: Optional[str] = Query(None, description="Filter by instance id"),
            instance_name: Optional[str] = Query(None, description="Filter by instance name"),
            start_date: Optional[datetime] = Query(None, description="Lower date bound, ISO format"),
            end_date: Optional[datetime] = Query(None, description="Upper date bound, ISO format"),
            page: int = Query(1, ge=1, description="Page number"),
            page_size: int = Query(50, ge=1, le=500, description="Page size"),
            current_user: dict = Depends(get_current_admin)
    ):
        """Return events, filtered and paginated.

        Administrators only: the journal holds logins, agent connections and
        console entries of every user, which is the administrator's view of
        the platform and not the user's own.
        """
        try:
            result = await event_manager.get_events(
                event_type=event_type,
                instance_id=instance_id,
                instance_name=instance_name,
                start_date=start_date,
                end_date=end_date,
                page=page,
                page_size=page_size
            )

            return result
        except Exception as e:
            logger.error(f"Error in get_events_endpoint: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/events/types")
    async def get_event_types_endpoint(
            current_user: dict = Depends(get_current_admin)
    ):
        """Every type the filter may offer: seen in the journal, or declared.

        A list built only from what the table holds cannot offer a type that
        has never occurred -- which is exactly the one an administrator asks
        for when checking whether something happened at all. The declared
        audit types are therefore added to the observed ones.

        The declared types are supplied by the application through
        ``register_event_api_routes``; the framework does not import the
        application's audit catalogue.
        """
        try:
            declared = list(declared_event_types() or ()) if declared_event_types else []

            observed = await event_manager.get_event_types()
            event_types = sorted(set(observed) | set(declared))
            return {
                "event_types": event_types,
                "count": len(event_types)
            }
        except Exception as e:
            logger.error(f"Error in get_event_types_endpoint: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/events/instances")
    async def get_instances_endpoint(
            current_user: dict = Depends(get_current_admin)
    ):
        """Return every instance that has produced events (administrators only)."""
        try:
            instances = await event_manager.get_instances()
            return {
                "instances": instances,
                "count": len(instances),
                "current_instance": {
                    "id": event_manager.instance_id,
                    "name": event_manager.instance_name
                }
            }
        except Exception as e:
            logger.error(f"Error in get_instances_endpoint: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/events/cleanup")
    async def cleanup_old_events_endpoint(
            days: int = Query(30, ge=1, le=365, description="Delete events older than N days"),
            current_user: dict = Depends(get_current_admin)
    ):
        """Delete old events (administrators only)."""
        try:
            deleted_count = await event_manager.delete_old_events(days)
            return {
                "success": True,
                "deleted_count": deleted_count,
                "message": f"Deleted {deleted_count} events older than {days} days"
            }
        except Exception as e:
            logger.error(f"Error in cleanup_old_events_endpoint: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/events/stats")
    async def get_events_stats_endpoint(
            current_user: dict = Depends(get_current_admin)
    ):
        """Return extended event statistics (administrators only)."""
        try:
            stats = await event_manager.get_events_stats()
            return stats
        except Exception as e:
            logger.error(f"Error in get_events_stats_endpoint: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))


def init_event_manager():
    """Initialise the event manager at application start."""
    try:
        event_manager.init_table()
        logger.info(f"Event manager initialized successfully (instance: {event_manager.instance_name})")
        return event_manager
    except Exception as e:
        logger.error(f"Failed to initialize event manager: {str(e)}")
        return None