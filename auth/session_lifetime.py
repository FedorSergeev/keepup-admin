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

#: How long before the end a holder is told to renew. An hour is far longer
#: than any pause in the agent's work -- it reconnects in seconds and reports
#: telemetry in minutes -- so a warning always arrives while there is still a
#: valid token to exchange.
WARNING_WINDOW = timedelta(hours=1)

#: How long a session may keep renewing itself, counted from the login that
#: started it. Renewal is authorised by the token being renewed and nothing
#: else, so without a ceiling one login lasts forever and changing a password
#: takes nothing away from whoever already holds a token.
#:
#: The default is long on purpose: the host agent holds its session across a
#: rental, and asking its owner to type a password mid-rent drops the host out
#: of the system with a machine still running on it. A month bounds the session
#: without making that the normal case.
RENEWAL_WINDOW_ENV = "SESSION_RENEWAL_WINDOW_HOURS"
DEFAULT_RENEWAL_WINDOW = timedelta(days=30)


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

    A session with no recorded beginning is renewable: tokens issued before this
    rule existed carry no such mark, and refusing them would log everybody out
    twice over -- once for the key change, once for this.
    """
    if session_started_at is None:
        return True
    now = now or datetime.utcnow()
    return now - session_started_at <= renewal_window(environ)


def seconds_left(expires_at: Optional[datetime], now: Optional[datetime] = None) -> int:
    """Seconds of life the session has left, never negative.

    A session with no known expiry counts as ended: not knowing when a token
    dies is not a reason to treat it as immortal.
    """
    if expires_at is None:
        return 0
    now = now or datetime.utcnow()
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
