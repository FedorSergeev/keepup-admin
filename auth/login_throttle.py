"""Counting failed logins, so a password cannot be guessed at full speed.

The counter lives in the database rather than in the process. The application is
meant to run as several replicas against one database, and a counter held in
memory is one the next attempt walks around simply by landing on another replica.

Attempts are counted per username, not per address. An address behind a shared
uplink belongs to many people, and locking it out shuts the door on everyone who
happens to share it. The trade is that somebody can keep a known name locked for
the length of the window; that window is short, and the alternative is worse.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import Column, DateTime, Integer, String, Text, text

from keepup import tables
from keepup.db import DatabaseManagerV2

logger = logging.getLogger(__name__)

#: How many failures in a row close the door, and for how long.
MAX_ATTEMPTS_ENV = "LOGIN_MAX_ATTEMPTS"
LOCKOUT_MINUTES_ENV = "LOGIN_LOCKOUT_MINUTES"
DEFAULT_MAX_ATTEMPTS = 10
DEFAULT_LOCKOUT_MINUTES = 15


def max_attempts(environ=None) -> int:
    """Failures tolerated before the lockout window starts."""
    environ = environ if environ is not None else os.environ
    try:
        value = int(environ.get(MAX_ATTEMPTS_ENV, DEFAULT_MAX_ATTEMPTS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_ATTEMPTS
    return value if value > 0 else DEFAULT_MAX_ATTEMPTS


def lockout_window(environ=None) -> timedelta:
    """How long the door stays closed once the attempts are spent."""
    environ = environ if environ is not None else os.environ
    try:
        minutes = int(environ.get(LOCKOUT_MINUTES_ENV, DEFAULT_LOCKOUT_MINUTES))
    except (TypeError, ValueError):
        minutes = DEFAULT_LOCKOUT_MINUTES
    return timedelta(minutes=minutes if minutes > 0 else DEFAULT_LOCKOUT_MINUTES)


LOGIN_ATTEMPTS = tables.table(
    "login_attempts",
    Column("username", String(255).with_variant(Text(), "sqlite"), primary_key=True, nullable=True),
    Column("failures", Integer, nullable=False, server_default=tables.sql_text("0")),
    Column("last_failure_at", DateTime),
)


def init_login_attempts_table() -> None:
    """Create the table this module keeps its count in.

    There are no migrations: each start creates what is missing.
    """
    tables.ensure_tables(LOGIN_ATTEMPTS)


def _row(username: str) -> Optional[dict]:
    return DatabaseManagerV2.execute_one(
        "SELECT username, failures, last_failure_at FROM login_attempts WHERE username = :username",
        {"username": username}
    )


def _as_datetime(value) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def locked_until(username: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """When this name may try again, or None when it may try now."""
    if not username:
        return None

    now = now or datetime.utcnow()
    try:
        row = _row(username)
    except Exception as error:
        # A throttle that cannot read its own table must not become the reason
        # nobody can log in.
        logger.warning(f"Could not read the login attempts of {username}: {error}")
        return None

    if not row or (row.get("failures") or 0) < max_attempts():
        return None

    last_failure = _as_datetime(row.get("last_failure_at"))
    if last_failure is None:
        return None

    until = last_failure + lockout_window()
    return until if until > now else None


#: One statement, the same on both dialects (PostgreSQL 9.5+, SQLite 3.24+), so
#: the count is kept by the database rather than read, incremented here and
#: written back. The read-modify-write lost attempts: failures arriving at once
#: on several replicas read the same number and each wrote it plus one, and two
#: first failures both tried to insert, the loser's error swallowed -- so the
#: lockout came later than the setting said, which is the one thing a throttle
#: against guessing must not do (keepup-48). A name whose window has passed
#: starts its count again, so old failures do not add up to a lockout weeks later.
_RECORD_FAILURE = (
    "INSERT INTO login_attempts (username, failures, last_failure_at) "
    "VALUES (:username, 1, :now) "
    "ON CONFLICT (username) DO UPDATE SET "
    "failures = CASE WHEN login_attempts.last_failure_at IS NOT NULL "
    "AND login_attempts.last_failure_at <= :window_start "
    "THEN 1 ELSE login_attempts.failures + 1 END, "
    "last_failure_at = :now"
)


def record_failure(username: str, now: Optional[datetime] = None) -> None:
    """Count one failed attempt against this name, atomically."""
    if not username:
        return

    now = now or datetime.utcnow()
    try:
        DatabaseManagerV2.execute_commit(
            _RECORD_FAILURE,
            {"username": username, "now": now, "window_start": now - lockout_window()})
    except Exception as error:
        logger.warning(f"Could not record a failed login of {username}: {error}")


def reserve_attempt(username: str, now: Optional[datetime] = None) -> int:
    """Count this attempt before the password is checked; how many there are now.

    The check and the count used to be separated by the password check -- a
    quarter of a second of bcrypt -- so attempts sent at once all passed the
    check before any of them was counted: forty in parallel against a limit of
    ten were all answered 401 and none 429 (audit keepup-52, finding 3). Counted
    first, attempts are numbered in the order the database takes them, and the
    ones past the limit are refused without their password being looked at. A
    successful sign-in clears the count as before.

    Returns 0 when the count could not be kept: a throttle that cannot write its
    own table must not become the reason nobody can sign in.
    """
    if not username:
        return 0
    now = now or datetime.utcnow()
    try:
        with DatabaseManagerV2.get_session() as session:
            row = session.execute(
                text(_RECORD_FAILURE + " RETURNING failures"),
                {"username": username, "now": now,
                 "window_start": now - lockout_window()}).fetchone()
        return int(row[0]) if row else 0
    except Exception as error:
        logger.warning(f"Could not count a sign-in attempt of {username}: {error}")
        return 0


def record_success(username: str) -> None:
    """Forget the failures of a name that has just got in."""
    if not username:
        return
    try:
        DatabaseManagerV2.execute_commit(
            "DELETE FROM login_attempts WHERE username = :username",
            {"username": username}
        )
    except Exception as error:
        logger.warning(f"Could not clear the login attempts of {username}: {error}")
