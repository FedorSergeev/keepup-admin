"""How long system metric snapshots live.

A snapshot is written once per collection interval -- twenty-five values per
replica every fifteen seconds, some hundred and forty thousand rows a day. Very
little of it is ever read: a summary of the last few minutes, and a day of CPU
and memory history. So a snapshot lives in three stages: in full detail for the
first hours, then thinned to one point per interval, then deleted outright.

The sweep only touches the names the collector writes. The same table holds
occasional administrative events, and the rule "one point a minute" -- sensible
for a measurement -- would erase the record of who did what.

In full: `doc/cpu_ram_metrics.md`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from keepup.db import DatabaseManagerV2
from keepup.metrics import PANEL_HISTORY_HOURS, SNAPSHOT_METRIC_NAMES

logger = logging.getLogger(__name__)

TABLE = "system_metrics"
LOCK_NAME = "metrics_retention"

# --- the retention rule ------------------------------------------------------

#: How many hours snapshots are kept in full detail. Forty-eight rather than
#: twenty-four precisely because the panel shows a day: there has to be slack
#: between what is read and what is swept, or the sweep and the chart will one
#: day meet on the same boundary.
DEFAULT_DETAILED_HOURS = 48
#: After how many days a snapshot is deleted outright.
DEFAULT_RETENTION_DAYS = 30
#: The rate old snapshots are thinned down to. On a month-wide chart the
#: difference between fifteen seconds and a minute is invisible, and there are
#: four times fewer rows.
DEFAULT_THINNED_INTERVAL_SECONDS = 60
#: How often a sweep runs.
DEFAULT_SWEEP_INTERVAL_SECONDS = 3600

#: How many candidate rows are read at a time.
CANDIDATE_BATCH_ROWS = 5000
#: How many ids go into one delete statement. Deleting millions of rows in a
#: single statement would hold up the writing of the next snapshot.
DELETE_CHUNK_ROWS = 500
#: How many batches one pass works through. This is what turns clearing a
#: backlog into several short passes instead of one long one.
MAX_BATCHES_PER_PASS = 20
#: How long to wait after a failed pass.
ERROR_BACKOFF_SECONDS = 60


def _positive(name: str, default: int) -> int:
    """A number from the environment, or the default.

    Rubbish in the variable must neither stop the start nor switch the sweep
    off silently: a value that cannot be read means the default and a line in
    the log.

    Args:
        name: the environment variable to read.
        default: what to use when it is absent or unusable.

    Returns:
        The positive integer to use.
    """
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(f"{name}={raw!r} is not a number, using {default}")
        return default
    if value <= 0:
        logger.warning(f"{name}={value} must be positive, using {default}")
        return default
    return value


@dataclass(frozen=True)
class RetentionPolicy:
    """How long snapshots are kept, at each stage."""

    detailed_hours: int = DEFAULT_DETAILED_HOURS
    retention_days: int = DEFAULT_RETENTION_DAYS
    thinned_interval_seconds: int = DEFAULT_THINNED_INTERVAL_SECONDS
    sweep_interval_seconds: int = DEFAULT_SWEEP_INTERVAL_SECONDS

    def __post_init__(self):
        # A detailed window narrower than the period the panel shows would
        # mean the sweep eating the chart in front of the administrator. Such a
        # value is not accepted -- it is raised to the panel's period, and that
        # is said out loud.
        if self.detailed_hours < PANEL_HISTORY_HOURS:
            logger.warning(
                f"Detailed window {self.detailed_hours}h is narrower than the "
                f"{PANEL_HISTORY_HOURS}h the panel shows, raising it")
            object.__setattr__(self, "detailed_hours", PANEL_HISTORY_HOURS)

    @classmethod
    def from_env(cls) -> "RetentionPolicy":
        return cls(
            detailed_hours=_positive("METRICS_DETAILED_HOURS", DEFAULT_DETAILED_HOURS),
            retention_days=_positive("METRICS_RETENTION_DAYS", DEFAULT_RETENTION_DAYS),
            thinned_interval_seconds=_positive(
                "METRICS_THINNED_INTERVAL_SECONDS", DEFAULT_THINNED_INTERVAL_SECONDS),
            sweep_interval_seconds=_positive(
                "METRICS_RETENTION_INTERVAL_SECONDS", DEFAULT_SWEEP_INTERVAL_SECONDS),
        )

    def horizons(self, now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
        """The two boundaries: the detailed window and the retention limit.

        Args:
            now: the moment to measure from; defaults to the current time.

        Returns:
            A pair (detailed_from, expired_before).
        """
        now = now or datetime.utcnow()
        return (now - timedelta(hours=self.detailed_hours),
                now - timedelta(days=self.retention_days))



# --- the sweep ---------------------------------------------------------------

#: The collector's names as a query fragment and a parameter set. Built once:
#: the list does not change, and assembling it per query would mean building
#: twenty-five parameters twenty times a pass.
_NAME_PARAMS: Dict[str, str] = {
    f"n{index}": name for index, name in enumerate(sorted(SNAPSHOT_METRIC_NAMES))
}
_NAME_CLAUSE = ", ".join(f":{key}" for key in _NAME_PARAMS)


def _as_datetime(value: Any) -> Optional[datetime]:
    """A row's timestamp as a datetime.

    On SQLite a DATETIME column arrives as a string, on PostgreSQL as a time.
    The bucket is computed in code, so the difference between the engines is
    settled here rather than in two variants of a query that will one day drift
    apart.

    Args:
        value: whatever the driver returned for the column.

    Returns:
        The datetime, or None if it could not be read.
    """
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        for shape in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value, shape)
            except ValueError:
                continue
    return None


_EPOCH = datetime(1970, 1, 1)


def _bucket(row: Dict[str, Any], interval_seconds: int) -> Optional[tuple]:
    """The row's bucket: metric, instance and slice of time.

    Args:
        row: the row as the driver returned it.
        interval_seconds: the width of a slice.

    Returns:
        The bucket key, or None if the row carries no readable timestamp.
    """
    moment = _as_datetime(row.get("timestamp"))
    if moment is None:
        return None
    slot = int((moment - _EPOCH).total_seconds()) // interval_seconds
    return (row.get("metric_name"), row.get("app_instance"), slot)


def _delete_ids(ids: Sequence[Any]) -> int:
    """Delete rows by id, in batches of a bounded size.

    Args:
        ids: the ids to remove.

    Returns:
        How many rows were deleted.
    """
    removed = 0
    for start in range(0, len(ids), DELETE_CHUNK_ROWS):
        chunk = ids[start:start + DELETE_CHUNK_ROWS]
        params = {f"i{index}": value for index, value in enumerate(chunk)}
        placeholders = ", ".join(f":{key}" for key in params)
        DatabaseManagerV2.execute_commit(
            f"DELETE FROM {TABLE} WHERE id IN ({placeholders})", params)
        removed += len(chunk)
    return removed


def purge_expired(policy: RetentionPolicy, now: Optional[datetime] = None) -> int:
    """Delete snapshots older than the retention limit.

    Args:
        policy: the retention policy in force.
        now: the moment to measure from; defaults to the current time.

    Returns:
        How many rows were deleted.
    """
    _, expired_before = policy.horizons(now)
    removed = 0
    for _ in range(MAX_BATCHES_PER_PASS):
        rows = DatabaseManagerV2.execute(
            f"SELECT id FROM {TABLE} WHERE timestamp < :before "
            f"AND metric_name IN ({_NAME_CLAUSE}) "
            f"ORDER BY timestamp ASC LIMIT :limit",
            {"before": expired_before, "limit": CANDIDATE_BATCH_ROWS, **_NAME_PARAMS})
        if not rows:
            break
        removed += _delete_ids([row["id"] for row in rows])
        if len(rows) < CANDIDATE_BATCH_ROWS:
            break
    return removed


def thin_old(policy: RetentionPolicy, now: Optional[datetime] = None) -> int:
    """Thin snapshots beyond the detailed window down to one point per bucket.

    Candidates are read newest first: what needs thinning is what has just
    crossed out of the detailed window -- everything older was thinned by
    earlier passes. Reading oldest first, every pass would work through the
    already thinned tail and never reach the dense edge.

    Args:
        policy: the retention policy in force.
        now: the moment to measure from; defaults to the current time.

    Returns:
        How many rows were deleted.
    """
    detailed_from, expired_before = policy.horizons(now)
    interval = policy.thinned_interval_seconds
    removed = 0

    for _ in range(MAX_BATCHES_PER_PASS):
        rows = DatabaseManagerV2.execute(
            f"SELECT id, metric_name, app_instance, timestamp FROM {TABLE} "
            f"WHERE timestamp >= :since AND timestamp < :before "
            f"AND metric_name IN ({_NAME_CLAUSE}) "
            f"ORDER BY timestamp DESC, id DESC LIMIT :limit",
            {"since": expired_before, "before": detailed_from,
             "limit": CANDIDATE_BATCH_ROWS, **_NAME_PARAMS})
        if not rows:
            break

        # The batch may have ended in the middle of a bucket: the rest of the
        # oldest row's bucket lies past its edge. Such a bucket is left to the
        # next pass -- otherwise what is in fact the only row of it would be
        # deleted.
        truncated = _bucket(rows[-1], interval) if len(rows) == CANDIDATE_BATCH_ROWS else None

        kept: Dict[tuple, Dict[str, Any]] = {}
        doomed: List[Any] = []
        for row in rows:
            bucket = _bucket(row, interval)
            if bucket is None or bucket == truncated:
                continue
            best = kept.get(bucket)
            if best is None:
                kept[bucket] = row
                continue
            # The earliest row of the bucket is what stays, the lower id
            # breaking a tie: the choice is unambiguous, which is why running
            # the pass again changes nothing.
            if _order(row) < _order(best):
                kept[bucket] = row
                doomed.append(best["id"])
            else:
                doomed.append(row["id"])

        removed += _delete_ids(doomed)
        if len(rows) < CANDIDATE_BATCH_ROWS:
            break
    return removed


def _order(row: Dict[str, Any]) -> tuple:
    moment = _as_datetime(row.get("timestamp")) or _EPOCH
    return (moment, row.get("id"))


def run_sweep(policy: Optional[RetentionPolicy] = None,
              now: Optional[datetime] = None) -> Dict[str, int]:
    """One sweep: delete what has expired, then thin what is left.

    In that order: thinning what is about to be deleted is work for nothing.

    Neither half cancels the other -- they answer for different ages, and a
    failure while deleting must not leave the table unthinned as well.

    Args:
        policy: the retention policy; read from the environment when omitted.
        now: the moment to measure from; defaults to the current time.

    Returns:
        How many rows expired and how many were thinned.
    """
    policy = policy or RetentionPolicy.from_env()

    expired = 0
    try:
        expired = purge_expired(policy, now)
    except Exception as e:
        logger.error(f"Could not purge expired metric snapshots: {e}")

    thinned = 0
    try:
        thinned = thin_old(policy, now)
    except Exception as e:
        logger.error(f"Could not thin old metric snapshots: {e}")

    return {"expired": expired, "thinned": thinned}



# --- the background loop -----------------------------------------------------


async def metrics_retention_background(policy: Optional[RetentionPolicy] = None):
    """A sweep once per interval, under a distributed lock.

    Locked because there are several replicas and the work is shared: three
    replicas deleting the same rows at once make three times the queries and
    not one row fewer.

    A failed pass costs nothing: collecting and writing snapshots runs on its
    own loop and knows nothing of the sweep.

    Args:
        policy: the retention policy; read from the environment when omitted.
    """
    from keepup.locks import distributed_lock

    policy = policy or RetentionPolicy.from_env()
    while True:
        try:
            async with distributed_lock(LOCK_NAME, timeout=5,
                                        max_lock_time=policy.sweep_interval_seconds):
                swept = await asyncio.to_thread(run_sweep, policy)
            if swept["expired"] or swept["thinned"]:
                logger.info(
                    f"Metric snapshots: {swept['expired']} expired, "
                    f"{swept['thinned']} thinned")
            await asyncio.sleep(policy.sweep_interval_seconds)
        except asyncio.CancelledError:
            # Shutting the application down is not a failure: without this
            # branch, cancelling the task would log an error on every stop.
            raise
        except Exception as e:
            logger.error(f"Error in metrics retention sweep: {e}")
            await asyncio.sleep(ERROR_BACKOFF_SECONDS)
