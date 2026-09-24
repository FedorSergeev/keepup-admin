"""The panel session: a server-side record, carried in an httpOnly cookie.

The token used to live in localStorage, where any script on the page could read
it and take the whole session away with it; logging out only forgot it in that
one browser, and a copied token stayed good for a day. Now a session is a row,
the token names it (`sid`), and every request looks the row up -- so a logout, a
password change or a block takes effect on every replica at once.

The browser never sees the token: it sits in `ss_session` (HttpOnly). The panel
keeps the non-secret marker `cookie` where the token used to be, so the many
sections that check "is there a token" and send `Bearer <token>` keep working;
the server reads such a header as no header at all and falls back to the cookie.

A cookie is sent by the browser on its own, so a request authenticated by it must
also prove it came from the panel: the double-submit `ss_csrf` cookie, readable by
the page, echoed in `X-CSRF-Token`. A real `Authorization: Bearer` (an agent, a
script) needs no such proof -- another site cannot set that header.
"""

import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from starlette import status

from keepup import tables
from keepup.db import DatabaseManagerV2

logger = logging.getLogger(__name__)

TABLE = "auth_session"
SESSION_CLAIM = "sid"
SESSION_COOKIE = "ss_session"
CSRF_COOKIE = "ss_csrf"
CSRF_HEADER = "X-CSRF-Token"

