"""The HTTP routes of the application's event log.

The journal itself -- the table, writing, reading, retention -- is
`keepup/events.py`; these routes are one reader of it (keepup-23). Every route
is for administrators: the journal holds sign-ins, agent connections and
console entries of every user. See `doc/event_manager.md`.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from keepup.auth.dependencies import get_current_admin
from keepup import admin_trail, events
from keepup.logging_setup import for_log

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "register_event_api_routes",
]

# The journal's manager is looked up on its module at each call
# (``events.event_manager``), not bound here: whoever replaces the journal's
# manager -- an application, a test -- replaces it for the routes too.

logger = logging.getLogger(__name__)

#: What a failed route answers. The exception's own text -- a database message
#: naming tables, values and sometimes a query -- went to the client and to the
#: audit as it was (keepup-76); it goes to the application log instead.
INTERNAL_ERROR = "Internal server error; see the application log"


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
        """Create an event by hand.

        Administrators only. Audit events are written by the server itself at
        the point the action happens (the application's audit helper), never
        accepted over HTTP: a journal the watched party can fill is not
        evidence. So a type the application declares, or one the framework
        writes itself, is refused here, and what is accepted carries who wrote
        it by hand (keepup-67). The event is attached to the current instance.
        """
        reserved = set(admin_trail.ADMIN_EVENT_TYPES)
        if declared_event_types:
            reserved.update(declared_event_types() or ())
        if event.event_type in reserved:
            raise HTTPException(
                status_code=400,
                detail="This event type is written by the application itself and "
                       "cannot be created by hand.")
        data = dict(event.event_data or {})
        data["manual"] = {"user_id": current_user.get("id"),
                          "username": current_user.get("username")}
        try:
            event_id = await events.event_manager.create_event(
                event_type=event.event_type,
                event_text=event.event_text,
                event_data=data
            )

            return {
                "success": True,
                "event_id": event_id,
                "message": "Event created successfully",
                "instance": {
                    "id": events.event_manager.instance_id,
                    "name": events.event_manager.instance_name
                }
            }
        except Exception as e:
            logger.error("Error in create_event_endpoint: %s", for_log(e))
            raise HTTPException(status_code=500, detail=INTERNAL_ERROR)

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
            result = await events.event_manager.get_events(
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
            logger.error("Error in get_events_endpoint: %s", for_log(e))
            raise HTTPException(status_code=500, detail=INTERNAL_ERROR)

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

            observed = await events.event_manager.get_event_types()
            event_types = sorted(set(observed) | set(declared))
            return {
                "event_types": event_types,
                "count": len(event_types)
            }
        except Exception as e:
            logger.error("Error in get_event_types_endpoint: %s", for_log(e))
            raise HTTPException(status_code=500, detail=INTERNAL_ERROR)

    @app.get("/api/events/instances")
    async def get_instances_endpoint(
            current_user: dict = Depends(get_current_admin)
    ):
        """Return every instance that has produced events (administrators only)."""
        try:
            instances = await events.event_manager.get_instances()
            return {
                "instances": instances,
                "count": len(instances),
                "current_instance": {
                    "id": events.event_manager.instance_id,
                    "name": events.event_manager.instance_name
                }
            }
        except Exception as e:
            logger.error("Error in get_instances_endpoint: %s", for_log(e))
            raise HTTPException(status_code=500, detail=INTERNAL_ERROR)

    @app.delete("/api/events/cleanup")
    async def cleanup_old_events_endpoint(
            days: int = Query(30, ge=1, le=365, description="Delete events older than N days"),
            current_user: dict = Depends(get_current_admin)
    ):
        """Delete old events (administrators only).

        The purge itself is written into the log afterwards, so what it
        removed is not all that is left of it (keepup-67).
        """
        try:
            deleted_count = await events.event_manager.delete_old_events(days)
            await admin_trail.record(
                admin_trail.EVENTS_PURGED, current_user,
                f"Deleted {deleted_count} events older than {days} days",
                days=days, deleted_count=deleted_count)
            return {
                "success": True,
                "deleted_count": deleted_count,
                "message": f"Deleted {deleted_count} events older than {days} days"
            }
        except Exception as e:
            logger.error("Error in cleanup_old_events_endpoint: %s", for_log(e))
            raise HTTPException(status_code=500, detail=INTERNAL_ERROR)

    @app.get("/api/events/stats")
    async def get_events_stats_endpoint(
            current_user: dict = Depends(get_current_admin)
    ):
        """Return extended event statistics (administrators only)."""
        try:
            stats = await events.event_manager.get_events_stats()
            return stats
        except Exception as e:
            logger.error("Error in get_events_stats_endpoint: %s", for_log(e))
            raise HTTPException(status_code=500, detail=INTERNAL_ERROR)
