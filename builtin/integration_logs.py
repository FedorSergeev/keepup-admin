"""The integration log: a capability built on the new mechanics.

Task keepup-110. Writing to the integration log has worked for years and showing
it never did: the panel's section calls four routes that no module of the
framework ever registered, so the section was a storefront with no shop behind
it. The plugin here is the first capability assembled entirely from the
constructor -- it declares its table (it does not create it), declares the routes
it answers as data (not as FastAPI decorators), publishes a service so another
plugin can record a call without importing this one, and answers the four paths
the panel has always called.

The table's declaration still lives in `keepup/schema.py` and the writer is still
`keepup/integrations.py`: they travel into this plugin's own distribution in
keepup-124, and until then this file *uses* the one declaration rather than
writing a second one.
"""

import logging
from typing import Any, Dict, Optional

from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.plugins.base import BasePlugin
from keepup.schema import INTEGRATION_LOGS

logger = logging.getLogger(__name__)

__all__ = ["Integration_logsPlugin", "IntegrationLogService", "SERVICE_INTEGRATION_LOG"]

#: What another plugin asks for to record a call it made outside.
SERVICE_INTEGRATION_LOG = "integration_log"

#: How much of a body is kept: one large payload must not dominate the log.
BODY_LIMIT = 10_000


class IntegrationLogService:
    """Records calls to outside systems and reads them back.

    The service holds the data source and nothing else: which database it is,
    who may read the log and how long a record lives are not its business.
    """

    def __init__(self, datasource: Any):
        self.datasource = datasource

    async def record(self, host: str, endpoint: str, method: str = "GET",
                     user_id: Optional[int] = None, username: str = "system",
                     request_body: Any = None, response_body: Any = None,
                     status_code: Optional[int] = None,
                     duration_ms: Optional[int] = None) -> None:
        """Write one call to the log.

        Args:
            host: the system called.
            endpoint: what was called there.
            method: the method used.
            user_id: who caused the call, when somebody did.
            username: their name, or ``system`` for a call nobody made.
            request_body: what was sent, truncated.
            response_body: what came back, truncated.
            status_code: what the system answered.
            duration_ms: how long it took.
        """

        await self.datasource.execute_commit(
            "INSERT INTO integration_logs"
            " (user_id, username, host, endpoint, method, request_body, response_body,"
            "  status_code, duration_ms)"
            " VALUES (:user_id, :username, :host, :endpoint, :method, :request_body,"
            "  :response_body, :status_code, :duration_ms)",
            {
                "user_id": user_id or 0,
                "username": username or "system",
                "host": host,
                "endpoint": endpoint,
                "method": str(method).upper(),
                "request_body": _body(request_body),
                "response_body": _body(response_body),
                "status_code": status_code,
                "duration_ms": duration_ms,
            },
        )

    async def read(self, host: str = None, username: str = None, since: str = None,
                   until: str = None, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """The log, newest first, with what a panel page needs to show.

        Args:
            host: only calls to this system.
            username: only calls this person made.
            since: only calls at or after this moment.
            until: only calls at or before this moment.
            limit: how many to answer with.
            offset: how many to skip.

        Returns:
            The rows and how many there are in total.
        """
        where, params = _filters(host, username, since, until)
        rows = await self.datasource.execute(
            "SELECT * FROM integration_logs" + where +
            " ORDER BY created_at DESC LIMIT :limit OFFSET :offset",
            {**params, "limit": int(limit), "offset": int(offset)},
        )
        total = await self.datasource.execute(
            "SELECT COUNT(*) AS count FROM integration_logs" + where, params)
        return {
            "logs": [dict(row) for row in (rows or [])],
            "total": _total(total),
            "limit": int(limit),
            "offset": int(offset),
        }

    async def one(self, log_id: int) -> Optional[Dict[str, Any]]:
        """One record, or None when there is no such record."""
        rows = await self.datasource.execute(
            "SELECT * FROM integration_logs WHERE id = :id", {"id": int(log_id)})
        if not rows:
            return None
        return dict(rows[0])

    async def statistics(self, days: int = 7) -> Dict[str, Any]:
        """How many calls, by day, over the last few days."""
        rows = await self.datasource.execute(
            "SELECT DATE(created_at) AS day, COUNT(*) AS calls"
            " FROM integration_logs"
            " WHERE created_at >= :since"
            " GROUP BY DATE(created_at) ORDER BY day DESC",
            {"since": _days_ago(days)},
        )
        return {"days": int(days), "by_day": [dict(row) for row in (rows or [])]}

    async def cleanup(self, days: int = 30) -> Dict[str, Any]:
        """Forget records older than a few days.

        Returns:
            How many were forgotten.
        """
        rows = await self.datasource.execute(
            "SELECT COUNT(*) AS count FROM integration_logs WHERE created_at < :before",
            {"before": _days_ago(days)},
        )
        removed = _total(rows)
        await self.datasource.execute_commit(
            "DELETE FROM integration_logs WHERE created_at < :before",
            {"before": _days_ago(days)},
        )
        return {"removed": removed, "days": int(days)}


class Integration_logsPlugin(BasePlugin):
    """The capability: a table, a service, four routes and a panel section."""

    descriptor = PluginDescriptor(
        id="integration_logs",
        name="Integration log",
        version="0.4.0",
        distribution="keepup-integration-log",
        kind=KIND_OPTIONAL,
        priority=50,
        provides=("integration_log>=1",),
        requires=("datasource>=1",),
        wants=("users>=1",),
        contributions=("routes", "sections", "tables"),
    )

    def __init__(self, config=None):
        super().__init__("integration_logs", "Integration log", config)

    def register(self, services):
        """Publish the service when the data source is there, and ask for it.

        Args:
            services: the runtime's registry.
        """
        if services.has(SERVICE_DATASOURCE):
            services.provide(
                SERVICE_INTEGRATION_LOG,
                IntegrationLogService(services.require(SERVICE_DATASOURCE)),
                version=1, plugin_id="integration_logs")

    async def initialize(self):
        """Build the service on the data source the runtime resolved for us."""
        self.service = IntegrationLogService(self.services.require(SERVICE_DATASOURCE))
        return True

    def get_declared_tables(self):
        """The table this plugin owns, declared once, in schema.py, until keepup-124."""
        return [INTEGRATION_LOGS]

    def get_panel_sections(self):
        """The section the panel shows, as data rather than as a file entry."""
        return [{
            "id": "integration_logs",
            "name": "Integration log",
            "description": "Calls this application makes to outside systems",
            "js": "/keepup-static/modules/js/integration_logs.js",
            "css": "/keepup-static/modules/css/integration_logs.css",
            "initFunction": "initIntegrationLogs",
            "icon": "activity",
            "version": "1.0.0",
        }]

    def get_api_routes(self):
        """The four paths the panel has always called, and no others."""
        return [
            {"path": "/api/integration-logs", "methods": ["GET"],
             "handler": self.list_logs,
             "params": {
                 "host": {"type": "str", "in": "query"},
                 "username": {"type": "str", "in": "query"},
                 "since": {"type": "str", "in": "query"},
                 "until": {"type": "str", "in": "query"},
                 "limit": {"type": "int", "in": "query", "min": 1, "max": 500},
                 "offset": {"type": "int", "in": "query", "min": 0},
             }},
            {"path": "/api/integration-logs/stats", "methods": ["GET"],
             "handler": self.stats,
             "params": {"days": {"type": "int", "in": "query", "min": 1, "max": 365}}},
            {"path": "/api/integration-logs/cleanup", "methods": ["POST"],
             "handler": self.cleanup,
             "params": {"days": {"type": "int", "in": "query", "min": 1, "max": 3650}}},
            {"path": "/api/integration-logs/{log_id}", "methods": ["GET"],
             "handler": self.one_log,
             "params": {"log_id": {"type": "int", "in": "path"}}},
        ]

    def get_handlers(self):
        """What another plugin may call instead of importing this one."""
        return {"record": self.record, "service": self.service}

    async def record(self, host: str, endpoint: str, method: str = "GET", **kwargs) -> None:
        """Record a call this application made outside."""
        return await self.service.record(host, endpoint, method, **kwargs)

    async def list_logs(self, current_user=None, **filters):
        """The log, newest first."""
        return await self.service.read(**filters)

    async def stats(self, current_user=None, days: int = 7):
        """Calls by day."""
        return await self.service.statistics(days)

    async def cleanup(self, current_user=None, days: int = 30, request=None):
        """Forget the oldest records: the table had no retention at all."""
        return await self.service.cleanup(days)

    async def one_log(self, log_id: int, current_user=None):
        """One record."""
        from fastapi import HTTPException

        record = await self.service.one(log_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no such record")
        return record


def _body(value: Any) -> Optional[str]:
    """A body as the log keeps it: JSON, truncated, or nothing."""
    if value is None:
        return None
    import json

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[:BODY_LIMIT] + "... [truncated]" if len(text) > BODY_LIMIT else text


def _filters(host, username, since, until):
    """The WHERE clause the filters make, and its parameters."""
    clauses, params = [], {}
    for column, value in (("host", host), ("username", username)):
        if value:
            clauses.append(f" {column} = :{column}")
            params[column] = value
    if since:
        clauses.append(" created_at >= :since")
        params["since"] = since
    if until:
        clauses.append(" created_at <= :until")
        params["until"] = until
    return (" WHERE" + " AND".join(clauses)) if clauses else "", params


def _days_ago(days: int) -> str:
    """A moment this many days ago, as the log stores moments."""
    from datetime import datetime, timedelta

    return (datetime.utcnow() - timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")


def _total(rows) -> int:
    """The count from a COUNT(*) answer, whatever the driver called the column."""
    if not rows:
        return 0
    row = dict(rows[0])
    return int(next(iter(row.values()), 0) or 0)
