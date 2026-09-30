"""Who knocks: accounts of each standing, their sessions, and forged tokens.

Accounts are made the way the framework makes them and signed in the way it
signs people in, so a check that passes here passes for the same reason it
would on a stand. Forgeries are built by hand -- each one is a way somebody
outside might try to be somebody inside.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import jwt

from keepup.auth import dependencies, panel_session, user_roles
from keepup.auth.signing_key import resolve_signing_key
from keepup.roles import ROLE_ADMIN, ROLE_CLIENT

#: A password long enough for any rule an application might supply.
ACTOR_PASSWORD = "an-audit-password-94"


@dataclass
class Actor:
    id: int
    username: str
    token: str
    sid: str

    def bearer(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def cookies(self, csrf: Optional[str] = "right") -> Dict[str, str]:
        """Headers of a panel page: the session cookie, and the CSRF pair or not.

        ``csrf`` is "right", "wrong" or None (no header at all).
        """
        value = panel_session.csrf_for(self.token)
        headers = {"Cookie": f"{panel_session.SESSION_COOKIE}={self.token}; "
                             f"{panel_session.CSRF_COOKIE}={value}"}
        if csrf == "right":
            headers[panel_session.CSRF_HEADER] = value
        elif csrf == "wrong":
            headers[panel_session.CSRF_HEADER] = "not-" + value
        return headers


def make_actor(role: str = ROLE_CLIENT, status: str = "active", prefix: str = "audit") -> Actor:
    """A local account with a live session of its own."""
    name = f"{prefix}-{uuid.uuid4().hex[:10]}"
    uid = dependencies.save_user_to_db(name, ACTOR_PASSWORD)
    dependencies.update_user(uid, status=status)
    user_roles.set_roles(uid, [role], checked=False)
    issued = dependencies.issue_session_token(uid, name)
    return Actor(uid, name, issued["access_token"], issued["session_id"])


def admin() -> Actor:
    return make_actor(ROLE_ADMIN, prefix="audit-admin")


def client_actor() -> Actor:
    return make_actor(ROLE_CLIENT)


# --- forgeries -------------------------------------------------------------------------

def _b64(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _claims(actor: Actor, **overrides) -> dict:
    claims = {"sub": actor.username, panel_session.SESSION_CLAIM: actor.sid,
              "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp())}
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def forged_unsigned(actor: Actor) -> str:
    """`alg: none` -- the oldest trick: no signature at all."""
    return f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(_claims(actor))}."


def forged_other_key(actor: Actor) -> str:
    """The right claims, signed with a key that is not the deployment's."""
    return jwt.encode(_claims(actor), "a-key-somebody-guessed-" + "x" * 16, algorithm="HS256")


def forged_rs256(actor: Actor) -> str:
    """Signed with an asymmetric key of the forger's own (algorithm confusion)."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return jwt.encode(_claims(actor), key, algorithm="RS256")


def expired(actor: Actor) -> str:
    """Our key, our session -- and a minute past its end."""
    past = int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())
    return jwt.encode(_claims(actor, exp=past), resolve_signing_key(), algorithm="HS256")


def without_session(actor: Actor) -> str:
    """Our key, no session claim: tokens from before sessions (keepup-81)."""
    return jwt.encode(_claims(actor, **{panel_session.SESSION_CLAIM: None}),
                      resolve_signing_key(), algorithm="HS256")


def someone_elses_session(actor: Actor, other: Actor) -> str:
    """Our key, the actor's name, the other account's live session (keepup-64)."""
    return jwt.encode(_claims(actor, **{panel_session.SESSION_CLAIM: other.sid}),
                      resolve_signing_key(), algorithm="HS256")


def revoked(actor: Actor) -> str:
    """A real token whose session has been revoked."""
    issued = dependencies.issue_session_token(actor.id, actor.username)
    panel_session.revoke(issued["session_id"])
    return issued["access_token"]


#: Every forgery by name, each taking the actor it pretends to be.
FORGERIES = {
    "alg-none": forged_unsigned,
    "other-key": forged_other_key,
    "rs256": forged_rs256,
    "expired": expired,
    "no-session": without_session,
    "revoked-session": revoked,
}
