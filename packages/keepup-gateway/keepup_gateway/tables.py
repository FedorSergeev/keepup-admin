"""The table of the gateway, declared where the capability lives.

One table: the registry of services (`registry.py`). The gateway's routes from
the config and from this runtime are not stored -- the config and the plugins are
where they live.
"""

from sqlalchemy import Column, DateTime, Float, Index, String, Text

from keepup_db import tables

__all__ = ["GATEWAY_SERVICE"]

GATEWAY_SERVICE = tables.table(
    "gateway_service",
    Column("name", String(100), primary_key=True),
    Column("address", String(500), primary_key=True),
    Column("prefix", String(500), nullable=False),
    Column("route", Text, nullable=False),
    Column("instance", String(200)),
    Column("ttl", Float, nullable=False),
    Column("registered_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Index("idx_gateway_service_expires", "expires_at"),
)
