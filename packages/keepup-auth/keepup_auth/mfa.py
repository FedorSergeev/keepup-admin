"""A second factor of the framework's own: TOTP, one-use, with recovery codes.

The panel could delegate a second factor to an identity provider, and a
deployment that has one should. A deployment that does not had no second factor
at all: a password, however long, was the whole of the door (keepup-174).

This is the smallest honest thing that closes that: RFC 6238 with the parameters
every authenticator app assumes (SHA-1, 30 seconds, six digits), a secret kept in
the account's own table, a code accepted once, and ten recovery codes shown once
and kept hashed. The API is here; the panel that shows a QR code is an
application's screen -- the framework answers the state and the provisioning
URI, and a client that can render them is enough.

A confirmation is a row with ``confirmed_at``. The login route asks
:func:`needs_second_factor`; a confirmed account gets a five-minute challenge
instead of a session and exchanges it with :func:`verify_for_user`.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import struct
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from keepup import clock
from keepup_db import DatabaseManagerV2

logger = logging.getLogger(__name__)

__all__ = [
    "CHALLENGE_MINUTES",
    "RECOVERY_CODES",
    "begin",
    "challenge_remember",
    "challenge_token",
    "code_for",
    "confirm",
    "disable",
    "needs_second_factor",
    "new_secret",
    "otpauth_uri",
    "read_challenge",
    "status",
    "verify_for_user",
]

#: How long the challenge between the password and the code lives.
CHALLENGE_MINUTES = 5
#: How many one-time recovery codes an account gets.
RECOVERY_CODES = 10
#: The step every authenticator assumes.
STEP_SECONDS = 30
DIGITS = 6
ISSUER = "keepup"
PURPOSE = "mfa"


def new_secret() -> str:
    """A 160-bit secret, in the base32 an authenticator app reads."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    padding = "=" * ((8 - len(secret) % 8) % 8)
    return base64.b32decode(secret + padding, casefold=True)


def step_for(now: Optional[datetime] = None) -> int:
    """The 30-second counter this moment belongs to."""
    return int((now or clock.utcnow()).timestamp() // STEP_SECONDS)


def code_for(secret: str, step: Optional[int] = None) -> str:
    """The six digits of this secret at this step (RFC 6238, SHA-1)."""
    counter = step_for() if step is None else step
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % (10 ** DIGITS):0{DIGITS}d}"


def otpauth_uri(secret: str, username: str, issuer: str = ISSUER) -> str:
    """What an authenticator app scans, or types by hand."""
    label = quote(f"{issuer}:{username}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}")


def _normalise(code: str) -> str:
    return "".join(character for character in str(code or "") if character.isdigit())


def _matches(secret: str, code: str, last_step: Optional[int],
             now: Optional[datetime] = None) -> Optional[int]:
    """The step this code belongs to, or None; a step once spent is spent."""
    code = _normalise(code)
    if len(code) != DIGITS:
        return None
    current = step_for(now)
    for step in (current - 1, current, current + 1):
        if step <= (last_step or 0):
            continue
        if hmac.compare_digest(code, code_for(secret, step)):
            return step
    return None


def _hash_recovery(code: str) -> str:
    return hashlib.sha256(code.strip().upper().replace("-", "").encode("utf-8")).hexdigest()


def _recovery_codes() -> List[str]:
    """Ten codes, grouped as people write them down; shown once."""
    return [f"{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
            for _ in range(RECOVERY_CODES)]


def status(user_id: int) -> Dict[str, Any]:
    """Whether this account has a confirmed second factor."""
    row = DatabaseManagerV2.execute_one(
        "SELECT confirmed_at FROM auth_totp WHERE user_id = :user_id", {"user_id": user_id})
    return {"enabled": bool(row and row.get("confirmed_at") is not None),
            "confirmed_at": row.get("confirmed_at") if row else None}


def needs_second_factor(user_id: int) -> bool:
    """Whether a password alone is not this account's whole sign-in."""
    return bool(status(user_id)["enabled"])


def begin(user_id: int) -> Dict[str, Any]:
    """A new secret, not yet confirmed. A confirmed factor is not replaced here."""
    existing = DatabaseManagerV2.execute_one(
        "SELECT confirmed_at FROM auth_totp WHERE user_id = :user_id", {"user_id": user_id})
    if existing and existing.get("confirmed_at") is not None:
        raise ValueError("a second factor is already confirmed")
    secret = new_secret()
    DatabaseManagerV2.execute_commit(
        "INSERT INTO auth_totp (user_id, secret, confirmed_at, last_step, recovery, created_at) "
        "VALUES (:user_id, :secret, NULL, NULL, NULL, :now) "
        "ON CONFLICT (user_id) DO UPDATE SET secret = :secret, confirmed_at = NULL, "
        "last_step = NULL, recovery = NULL",
        {"user_id": user_id, "secret": secret, "now": clock.utcnow()})
    return {"secret": secret}


