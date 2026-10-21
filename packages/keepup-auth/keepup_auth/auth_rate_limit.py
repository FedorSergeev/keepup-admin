"""How often one address may knock on the sign-in's doors.

The per-name lockout in :mod:`keepup_auth.login_throttle` answers "is this
password being guessed"; it cannot answer "is one address spraying one password
across a thousand names", because each name has its own counter and never
reaches the limit. An autonomous attacker does exactly that, and every attempt
that reaches the password check costs a quarter of a second of bcrypt -- so the
address and the whole deployment get a window of their own (keepup-171).

Two buckets, both counted in the database so every replica shares them:

* ``ip:<address>`` -- what one caller may spend in a minute;
* ``all`` -- what the deployment may spend in a minute, however many addresses
  the caller has.

The window is a wall-clock minute, counted with one atomic statement. A limit of
0 switches that bucket off; a database that cannot be read fails open, because
a limiter that locks everybody out when its own table is unavailable is worse
than the flood it was meant to stop.
"""

import logging
import os
import random
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException, Request, status
from sqlalchemy import text

from keepup import clock
from keepup_db import DatabaseManagerV2

logger = logging.getLogger(__name__)

__all__ = [
    "PER_MINUTE_ENV",
    "GLOBAL_PER_MINUTE_ENV",
    "DEFAULT_PER_MINUTE",
    "DEFAULT_GLOBAL_PER_MINUTE",
    "check",
    "limit_for",
    "global_limit_for",
]

#: How many sign-in calls one address may make in a minute. The old per-name
#: lockout allowed ten failures in fifteen minutes for one name; this is the
#: ceiling for one address across every name it tries.
PER_MINUTE_ENV = "AUTH_RATE_LIMIT_PER_MINUTE"
DEFAULT_PER_MINUTE = 30

#: How many sign-in calls the whole deployment may make in a minute. High
#: enough for a busy panel behind one address, low enough that a flood is not
#: free; a deployment with a real load raises it rather than removing it.
GLOBAL_PER_MINUTE_ENV = "AUTH_RATE_LIMIT_GLOBAL_PER_MINUTE"
DEFAULT_GLOBAL_PER_MINUTE = 600

#: How long a spent window is kept: enough to read an incident afterwards.
KEEP = timedelta(minutes=5)

#: One statement, both dialects: the count is kept by the database, not read and
#: written back by the process (the same reason as the login attempt counter).
_HIT = (
    "INSERT INTO auth_rate_limit (bucket, window_start, hits) "
    "VALUES (:bucket, :window_start, 1) "
    "ON CONFLICT (bucket, window_start) DO UPDATE SET "
    "hits = auth_rate_limit.hits + 1"
)


def _configured(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def limit_for() -> int:
    """What one address may spend in a minute; 0 means the bucket is off."""
    return _configured(PER_MINUTE_ENV, DEFAULT_PER_MINUTE)


def global_limit_for() -> int:
    """What the deployment may spend in a minute; 0 means the bucket is off."""
    return _configured(GLOBAL_PER_MINUTE_ENV, DEFAULT_GLOBAL_PER_MINUTE)


def _window(now: datetime) -> datetime:
    """The minute this call belongs to, as a whole timestamp."""
    return now.replace(second=0, microsecond=0)


def _hit(bucket: str, window_start: datetime) -> int:
    """Count this call and return how many the bucket holds now."""
    try:
        with DatabaseManagerV2.get_session() as session:
            row = session.execute(
                text(_HIT + " RETURNING hits"),
                {"bucket": bucket, "window_start": window_start}).fetchone()
        return int(row[0]) if row else 0
    except Exception as error:
        logger.warning(f"Could not count a sign-in call for {bucket}: {error}")
        return 0


def _refused(window_start: datetime, now: datetime) -> HTTPException:
    retry = max(1, int((window_start + timedelta(minutes=1) - now).total_seconds()))
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many sign-in attempts. Try again later.",
        headers={"Retry-After": str(retry)},
    )


def _purge(now: datetime) -> None:
    """Drop windows nobody will read again, now and then rather than every call."""
    if random.random() > 0.01:
        return
    try:
        DatabaseManagerV2.execute_commit(
            "DELETE FROM auth_rate_limit WHERE window_start < :cutoff",
            {"cutoff": _window(now) - KEEP})
    except Exception as error:
        logger.warning(f"Could not purge the sign-in rate limit table: {error}")


def check(request: Request, now: Optional[datetime] = None) -> None:
    """Refuse this sign-in call when its address or the deployment is spent.

    Args:
        request: the request being answered; its client address is the caller.
        now: what "now" is, for a check that does not want the clock.

    Raises:
        HTTPException: 429 with ``Retry-After`` when either bucket is over.
    """
    now = now or clock.utcnow()
    window_start = _window(now)
    address = request.client.host if request.client else "unknown"

    per_address = limit_for()
    if per_address and _hit(f"ip:{address}", window_start) > per_address:
        _purge(now)
        raise _refused(window_start, now)

    overall = global_limit_for()
    if overall and _hit("all", window_start) > overall:
        _purge(now)
        raise _refused(window_start, now)

    _purge(now)
