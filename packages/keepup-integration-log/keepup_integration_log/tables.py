"""The tables of the integration log, declared where the capability lives.

Task keepup-124. `keepup.schema` no longer declares it: a table belongs to the
capability that keeps it, and the capability's plugin offers the declaration to
the abstraction that creates it (keepup-106, keepup-123).
"""

from sqlalchemy import Column, DateTime, Index, Integer, Text

from keepup_db import tables

__all__ = ["INTEGRATION_LOGS"]

INTEGRATION_LOGS = tables.table(
    "integration_logs",
    tables.auto_id(),
    Column("user_id", Integer, nullable=False),
    Column("username", Text, nullable=False),
    Column("host", Text, nullable=False),
    Column("endpoint", Text, nullable=False),
    Column("method", Text, nullable=False),
    Column("request_body", Text),
    Column("response_body", Text),
    Column("status_code", Integer),
    Column("duration_ms", Integer),
    Column("created_at", DateTime, server_default=tables.NOW),
    tables.foreign_key("user_id", "users", ("id",)),
    Index("idx_integration_logs_user_id", "user_id"),
    Index("idx_integration_logs_created_at", "created_at"),
    Index("idx_integration_logs_host", "host"),
)
