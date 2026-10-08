"""The tables of the audit capability, declared where it lives.

Task keepup-124. `keepup.audit` and `keepup.events` re-export them for one
release, so the path that predates the catalogue still creates them and every
reader keeps working; the declarations belong to the capability that keeps the
rows -- the calls that came in and the events the application decided about.
"""

from sqlalchemy import (CheckConstraint, Column, DateTime, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.dialects import postgresql

from keepup_db import tables

__all__ = ["APP_EVENTS", "INCOMING_REQUESTS"]

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
