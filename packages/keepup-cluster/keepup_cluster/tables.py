"""The cluster's tables, declared where the capability lives.

Task keepup-117. `cluster_members` and `cluster_commands` were declared by the
kernel's `schema.py`, so every deployment created them whether or not it ran
more than one replica. `keepup_db.schema`, which re-exported them for one release,
is gone (keepup-158), which
is what a path written before the split still imports.
"""

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, Text

from keepup_db import tables

__all__ = ["CLUSTER_COMMANDS", "CLUSTER_MEMBERS"]

CLUSTER_MEMBERS = tables.table(
    "cluster_members",
    Column("instance_id", Text, primary_key=True, nullable=True),
    Column("instance_name", Text),
    Column("host", Text),
    Column("pid", Integer),
    Column("build", Text),
    Column("started_at", DateTime),
    Column("last_seen", DateTime),
    Column("state", Text, nullable=False, server_default="serving"),
    Column("can_restart", Boolean),
    Column("restart_blocker", Text),
    Column("plugins_running", Text),
    Column("plugins_pending", Text),
)

CLUSTER_COMMANDS = tables.table(
    "cluster_commands",
    tables.auto_id(),
    Column("instance_id", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("forced", Boolean),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("requested_by", Integer),
    Column("requested_by_name", Text),
    Column("requested_at", DateTime),
    Column("claimed_at", DateTime),
    Column("finished_at", DateTime),
    Column("result", Text),
    Index("idx_cluster_commands_target", "instance_id", "status"),
)
