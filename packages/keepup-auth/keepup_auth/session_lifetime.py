"""How much life a session has left, and when to warn about it.

The access token is a JWT: it carries its own expiry and the server keeps no
session state, so "when does this session end" is answerable only from the
token itself. These are the numbers around that -- no request, no database, no
token decoding -- which is what makes the rule checkable on its own.

Two doors, two lengths. A session signed in without "Stay signed in" is gone
after a day of renewals and does not outlive the browser. One that was asked to
stay renews for as long as it is used, sliding its end by a month on every
renewal, and the browser keeps its cookies across a restart (keepup-177).

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
#: who has been away signs in once a day rather than once a month. A session
#: that was signed in with "Stay signed in" is the exception, below.
RENEWAL_WINDOW_ENV = "SESSION_RENEWAL_WINDOW_HOURS"
DEFAULT_RENEWAL_WINDOW = timedelta(hours=24)

#: How long a remembered session may go unused before it ends. Every renewal
#: moves the end of the window, so a session that is used lasts as long as it
#: is used; a month without a visit is where the promise stops. The number is
#: also the browser's cookie lifetime: there is no point in a credential the
#: browser has already dropped (keepup-177).
REMEMBER_WINDOW_ENV = "SESSION_REMEMBER_DAYS"
DEFAULT_REMEMBER_WINDOW = timedelta(days=30)


def _positive_days(environ, name: str, default: timedelta, unit: timedelta,
                   limit: int = 1) -> timedelta:
    """A window from the environment: a positive number, else the default."""
    import os

    environ = environ if environ is not None else os.environ
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return unit * value if value >= limit else default


def renewal_window(environ=None) -> timedelta:
    """How long a session may go on being renewed."""
    return _positive_days(environ, RENEWAL_WINDOW_ENV, DEFAULT_RENEWAL_WINDOW,
                          timedelta(hours=1))


def remember_window(environ=None) -> timedelta:
    """How long a remembered session may go unused before it is over."""
    return _positive_days(environ, REMEMBER_WINDOW_ENV, DEFAULT_REMEMBER_WINDOW,
                          timedelta(days=1))


def is_renewable(
        session_started_at: Optional[datetime],
        now: Optional[datetime] = None,
        environ=None,
        remember: bool = False,
) -> bool:
    """Whether a session that began then may still be renewed.

    A session with no recorded beginning is not renewable. Those tokens were
    issued before the mark existed, and accepting them was the one path that
    renewed for ever: every exchange carried ``None`` forward, and ``None`` had
    no end (keepup-173). A remembered session carries the mark of its last
    renewal, so the same check counts a month (keepup-177).

    Args:
        session_started_at: when the window the token is held to began.
        now: the moment to judge by; the clock otherwise.
        environ: the environment to read the windows from.
        remember: whether this session was asked to stay signed in.
    """
    if session_started_at is None:
        return False
    now = now or clock.utcnow()
    window = remember_window(environ) if remember else renewal_window(environ)
    return now - session_started_at <= window



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