#: What the panel keeps in localStorage instead of the token, and the values a
#: section sends when it had nothing there. None of them is a credential.
BEARER_PLACEHOLDERS = frozenset({"", "cookie", "null", "undefined"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

REASON_LOGOUT = "logout"
REASON_PASSWORD_CHANGED = "password_changed"
REASON_ACCOUNT_BLOCKED = "account_blocked"


AUTH_SESSION = tables.table(
    TABLE,
    Column("sid", String(64).with_variant(Text(), "sqlite"), primary_key=True, nullable=True),
    Column("user_id", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("revoked_at", DateTime),
    Column("revoked_reason", String(64).with_variant(Text(), "sqlite")),
    Index(f"idx_{TABLE}_user", "user_id"),
)


def init_table() -> None:
    tables.ensure_tables(AUTH_SESSION)
    # Each start is often enough: a row past its end is only kept for inquiry.
    purge_expired()


# --- the record ------------------------------------------------------------------------------

def open_session(user_id: int, lifetime: timedelta, now: Optional[datetime] = None) -> str:
    now = now or datetime.utcnow()
    sid = secrets.token_urlsafe(32)
    DatabaseManagerV2.execute_commit(
        f"INSERT INTO {TABLE} (sid, user_id, created_at, expires_at) "
        f"VALUES (:sid, :user_id, :now, :expires_at)",
        {"sid": sid, "user_id": user_id, "now": now, "expires_at": now + lifetime})
    return sid


def extend(sid: str, lifetime: timedelta, now: Optional[datetime] = None) -> None:
    """A renewal keeps the session and moves its end, like the token it renews."""
    now = now or datetime.utcnow()
    DatabaseManagerV2.execute_commit(
        f"UPDATE {TABLE} SET expires_at = :expires_at WHERE sid = :sid AND revoked_at IS NULL",
        {"sid": sid, "expires_at": now + lifetime})


def is_active(sid: str, user_id: Optional[int] = None) -> bool:
    row = DatabaseManagerV2.execute_one(
        f"SELECT user_id, revoked_at FROM {TABLE} WHERE sid = :sid", {"sid": sid})
    if not row or row.get("revoked_at") is not None:
        return False
    # A session is somebody's: a token whose name and session disagree was not
    # issued by this server.
    return user_id is None or int(row["user_id"]) == int(user_id)


def revoke(sid: str, reason: str = REASON_LOGOUT) -> int:
    return DatabaseManagerV2.execute_commit(
        f"UPDATE {TABLE} SET revoked_at = :now, revoked_reason = :reason "
        f"WHERE sid = :sid AND revoked_at IS NULL",
        {"sid": sid, "now": datetime.utcnow(), "reason": reason})


def revoke_all(user_id: int, reason: str) -> int:
    return DatabaseManagerV2.execute_commit(
        f"UPDATE {TABLE} SET revoked_at = :now, revoked_reason = :reason "
        f"WHERE user_id = :u AND revoked_at IS NULL",
        {"u": user_id, "now": datetime.utcnow(), "reason": reason})


def purge_expired(now: Optional[datetime] = None, keep: timedelta = timedelta(days=7)) -> int:
    """Rows past their end serve nothing; a week is kept for whoever investigates."""
    return DatabaseManagerV2.execute_commit(
        f"DELETE FROM {TABLE} WHERE expires_at < :cutoff",
        {"cutoff": (now or datetime.utcnow()) - keep})


# --- where the token comes from --------------------------------------------------------------

def real_bearer(value: Optional[str]) -> Optional[str]:
    """The bearer token, or None when the header only carries a placeholder."""
    if value is None or value.strip() in BEARER_PLACEHOLDERS:
        return None
    return value.strip()


def token_from_request(request: Request, bearer: Optional[str]) -> Optional[str]:
    """The token of this request: a real bearer first, the session cookie second.

    A cookie-authenticated request that changes something must carry the CSRF
    header; without it the answer is 403, not a quiet fall-through to anonymous.
    """
    token = real_bearer(bearer)
    if token:
        return token
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    if request.method.upper() not in SAFE_METHODS and not csrf_matches(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="CSRF token missing or invalid")
    return token


def csrf_matches(request: Request) -> bool:
    expected = request.cookies.get(CSRF_COOKIE)
    presented = request.headers.get(CSRF_HEADER)
    return bool(expected and presented and secrets.compare_digest(expected, presented))


def websocket_token(query_token: Optional[str], cookies) -> Optional[str]:
    """A websocket has no CSRF header to send; `same_origin` stands in for it."""
    return real_bearer(query_token) or (cookies or {}).get(SESSION_COOKIE)


def same_origin(headers) -> bool:
    """Whether the page that opened a websocket is served from this server.

    Compared with the host the request names and with the public address: a proxy
    that does not pass `Host` on still leaves the public address to match.
    """
    origin = urlparse((headers.get("origin") or "").strip()).netloc.lower()
    if not origin:
        return False
    names = {(headers.get("x-forwarded-host") or "").split(",")[0].strip().lower(),
             (headers.get("host") or "").strip().lower()}
    api_host = (os.environ.get("API_HOST") or "").strip().lower()
    if api_host:
        names.add(urlparse(api_host if "://" in api_host else f"//{api_host}").netloc)
    return origin in names - {""}


# --- the cookies -----------------------------------------------------------------------------

#: Set on a deployment behind a proxy that terminates TLS and does not say so.
#: Without it the cookie goes out without Secure and travels over plain HTTP
#: the first time somebody types the address without the scheme -- and there
#: was no way to insist (task keepup-14).
FORCE_SECURE_COOKIES_ENV = "FORCE_SECURE_COOKIES"


def is_https(request: Request) -> bool:
    """Whether the browser reached us over HTTPS -- the proxy says so, or the socket.

    Not guessed from API_HOST: a direct http visit on the local network would then
    get a Secure cookie the browser refuses to keep, and never stay signed in.
    A deployment that knows better says so with FORCE_SECURE_COOKIES.

    Args:
        request: the incoming request.

    Returns:
        Whether the cookie may be marked Secure.
    """
    if os.getenv(FORCE_SECURE_COOKIES_ENV, "false").lower() == "true":
        return True
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if forwarded:
        return forwarded == "https"
    return request.url.scheme == "https"


def set_cookies(response, request: Request, token: str, max_age: int) -> str:
    secure = is_https(request)
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, path="/",
                        httponly=True, secure=secure, samesite="lax")
    # Kept across renewals: a panel tab holding the old value would otherwise
    # have its next change refused.
    csrf = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, path="/",
                        httponly=False, secure=secure, samesite="lax")
    return csrf


def clear_cookies(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
