"""The audit: incoming calls, the application's events and the administrator's trail.

Task keepup-111. These live in three modules of the kernel -- ``audit.py``
(incoming requests, redaction, retention), ``events.py`` (the application's own
event log) and ``admin_trail.py`` (what an administrator decided) -- and all
three import SQLAlchemy, so the base bundle cannot be assembled without a
database library while they stay. The plugin here owns the two tables and
publishes the two services; the kernel's own trail and the route wrapper reach
them through the names (``keepup/kernel/observability.py``), not by importing
these modules.

The routes of the event log and the section that shows it travel with the panel
(keepup-104); what is here is the recording itself, its retention and the two
names.
"""

import logging
from typing import Any, Dict, List

from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.kernel.observability import SERVICE_AUDIT, SERVICE_EVENTS, Recording
from keepup.audit import INCOMING_REQUESTS
from keepup.events import APP_EVENTS
from keepup.plugins.base import BasePlugin

logger = logging.getLogger(__name__)

__all__ = ["AuditPlugin", "AuditRecording"]


class AuditRecording(Recording):
    """The framework's own recording, behind the kernel's two names.

    It delegates to the modules that already do the work (``keepup.audit``,
    ``keepup.events``) so that nothing changes for a deployment today; those
    modules travel into this capability's distribution in keepup-124.
    """

    def __init__(self, datasource: Any = None):
        from keepup.audit import IncomingRequestLogger, log_api_request

        super().__init__(start=log_api_request, end=IncomingRequestLogger.end_request,
                         name="keepup audit")

    def watch(self, emitter: Any) -> None:
        """Add another emitter, for a capability that keeps events of its own."""
        self.emitters.append(emitter)


class AuditPlugin(BasePlugin):
    """The capability: two tables, two services and the retention they need."""

    descriptor = PluginDescriptor(
        id="audit",
        name="Event audit",
        version="0.4.0",
        distribution="keepup-audit",
        kind=KIND_OPTIONAL,
        priority=60,
        provides=("audit>=1", "events>=1"),
        requires=("datasource>=1",),
        wants=("locks>=1", "scheduler>=1"),
        contributions=("routes", "sections", "tables", "events"),
    )

    def __init__(self, config=None):
        super().__init__("audit", "Event audit", config)

    def register(self, services):
        """Publish the recording where there is a data source to write it to.

        Args:
            services: the runtime's registry.
        """
        if services.has(SERVICE_DATASOURCE):
            self.recording = AuditRecording(services.require(SERVICE_DATASOURCE))
            services.provide(SERVICE_AUDIT, self.recording, version=1, plugin_id="audit")
            services.provide(SERVICE_EVENTS, self.recording, version=1, plugin_id="audit")

    async def initialize(self):
        """Keep the recording published at registration; build one if there is none."""
        if getattr(self, "recording", None) is None:
            self.recording = AuditRecording(self.services.require(SERVICE_DATASOURCE))
        return True

    def get_declared_tables(self):
        """The two tables this capability owns, declared once, until keepup-124."""
        return [INCOMING_REQUESTS, APP_EVENTS]

    def get_event_sinks(self) -> List[Any]:
        """What writes an event down, for the kernel's own trail."""
        return [self.emit]

    async def emit(self, event_type: str, payload: Dict[str, Any] = None) -> None:
        """Write one event through the module that still owns the table."""
        from keepup.events import emit_event

        await emit_event(event_type, payload or {})

    def get_api_routes(self):
        """No routes yet: the event log's own travel with the panel (keepup-104)."""
        return []

    def get_handlers(self):
        """What another plugin may reach instead of importing the audit."""
        return {"recording": self.recording, "emit": self.emit}
