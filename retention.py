"""How long the framework's journals keep their rows, and the sweep that holds it.

The request audit (``keepup/audit.py``) and the application event log
(``keepup/events.py``) grow with every request and every event, and neither is
the place a deployment keeps its history: a journal that is never swept grows
for as long as the deployment runs. Both keep rows for a number of days read
from the environment, and both delete older ones in the same way:

- **in chunks**, so a backlog of months is cleared by several short statements
  rather than one long one that holds up whoever writes to the table next;
- **a bounded number of chunks per pass**, so one pass never runs for long;
- **under a distributed lock** (by the caller), because the work is shared
  across replicas: three of them deleting the same rows make three times the
  statements and not one row fewer.

The metric snapshots have a policy of their own (``keepup/metrics_retention.py``):
they are thinned as well as expired, which a journal is not.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from keepup.db import DatabaseManagerV2

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "purge_older_than",
    "retention_days",
]

logger = logging.getLogger(__name__)


def retention_days(variable: str, default: int) -> int:
    """How many days rows are kept, from ``variable`` in the environment or the default.

    Rubbish in the variable means the default and a line in the log: a sweep
    that switched itself off silently would be worse than one that swept too much.
    """
    raw = os.getenv(variable)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r is not a number, using %s", variable, raw, default)
        return default
    if value <= 0:
        logger.warning("%s=%s must be positive, using %s", variable, value, default)
        return default
    return value


def purge_older_than(table: str, column: str, days: int, chunk_rows: int,
                     max_chunks: int, now: Optional[datetime] = None) -> int:
    """Delete rows of ``table`` whose ``column`` is older than ``days``, oldest first.

    ``table`` and ``column`` are the framework's own names, never a caller's
    input. The boundary is computed here rather than in the statement: interval
    syntax differs between the engines.

    Returns:
        How many rows were deleted in this pass.
    """
    cutoff = (now or datetime.utcnow()) - timedelta(days=days)
    removed = 0
    for _ in range(max_chunks):
        rows = DatabaseManagerV2.execute(
            f"SELECT id FROM {table} WHERE {column} < :cutoff "
            f"ORDER BY {column} ASC LIMIT :limit",
            {"cutoff": cutoff, "limit": chunk_rows})
        if not rows:
            break
        ids = {f"i{index}": row["id"] for index, row in enumerate(rows)}
        placeholders = ", ".join(f":{key}" for key in ids)
        DatabaseManagerV2.execute_commit(
            f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)
        removed += len(rows)
        if len(rows) < chunk_rows:
            break
    return removed
