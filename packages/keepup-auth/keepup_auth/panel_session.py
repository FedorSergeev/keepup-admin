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

A cookie belongs to a host, not to a port. Two keepup applications opened on
one host -- `localhost:8000` and `localhost:8010` -- therefore used to write the
same `ss_session`, and whichever signed in last took the other's session away.
Since 0.7.0 the names carry the application's own name (`ss_session_artgallery`,
`ss_csrf_artgallery`), which `configure()` sets from the deployment's
`project_name`; the names of 0.6.0 are read only by an application that asks
for them with an empty prefix.

A cookie is sent by the browser on its own, so a request authenticated by it must
also prove it came from the panel: the double-submit `ss_csrf` cookie, readable by
the page, echoed in `X-CSRF-Token`. A real `Authorization: Bearer` (an agent, a
script) needs no such proof -- another site cannot set that header.

"Stay signed in" adds a third cookie, `ss_refresh` (HttpOnly, also `__Host-` over
HTTPS): an opaque secret whose digest is kept in the session row. It buys a new
access token after the old one has expired, which is what makes a remembered
session survive a closed browser and a machine that slept. It is spent only
against the row: revoked, blocked and past its idle window all refuse it
(keepup-177).
"""

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from starlette import status

from keepup import clock
from keepup_db import tables as tables
from keepup_db import DatabaseManagerV2

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
    "REFRESH_COOKIE",
    "SESSION_COOKIE",
    "clear_cookies",
    "clear_refresh_cookies",
    "configure",
    "cookie_base",
    "cookie_names",
    "cookie_prefix",
    "is_https",
    "new_refresh_secret",
    "refresh_cookie_name",
    "refresh_digest",
    "refresh_token_in",
    "remember_cookie_age",
    "session_for_refresh",
    "session_of_refresh",
    "session_token_in",
    "set_cookies",
]

TABLE = "auth_session"
SESSION_CLAIM = "sid"
SESSION_COOKIE = "ss_session"
CSRF_COOKIE = "ss_csrf"
REFRESH_COOKIE = "ss_refresh"
CSRF_HEADER = "X-CSRF-Token"

#: Bytes behind the renewal credential. It is never shown to the page and the
#: database keeps only its digest, so a leaked table is not a set of sessions.
REFRESH_SECRET_BYTES = 32

#: The application's name inside its cookie names. Empty keeps the names of
#: releases before 0.7.0 (`ss_session`, `ss_csrf`), which one application per
#: host can still use; everything else appends `_<prefix>`.
COOKIE_PREFIX = ""


def _namespace(value) -> str:
    """Make one cookie-name-safe token of an application's name.

    A cookie name may carry only a small set of characters, and two names that
    differ only in the characters a browser refuses would become the same
    cookie. A value that had to be changed, or shortened, therefore gains a
    short digest of what it was: `My App` and `My-App` stay different cookies,
    and a name longer than the limit does not lose its tail unnoticed.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-_")
    if not slug:
        slug = "app"
    if slug != raw or len(raw) > 32:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        slug = f"{slug[:32].strip('-_')}-{digest}"
    return slug


def configure(prefix=None) -> None:
    """Name this application's cookies.

    Cookies are scoped to a host, not to a port or a path: two keepup panels
    opened on one host -- `localhost:8000` and `localhost:8010` -- write the
    same `ss_session`, and whichever signs in last takes the other's session
    away. A deployment says who it is here, and the panel's cookies carry that
    name; `None` gives the shared names of 0.6.0 and earlier.

    Args:
        prefix: the application's name, or None for the shared names of 0.6.0
            and earlier.
    """
    global COOKIE_PREFIX
    COOKIE_PREFIX = _namespace(prefix)


def cookie_prefix() -> str:
    """The namespace the panel's cookies carry, or "" for the shared names."""
    return COOKIE_PREFIX


