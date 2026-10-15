"""The time the framework stores: UTC, without a timezone.

Task keepup-161. `datetime.utcnow()` and `datetime.utcfromtimestamp()` are
deprecated as of Python 3.12 and scheduled for removal; every one of the forty
odd calls was also a place that could have meant local time by accident.

The shape stays naive on purpose. Every timestamp column of every capability is
a naive `DateTime` on both dialects, PostgreSQL and SQLite return naive values,
and a value with a timezone does not compare with one without -- so a change to
aware datetimes is a schema-and-comparison change, not a cleanup. What changes
here is only how the naive UTC value is produced.
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["utcnow", "utcfromtimestamp"]


def utcnow() -> datetime:
    """Now, in UTC, without a timezone."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcfromtimestamp(timestamp: float) -> datetime:
    """The moment a POSIX timestamp names, in UTC, without a timezone."""
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)
