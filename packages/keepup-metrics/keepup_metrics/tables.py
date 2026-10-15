"""The tables of the metrics capability, declared where it lives.

Task keepup-124. `keepup_db.schema` re-exported the snapshot table for one release
and is gone (keepup-158), so
the path that predates the catalogue still creates it and every reader keeps
working; the declaration belongs to the capability that keeps the rows.
"""

from sqlalchemy import Column, DateTime, Index, REAL, Text

from keepup_db import tables

__all__ = ["SYSTEM_METRICS"]

SYSTEM_METRICS = tables.table(
    "system_metrics",
    tables.auto_id(),
    Column("metric_name", Text, nullable=False),
    Column("metric_value", REAL, nullable=False),
    Column("timestamp", DateTime, server_default=tables.NOW),
    Column("app_instance", Text, server_default="main"),
    Column("tags", Text),
    Index("idx_metrics_timestamp", "timestamp"),
    Index("idx_metrics_name", "metric_name"),
)