def confirm(user_id: int, code: str, now: Optional[datetime] = None) -> Optional[List[str]]:
    """Confirm the secret with a code; the recovery codes, or None when wrong."""
    row = DatabaseManagerV2.execute_one(
        "SELECT secret, confirmed_at, last_step FROM auth_totp WHERE user_id = :user_id",
        {"user_id": user_id})
    if not row or row.get("confirmed_at") is not None:
        return None
    step = _matches(row["secret"], code, row.get("last_step"), now)
    if step is None:
        return None
    codes = _recovery_codes()
    DatabaseManagerV2.execute_commit(
        "UPDATE auth_totp SET confirmed_at = :now, last_step = :step, recovery = :recovery "
        "WHERE user_id = :user_id",
        {"now": now or clock.utcnow(), "step": step,
         "recovery": json.dumps([_hash_recovery(value) for value in codes]),
         "user_id": user_id})
    return codes


def disable(user_id: int) -> None:
    """Forget the factor: used after the account proved the password again."""
    DatabaseManagerV2.execute_commit(
        "DELETE FROM auth_totp WHERE user_id = :user_id", {"user_id": user_id})


def verify_for_user(user_id: int, code: Optional[str] = None,
                    recovery: Optional[str] = None,
                    now: Optional[datetime] = None) -> bool:
    """Whether this code (or recovery code) lets the account in, once."""
    row = DatabaseManagerV2.execute_one(
        "SELECT secret, confirmed_at, last_step, recovery FROM auth_totp "
        "WHERE user_id = :user_id", {"user_id": user_id})
    if not row or row.get("confirmed_at") is None:
        return False

    if recovery:
        stored = json.loads(row.get("recovery") or "[]")
        digest = _hash_recovery(recovery)
        if digest not in stored:
            return False
        stored.remove(digest)
        # Conditional on the list we read: two uses arriving together both
        # read the code, and only the one whose UPDATE finds the old list wins.
        claimed = DatabaseManagerV2.execute_commit(
            "UPDATE auth_totp SET recovery = :recovery WHERE user_id = :user_id "
            "AND recovery = :seen",
            {"recovery": json.dumps(stored), "seen": row.get("recovery"),
             "user_id": user_id})
        return bool(claimed)

    step = _matches(row["secret"], code or "", row.get("last_step"), now)
    if step is None:
        return False
    # The code is claimed by the UPDATE, not by the read above: two requests
    # carrying the same code both see the old last_step, and only the one whose
    # UPDATE moves it wins (keepup-174).
    claimed = DatabaseManagerV2.execute_commit(
        "UPDATE auth_totp SET last_step = :step WHERE user_id = :user_id "
        "AND (last_step IS NULL OR last_step < :step)",
        {"step": step, "user_id": user_id})
    return bool(claimed)


def challenge_token(username: str, remember: bool = False) -> str:
    """Five minutes between the password and the code; not a session.

    The "stay signed in" answer travels in the challenge: the session is issued
    after the code, and the box a person ticked must still hold then
    (keepup-177).
    """
    from keepup_auth.dependencies import REMEMBER_CLAIM, create_access_token

    claims = {"sub": username, "purpose": PURPOSE}
    if remember:
        claims[REMEMBER_CLAIM] = True
    return create_access_token(claims, expires_delta=timedelta(minutes=CHALLENGE_MINUTES))


def challenge_remember(token: str) -> bool:
    """Whether the sign-in behind this challenge asked to stay signed in."""
    from keepup_auth.dependencies import REMEMBER_CLAIM
    from keepup_auth.signing_key import decode_token

    try:
        payload = decode_token(token)
    except Exception:
        return False
    return payload.get("purpose") == PURPOSE and bool(payload.get(REMEMBER_CLAIM))


def read_challenge(token: str) -> Optional[str]:
    """The name a challenge names, or None when it is not a live challenge."""
    from keepup_auth.signing_key import decode_token

    try:
        payload = decode_token(token)
    except Exception:
        return None
    if payload.get("purpose") != PURPOSE or not payload.get("sub"):
        return None
    return str(payload["sub"])
