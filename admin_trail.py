"""What an administrator changed, written into the event log (keepup-67).

The event log is where a deployment looks for who did what, and the framework's
own administrative surface left nothing there: purging the log, changing the
panel's sections, their grants and the themes, and switching a plugin's
decision were visible only in the application log -- which rotates, is shipped
elsewhere or nowhere, and is not what an operator reads. Each of those now
writes one event naming the administrator.

The types below are the framework's. Neither they nor the types an application
declares can be created by hand through `POST /api/events`: a journal the
watched party can fill with lookalikes is not evidence (keepup/events_api.py).

Writing the trail never fails the action it records: the action has already
happened, and answering an error for it would only invite a repeat. A trail
that could not be written is logged as an error.
"""

import logging
from typing import Any, Dict, Optional

from keepup import events

logger = logging.getLogger(__name__)

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "ADMIN_EVENT_TYPES",
    "EVENTS_PURGED",
    "PLUGIN_DECISION_CHANGED",
    "SECTIONS_CHANGED",
    "THEMES_CHANGED",
    "record",
]

EVENTS_PURGED = "admin_events_purged"
SECTIONS_CHANGED = "admin_sections_changed"
THEMES_CHANGED = "admin_themes_changed"
PLUGIN_DECISION_CHANGED = "admin_plugin_decision_changed"

ADMIN_EVENT_TYPES = (EVENTS_PURGED, SECTIONS_CHANGED, THEMES_CHANGED, PLUGIN_DECISION_CHANGED)


def _author(admin: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    admin = admin or {}
    return {"user_id": admin.get("id"), "username": admin.get("username")}


async def record(event_type: str, admin: Optional[Dict[str, Any]], text: str,
                 **details: Any) -> Optional[int]:
    """Write one administrative event; the id, or None when it could not be written."""
    data = {"by": _author(admin), **details}
    try:
        return await events.event_manager.create_event(
            event_type=event_type, event_text=text, event_data=data)
    except Exception as error:
        logger.error(f"Could not record {event_type} by {data['by'].get('username')}: {error}")
        return None

