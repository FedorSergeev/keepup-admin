"""Who is calling, as a service behind the gateway is told -- and checks.

A service behind the gateway must not learn to read the panel's session, and must
not be handed its cookie as a credential either. The gateway checks the sign-in
at the edge and passes the caller on as headers signed with a secret the service
shares with it. Standard library only: a service verifies them without installing
the framework.

What is signed is the caller's id, name and roles, the request id and the moment,
each on its own line, so a value cannot be moved from one field into another and
a captured set of headers stops working once it is older than the service allows.
"""

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Tuple

__all__ = [
    "HEADER_PREFIX",
    "USER_ID",
    "USER_NAME",
    "ROLES",
    "TIMESTAMP",
    "SIGNATURE",
    "DEFAULT_MAX_AGE_SECONDS",
    "Caller",
    "IdentityError",
    "caller_of",
    "sign_identity",
    "verify_identity",
]

HEADER_PREFIX = "x-keepup-"
USER_ID = "X-Keepup-User-Id"
USER_NAME = "X-Keepup-User"
ROLES = "X-Keepup-Roles"
TIMESTAMP = "X-Keepup-Timestamp"
SIGNATURE = "X-Keepup-Signature"
REQUEST_ID = "X-Request-Id"

#: How old a signature a service accepts by default; the gateway signs each request
#: as it passes it on, so anything older is a replay or a clock gone wrong.
DEFAULT_MAX_AGE_SECONDS = 60


class IdentityError(ValueError):
    """The headers do not prove who is calling."""


@dataclass(frozen=True)
class Caller:
    """Who is calling, as the headers say it.

    Attributes:
        id: the account's id, as text.
        username: the account's name.
        roles: the roles the account holds.
        request_id: the request this identity was signed for.
    """

    id: str
    username: str
    roles: Tuple[str, ...]
    request_id: str


def caller_of(subject: Any) -> Caller:
    """The caller the kernel answered with, in the shape the headers carry.

    The kernel's subject is whatever the deployment's identity system returns; the
    framework's own is a dict with ``id``, ``username`` and ``roles`` (or the
    older ``role``). The gateway does not know how accounts are kept and does not
    ask their table.
    """
    get = subject.get if isinstance(subject, Mapping) else (
        lambda name, default=None: getattr(subject, name, default))
    roles = get("roles", None)
    if roles is None:
        role = get("role", None)
        roles = [role] if role else []
    if isinstance(roles, str):
        roles = [roles]
    return Caller(id=str(get("id", "") or ""), username=str(get("username", "") or ""),
                  roles=tuple(sorted(str(role) for role in roles if role)), request_id="")


def sign_identity(caller: Caller, secret: str, request_id: str,
                  now: Optional[float] = None) -> dict:
    """The headers that carry a caller to a service.

    Args:
        caller: who is calling.
        secret: the secret the gateway shares with its services.
        request_id: the request these headers go with.
        now: the moment, for a test.

    Returns:
        The five ``X-Keepup-*`` headers.
    """
    moment = str(int(time.time() if now is None else now))
    roles = ",".join(caller.roles)
    return {
        USER_ID: caller.id,
        USER_NAME: caller.username,
        ROLES: roles,
        TIMESTAMP: moment,
        SIGNATURE: _signature(secret, caller.id, caller.username, roles, request_id, moment),
    }


def verify_identity(headers: Mapping[str, str], secret: str,
                    max_age: float = DEFAULT_MAX_AGE_SECONDS,
                    now: Optional[float] = None) -> Caller:
    """Who is calling, if the gateway said so; a service calls this.

    Args:
        headers: the request's headers, in any case.
        secret: the secret shared with the gateway.
        max_age: how old a signature is still accepted, in seconds.
        now: the moment, for a test.

    Returns:
        The caller.

    Raises:
        IdentityError: when the headers are missing, altered or too old.
    """
    if not secret:
        raise IdentityError("no secret to verify with")
    read = _reader(headers)
    signature = read(SIGNATURE)
    moment = read(TIMESTAMP)
    if not signature or not moment:
        raise IdentityError("the request carries no signed identity")
    user_id, username, roles = read(USER_ID), read(USER_NAME), read(ROLES)
    request_id = read(REQUEST_ID)
    expected = _signature(secret, user_id, username, roles, request_id, moment)
    if not hmac.compare_digest(expected, signature):
        raise IdentityError("the identity's signature does not match")
    try:
        age = (time.time() if now is None else now) - int(moment)
    except ValueError:
        raise IdentityError("the identity's timestamp is not a number")
    if age > max_age or age < -max_age:
        raise IdentityError("the identity is too old")
    return Caller(id=user_id, username=username,
                  roles=tuple(role for role in roles.split(",") if role),
                  request_id=request_id)


def is_identity_header(name: str) -> bool:
    """Whether a header is one only the gateway may set."""
    return name.lower().startswith(HEADER_PREFIX)


def _signature(secret: str, *values: str) -> str:
    """The HMAC over the values, one per line."""
    material = "\n".join(values).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


def _reader(headers: Any) -> Callable[[str], str]:
    """A case-blind lookup over whatever mapping of headers was given."""
    if hasattr(headers, "get") and not isinstance(headers, dict):
        return lambda name: str(headers.get(name) or "")
    lowered = {str(key).lower(): value for key, value in dict(headers).items()}
    return lambda name: str(lowered.get(name.lower()) or "")
