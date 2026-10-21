"""How much life a session has left, and when to warn about it.

The access token is a JWT: it carries its own expiry and the server keeps no
session state, so "when does this session end" is answerable only from the
token itself. These are the numbers around that -- no request, no database, no
token decoding -- which is what makes the rule checkable on its own.

Why it matters beyond a person's convenience: the host agent logs in the same
way and authorises its reconnect with the same token. An expired one leaves it
unable to obtain a websocket key at all, so the host drops out of the system
while the machine is running.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from keepup import clock

#: How long before the end a holder is told to renew. Five minutes is longer
#: than a reconnect or a telemetry report, so the warning arrives while there
#: is still a valid token to exchange, and short enough not to be a constant
#: interruption on a sixty-minute token (keepup-173).
WARNING_WINDOW = timedelta(minutes=5)

#: How long a session may keep renewing itself, counted from the login that
#: started it. Renewal is authorised by the token being renewed and nothing
#: else, so without a ceiling one login lasts forever and changing a password
#: takes nothing away from whoever already holds a token.
#:
#: A day bounds the session without making the normal case a password in the
#: middle of a rental: the host agent renews on its own, and an administrator
#: who has been away signs in once a day rather than once a month.
RENEWAL_WINDOW_ENV = "SESSION_RENEWAL_WINDOW_HOURS"
DEFAULT_RENEWAL_WINDOW = timedelta(hours=24)


def renewal_window(environ=None) -> timedelta:
    """How long a session may go on being renewed."""
    import os

    environ = environ if environ is not None else os.environ
    raw = environ.get(RENEWAL_WINDOW_ENV)
    if raw is None:
        return DEFAULT_RENEWAL_WINDOW
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RENEWAL_WINDOW
    return timedelta(hours=hours) if hours > 0 else DEFAULT_RENEWAL_WINDOW


def is_renewable(
        session_started_at: Optional[datetime],
        now: Optional[datetime] = None,
        environ=None,
) -> bool:
    """Whether a session that began then may still be renewed.

    A session with no recorded beginning is not renewable. Those tokens were
    issued before the mark existed, and accepting them was the one path that
    renewed for ever: every exchange carried ``None`` forward, and ``None`` had
    no end (keepup-173). A deployment upgrading signs those holders in once.
    """
    if session_started_at is None:
        return False
    now = now or clock.utcnow()
    return now - session_started_at <= renewal_window(environ)


def seconds_left(expires_at: Optional[datetime], now: Optional[datetime] = None) -> int:
    """Seconds of life the session has left, never negative.

    A session with no known expiry counts as ended: not knowing when a token
    dies is not a reason to treat it as immortal.
    """
    if expires_at is None:
        return 0
    now = now or clock.utcnow()
    remaining = (expires_at - now).total_seconds()
    return max(0, int(remaining))


def is_expired(expires_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """Whether the session is over."""
    return seconds_left(expires_at, now) <= 0


def should_warn(expires_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """Whether the holder should be told to renew now.

    An already expired session is not warned about: there is nothing left to
    exchange, and the holder will find out from the next refusal.
    """
    left = seconds_left(expires_at, now)
    return 0 < left <= WARNING_WINDOW.total_seconds()


def can_refresh(expires_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """Whether this session may still be exchanged for a new one.

    Only a living token buys a new one. Renewing an expired token would make
    the expiry mean nothing: one token issued once would live forever.
    """
    return not is_expired(expires_at, now)
