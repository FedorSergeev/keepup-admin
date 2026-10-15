"""The table the locks service keeps its rows in.

Task keepup-158. The declaration used to live in `keepup_db.schema`, on the
0.3.0 path an application called by hand -- which meant a runtime assembled from
plugins, the way the kernel does it, created no `distributed_locks` table at all:
a project built that way served its panel and then reported `no such table:
distributed_locks` on every background sweep.

The table belongs to the capability that provides the service that uses it.
`keepup.locks` reaches it with plain SQL, so the declaration here is the only
thing that has to exist.
"""

from sqlalchemy import Column, DateTime, Text

from keepup_db import tables

__all__ = ["DISTRIBUTED_LOCKS"]


DISTRIBUTED_LOCKS = tables.table(
    "distributed_locks",
    # A text key written as ``TEXT PRIMARY KEY``: SQLite leaves such a column
    # nullable, and the declaration keeps it that way.
    Column("lock_name", Text, primary_key=True, nullable=True),
    Column("acquired_at", DateTime, nullable=False),
    Column("instance_id", Text, nullable=False),
)
