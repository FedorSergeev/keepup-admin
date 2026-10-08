"""The panel session: a server-side record, carried in an httpOnly cookie.

The token used to live in localStorage, where any script on the page could read
it and take the whole session away with it; logging out only forgot it in that
one browser, and a copied token stayed good for a day. Now a session is a row,
the token names it (`sid`), and every request looks the row up -- so a logout, a
password change or a block takes effect on every replica at once.

The browser never sees the token: it sits in `ss_session` (HttpOnly). Over
HTTPS that name carries the `__Host-` prefix, which ties the cookie to the exact
host that set it; the plain name is still read for one release, so an open panel
survives the upgrade (keepup-92). The panel keeps the non-secret marker `cookie`
where the token used to be, so the many sections that check "is there a token"
and send `Bearer <token>` keep working; the server reads such a header as no
header at all and falls back to the cookie.

A cookie is sent by the browser on its own, so a request authenticated by it must
also prove it came from the panel: the double-submit `ss_csrf` cookie, readable by
the page, echoed in `X-CSRF-Token`. A real `Authorization: Bearer` (an agent, a
script) needs no such proof -- another site cannot set that header.
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from starlette import status

from keepup import tables
from keepup.db import DatabaseManagerV2

# Declared by the sign-in declares its own tables (keepup-124); re-exported here so the path that predates
# the catalogue still creates it.
from keepup_auth.tables import AUTH_SESSION  # noqa: E402

logger = logging.getLogger(__name__)

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "HOST_PREFIX",
    "SESSION_COOKIE",
    "clear_cookies",
    "cookie_names",
    "is_https",
    "session_token_in",
    "set_cookies",
]

TABLE = "auth_session"
SESSION_CLAIM = "sid"
SESSION_COOKIE = "ss_session"
CSRF_COOKIE = "ss_csrf"
CSRF_HEADER = "X-CSRF-Token"

#: The prefix that ties a cookie to one host. A browser accepts a `__Host-`
#: cookie only from the host that set it, only with Secure, only with Path=/,
#: and never with a Domain -- so a sibling subdomain, or a plain-HTTP hop, can
#: no longer plant one under this name (keepup-92).
HOST_PREFIX = "__Host-"


def cookie_names(secure: bool) -> Tuple[str, str]:
    """The session and CSRF cookie names of a request over TLS, or not.

    Over HTTPS both names carry the `__Host-` prefix; over plain HTTP they stay
    as they were, because a browser refuses a `__Host-` cookie that is not
    Secure and a deployment on a local network without TLS would never stay
    signed in.

    Args:
        secure: whether the browser reached this server over HTTPS
            (:func:`is_https`).

    Returns:
        The session cookie name and the CSRF cookie name.
    """
    if secure:
        return f"{HOST_PREFIX}{SESSION_COOKIE}", f"{HOST_PREFIX}{CSRF_COOKIE}"
    return SESSION_COOKIE, CSRF_COOKIE


def session_token_in(cookies) -> Optional[str]:
    """The session token a request carries, under either name.

    The plain name is what a panel signed in before this release holds, and it
    is read for one release so that upgrading does not sign everybody out. The
    prefixed name wins when both are there: over HTTPS it is the one a sibling
    subdomain cannot have set.
    """
    cookies = cookies or {}
    return cookies.get(HOST_PREFIX + SESSION_COOKIE) or cookies.get(SESSION_COOKIE)

#: What the panel keeps in localStorage instead of the token, and the values a
#: section sends when it had nothing there. None of them is a credential.
BEARER_PLACEHOLDERS = frozenset({"", "cookie", "null", "undefined"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

REASON_LOGOUT = "logout"
REASON_PASSWORD_CHANGED = "password_changed"
REASON_ACCOUNT_BLOCKED = "account_blocked"




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


def session_owner(sid: str) -> Optional[int]:
    """The account a live session belongs to, or None when it is revoked or unknown."""
    row = DatabaseManagerV2.execute_one(
        f"SELECT user_id, revoked_at FROM {TABLE} WHERE sid = :sid", {"sid": sid})
    if not row or row.get("revoked_at") is not None:
        return None
    return int(row["user_id"])


def is_active(sid: str, user_id: Optional[int] = None) -> bool:
    row = DatabaseManagerV2.execute_one(
        f"SELECT user_id, revoked_at FROM {TABLE} WHERE sid = :sid", {"sid": sid})
    if not row or row.get("revoked_at") is not None:
        return False
    # A session is somebody's: a token whose name and session disagree was not
    # issued by this server.
    return user_id is None or int(row["user_id"]) == int(user_id)


def revoke(sid: str, reason: str = REASON_LOGOUT) -> int:
    return _closing_sockets(DatabaseManagerV2.execute_commit(
        f"UPDATE {TABLE} SET revoked_at = :now, revoked_reason = :reason "
        f"WHERE sid = :sid AND revoked_at IS NULL",
        {"sid": sid, "now": datetime.utcnow(), "reason": reason}))


def revoke_all(user_id: int, reason: str) -> int:
    return _closing_sockets(DatabaseManagerV2.execute_commit(
        f"UPDATE {TABLE} SET revoked_at = :now, revoked_reason = :reason "
        f"WHERE user_id = :u AND revoked_at IS NULL",
        {"u": user_id, "now": datetime.utcnow(), "reason": reason}))


def _closing_sockets(revoked: int) -> int:
    """A revocation also ends the sockets signed in with it, here and on the other replicas."""
    if revoked:
        from keepup.auth import socket_sessions
        socket_sessions.wake()
    return revoked


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
    token = session_token_in(request.cookies)
    if not token:
        return None
    if request.method.upper() not in SAFE_METHODS and not csrf_matches(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="CSRF token missing or invalid")
    return token


_CSRF_CONTEXT = b"keepup.csrf"


def csrf_for(token: Optional[str]) -> Optional[str]:
    """The CSRF value of the session this token names, or None.

    Derived from the session rather than drawn at random and trusted from the
    cookie (keepup-72): whoever could plant a cookie -- a sibling subdomain, a
    plain-HTTP hop -- knew the value a double-submit check would accept, and the
    value outlived every sign-in. A new sign-in is a new session and so a new
    value; a renewal keeps the session and so keeps the value, which is what an
    open panel tab needs. None for a token that names no session or does not
    decode -- neither is accepted by the server (keepup-81).
    """
    if not token:
        return None
    try:
        import jwt
        from keepup.auth.providers.base import ALGORITHM
        from keepup.auth.signing_key import resolve_signing_key
        key = resolve_signing_key()
        payload = jwt.decode(token, key, algorithms=[ALGORITHM],
                             options={"verify_exp": False})
    except Exception:
        return None
    sid = payload.get(SESSION_CLAIM)
    if not sid:
        return None
    derived = hmac.new(key.encode("utf-8"), _CSRF_CONTEXT, hashlib.sha256).digest()
    mac = hmac.new(derived, str(sid).encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def csrf_matches(request: Request) -> bool:
    presented = request.headers.get(CSRF_HEADER)
    if not presented:
        return False
    expected = csrf_for(session_token_in(request.cookies))
    return bool(expected) and secrets.compare_digest(expected, presented)


def websocket_token(query_token: Optional[str], cookies) -> Optional[str]:
    """A websocket has no CSRF header to send; `same_origin` stands in for it."""
    return real_bearer(query_token) or session_token_in(cookies)


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
    session_name, csrf_name = cookie_names(secure)
    response.set_cookie(session_name, token, max_age=max_age, path="/",
                        httponly=True, secure=secure, samesite="lax")
    # The session's own value: new with every sign-in, the same across renewals,
    # so a panel tab holding it keeps working (keepup-72).
    csrf = csrf_for(token) or secrets.token_urlsafe(32)
    response.set_cookie(csrf_name, csrf, max_age=max_age, path="/",
                        httponly=False, secure=secure, samesite="lax")
    if secure:
        # The plain names are what a sibling subdomain could plant, so a sign-in
        # over HTTPS puts out only the names that belong to this host. The old
        # session cookie is still read here for one release (session_token_in),
        # which is what keeps an open panel signed in across the upgrade.
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
    return csrf


class CsrfCookieRefresh:
    """Put the session's CSRF value in the cookie on a read that carries another.

    A session signed in before keepup-72 holds a random value, which no longer
    matches, and the panel renews its session with a POST that needs the right
    one -- so without this every open panel would be locked out of its own
    renewal on the release day. A read cannot be forged into doing anything, so
    the answer to any GET is where the value is put right; a planted cookie is
    overwritten the same way.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method", "GET").upper() not in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        token = session_token_in(request.cookies)
        expected = csrf_for(token) if token else None
        secure = is_https(request)
        _, csrf_name = cookie_names(secure)
        if expected is None or request.cookies.get(csrf_name) == expected:
            await self.app(scope, receive, send)
            return

        from starlette.responses import Response
        carrier = Response()
        carrier.set_cookie(csrf_name, expected, path="/", httponly=False,
                           secure=secure, samesite="lax")
        if secure:
            # The value moves to the name that belongs to this host; the plain
            # one a panel signed in earlier holds stops being written.
            carrier.delete_cookie(CSRF_COOKIE, path="/")
        cookie = [(name, value) for name, value in carrier.raw_headers
                  if name == b"set-cookie"]

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": list(message.get("headers", [])) + cookie}
            await send(message)

        await self.app(scope, receive, send_with_cookie)


def clear_cookies(response) -> None:
    """Signing out clears the cookies of either scheme and of both releases."""
    for name in (f"{HOST_PREFIX}{SESSION_COOKIE}", SESSION_COOKIE,
                 f"{HOST_PREFIX}{CSRF_COOKIE}", CSRF_COOKIE):
        response.delete_cookie(name, path="/")