def cookie_base(name: str) -> str:
    """One cookie's name without the `__Host-` prefix a secure request adds."""
    return f"{name}_{COOKIE_PREFIX}" if COOKIE_PREFIX else name


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
    signed in. The application's namespace, when it has one, sits inside the
    name in both schemes.

    Args:
        secure: whether the browser reached this server over HTTPS
            (:func:`is_https`).

    Returns:
        The session cookie name and the CSRF cookie name.
    """
    session_name = cookie_base(SESSION_COOKIE)
    csrf_name = cookie_base(CSRF_COOKIE)
    if secure:
        return f"{HOST_PREFIX}{session_name}", f"{HOST_PREFIX}{csrf_name}"
    return session_name, csrf_name


def session_token_in(cookies) -> Optional[str]:
    """The session token a request carries, under either scheme's name.

    The prefixed name wins when both are there: over HTTPS it is the one a
    sibling subdomain cannot have set. A namespaced application reads only its
    own names -- the shared `ss_session` of 0.6.0 belongs to whichever
    application wrote it last, and reading it here is exactly the collision the
    namespace exists to end.
    """
    cookies = cookies or {}
    session_name = cookie_base(SESSION_COOKIE)
    return cookies.get(HOST_PREFIX + session_name) or cookies.get(session_name)


def refresh_cookie_name(secure: bool) -> str:
    """The name the renewal credential travels under."""
    name = cookie_base(REFRESH_COOKIE)
    return f"{HOST_PREFIX}{name}" if secure else name


def refresh_token_in(cookies) -> Optional[str]:
    """The renewal credential a request carries, or None.

    Read under the prefixed name first, for the same reason the session token
    is: over HTTPS that is the name a sibling subdomain cannot write.
    """
    cookies = cookies or {}
    name = cookie_base(REFRESH_COOKIE)
    return cookies.get(HOST_PREFIX + name) or cookies.get(name)


def new_refresh_secret() -> str:
    """A fresh renewal credential: long, random, and never derived from anything."""
    return secrets.token_urlsafe(REFRESH_SECRET_BYTES)


def refresh_digest(secret: Optional[str]) -> Optional[str]:
    """What the database keeps of a renewal credential: a digest, not the secret."""
    if not secret:
        return None
    return hashlib.sha256(str(secret).encode("utf-8")).hexdigest()


def remember_cookie_age(environ=None) -> int:
    """How long the browser keeps the cookies of a remembered session."""
    from keepup_auth import session_lifetime

    return int(session_lifetime.remember_window(environ).total_seconds())


def _as_datetime(value) -> Optional[datetime]:
    """A datetime a database returned, whichever dialect and driver it came from."""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _remembered(token: Optional[str]) -> bool:
    """Whether this token belongs to a session that was asked to stay signed in."""
    if not token:
        return False
    try:
        from keepup_auth.dependencies import REMEMBER_CLAIM
        from keepup_auth.signing_key import decode_token

        return bool(decode_token(token, options={"verify_exp": False}).get(REMEMBER_CLAIM))
    except Exception:
        return False

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

def open_session(user_id: int, lifetime: timedelta, now: Optional[datetime] = None,
                 remember: bool = False,
                 refresh_hash: Optional[str] = None) -> str:
    """Open a session; a remembered one also gets a renewal digest.

    ``lifetime`` is how far the row's end is set -- the token's life for an
    ordinary session, the remember window for one that stays signed in. The
    token's own end is still an hour (keepup-177).
    """
    now = now or clock.utcnow()
    sid = secrets.token_urlsafe(32)
    DatabaseManagerV2.execute_commit(
        f"INSERT INTO {TABLE} (sid, user_id, created_at, expires_at, remember, refresh_hash) "
        f"VALUES (:sid, :user_id, :now, :expires_at, :remember, :refresh_hash)",
        {"sid": sid, "user_id": user_id, "now": now, "expires_at": now + lifetime,
         "remember": bool(remember), "refresh_hash": refresh_hash})
    return sid


def extend(sid: str, lifetime: timedelta, now: Optional[datetime] = None) -> None:
    """A renewal keeps the session and moves its end, like the token it renews."""
    now = now or clock.utcnow()
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


def session_for_refresh(secret: str,
                        now: Optional[datetime] = None) -> Optional[dict]:
    """The live remembered session this renewal credential names, or None.

    Every condition a person's "stay signed in" rests on is checked here: the
    digest is known, the row was not revoked (logout, a password change, a
    block), it was signed in with the box ticked, and the idle window has not
    passed. A blocked account is checked by the caller, which has the tools to
    read one.
    """
    if not secret:
        return None
    row = DatabaseManagerV2.execute_one(
        f"SELECT sid, user_id, remember, expires_at, revoked_at FROM {TABLE} "
        f"WHERE refresh_hash = :digest", {"digest": refresh_digest(secret)})
    if not row or row.get("revoked_at") is not None or not row.get("remember"):
        return None
    expires_at = _as_datetime(row.get("expires_at"))
    if expires_at is None or expires_at <= (now or clock.utcnow()):
        return None
    return row


def session_of_refresh(secret: str) -> Optional[str]:
    """The session a renewal credential belongs to, ended or not.

    Only signing out asks this: a session whose token has already expired must
    still be revoked when its holder presses "Sign out", rather than left alive
    in the database until the credential ages out.
    """
    if not secret:
        return None
    row = DatabaseManagerV2.execute_one(
        f"SELECT sid FROM {TABLE} WHERE refresh_hash = :digest",
        {"digest": refresh_digest(secret)})
    return row["sid"] if row else None


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
        {"sid": sid, "now": clock.utcnow(), "reason": reason}))


def revoke_all(user_id: int, reason: str) -> int:
    return _closing_sockets(DatabaseManagerV2.execute_commit(
        f"UPDATE {TABLE} SET revoked_at = :now, revoked_reason = :reason "
        f"WHERE user_id = :u AND revoked_at IS NULL",
        {"u": user_id, "now": clock.utcnow(), "reason": reason}))


def _closing_sockets(revoked: int) -> int:
    """A revocation also ends the sockets signed in with it, here and on the other replicas."""
    if revoked:
        from keepup_auth import socket_sessions
        socket_sessions.wake()
    return revoked


def purge_expired(now: Optional[datetime] = None, keep: timedelta = timedelta(days=7)) -> int:
    """Rows past their end serve nothing; a week is kept for whoever investigates."""
    return DatabaseManagerV2.execute_commit(
        f"DELETE FROM {TABLE} WHERE expires_at < :cutoff",
        {"cutoff": (now or clock.utcnow()) - keep})


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
        from keepup_auth.signing_key import decode_token, resolve_signing_key
        key = resolve_signing_key()
        payload = decode_token(token, options={"verify_exp": False})
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


def set_cookies(response, request: Request, token: str, max_age: Optional[int],
                refresh: Optional[str] = None) -> str:
    """Put the session in the browser, and the renewal credential if there is one.

    Args:
        response: the answer the cookies are set on.
        request: the request that caused it, read for HTTPS.
        token: the access token.
        max_age: how long the browser keeps the cookies, or None for cookies
            that end with the browser. A remembered sign-in passes the remember
            window; every other sign-in passes None (keepup-177).
        refresh: the renewal secret of a remembered session, or None. Without
            one a stale credential is removed: it must not outlive the session
            it was issued for.

    Returns:
        The CSRF value the page has to send back, which the cookie carries.
    """
    secure = is_https(request)
    session_name, csrf_name = cookie_names(secure)
    response.set_cookie(session_name, token, max_age=max_age, path="/",
                        httponly=True, secure=secure, samesite="lax")
    # The session's own value: new with every sign-in, the same across renewals,
    # so a panel tab holding it keeps working (keepup-72).
    csrf = csrf_for(token) or secrets.token_urlsafe(32)
    response.set_cookie(csrf_name, csrf, max_age=max_age, path="/",
                        httponly=False, secure=secure, samesite="lax")
    if refresh:
        response.set_cookie(refresh_cookie_name(secure), refresh, max_age=max_age, path="/",
                            httponly=True, secure=secure, samesite="lax")
    else:
        # A credential from an earlier, remembered sign-in must not outlive the
        # session that replaced it.
        clear_refresh_cookies(response, secure)
    if not COOKIE_PREFIX and secure:
        # The plain names are what a sibling subdomain could plant, so a sign-in
        # over HTTPS puts out only the names that belong to this host. The old
        # session cookie is still read here for one release (session_token_in),
        # which is what keeps an open panel signed in across the upgrade.
        # A namespaced application leaves the shared names alone: they may
        # still belong to another panel on this host, and signing in here must
        # not sign anybody out there.
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
        # A remembered session keeps the pair while the renewal credential
        # lives; replacing its value must not silently reduce the cookie to the
        # browser's session (keepup-177).
        carrier.set_cookie(csrf_name, expected, path="/", httponly=False,
                           secure=secure, samesite="lax",
                           max_age=remember_cookie_age() if _remembered(token) else None)
        if not COOKIE_PREFIX and secure:
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


def clear_refresh_cookies(response, secure: bool = False) -> None:
    """Forget the renewal credential under this scheme's name.

    A sign-in over HTTPS also expires the plain name: what a sibling subdomain
    may plant is exactly that one. Over plain HTTP only the plain name is
    touched, because a `__Host-` cookie without Secure is one a browser refuses
    to keep -- and setting one there would be a line the server never means.
    """
    name = refresh_cookie_name(secure)
    response.delete_cookie(name, path="/", secure=name.startswith(HOST_PREFIX))
    if secure:
        response.delete_cookie(cookie_base(REFRESH_COOKIE), path="/")


def clear_cookies(response) -> None:
    """Signing out clears this application's cookies, under either scheme.

    Another application's names are not in this set: its namespace is its own,
    and signing out here must not sign anybody out there. An application that
    kept the shared names of 0.6.0 (an empty prefix) clears those, because they
    are the ones it reads. The renewal credential goes with them: leaving it
    behind would let the next refresh sign the person back in (keepup-177).
    """
    names = set()
    for secure in (False, True):
        names.update(cookie_names(secure))
        names.add(refresh_cookie_name(secure))
    for name in names:
        response.delete_cookie(name, path="/", secure=name.startswith(HOST_PREFIX))
