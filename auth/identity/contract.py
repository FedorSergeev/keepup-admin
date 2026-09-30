"""What a plugin implements to put somebody else's identity system behind keepup.

An application built on keepup is sometimes installed inside a larger system
that already knows who everybody is and what each of them may do, and answers
those questions through an API of its own. The framework cannot know that API,
so it states the three questions it needs answered and lets a plugin -- one per
such system -- translate them:

- **is this token good, and whose is it** (``verify_token``) -- the service is
  called with the other system's tokens;
- **are this name and password good** (``verify_password``) -- a person signs in
  to the panel with the credentials the other system holds;
- **may this person do this** (``decide``) -- the other system owns the rights.

Each is optional: a plugin implements what its system can do, and the start
refuses a deployment that switched on a mode the plugin cannot serve, rather
than failing on the first request.

A plugin says "no" in exactly two ways. :class:`IdentityRejected` means the
other system answered and the answer is no. :class:`ProviderUnavailable` means
it did not answer. They lead to different replies -- 401 or 403 against 503 --
because a client told its token is bad throws the token away, and it must not
do that because somebody else's server was down. Anything else a plugin raises
is treated as unavailability: an error in code the framework did not write must
neither open the door nor reach the caller as a 500 with its text.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple

#: What an application or a plugin may import from this module. Everything else
#: is internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "AccessRequest",
    "CAPABILITIES",
    "ExternalIdentity",
    "IdentityProvider",
    "IdentityRejected",
    "ProviderUnavailable",
]


class IdentityRejected(Exception):
    """The provider answered, and the answer is no.

    The message is for the log. The caller gets one answer whatever the reason:
    telling somebody which check they failed tells them which ones they passed.
    """


class ProviderUnavailable(Exception):
    """The provider did not answer: unreachable, too slow, or broken."""


@dataclass(frozen=True)
class ExternalIdentity:
    """Somebody the other system vouches for.

    ``subject`` is the one field that matters: the stable identifier of this
    person in the other system. The account here is found by it, together with
    the provider's name, and never by the name or the address, which change
    hands (doc/external_sign_in.md explains why for OIDC; the reasoning is the
    same).
    """

    subject: str
    username: Optional[str] = None
    email: Optional[str] = None
    full_name: Optional[str] = None
    #: The other system's own role names; the deployment maps them onto roles
    #: here in its configuration.
    roles: Tuple[str, ...] = ()
    #: Rights the other system states outright, if it does. In the local
    #: authorisation mode a right listed here is a right granted.
    permissions: Tuple[str, ...] = ()
    #: When the other system stops vouching. A cached answer never outlives it.
    expires_at: Optional[datetime] = None
    #: Whatever else the plugin wants its own ``decide`` to see later.
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def public(self) -> Dict[str, Any]:
        """A JSON-safe copy, for ``current_user`` and anything that serialises it."""
        return {
            "subject": self.subject,
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "roles": list(self.roles),
            "permissions": list(self.permissions),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "attributes": dict(self.attributes),
        }

    @classmethod
    def from_public(cls, data: Mapping[str, Any]) -> "ExternalIdentity":
        """The identity back from :meth:`public`."""
        expires = data.get("expires_at")
        return cls(
            subject=data["subject"],
            username=data.get("username"),
            email=data.get("email"),
            full_name=data.get("full_name"),
            roles=tuple(data.get("roles") or ()),
            permissions=tuple(data.get("permissions") or ()),
            expires_at=datetime.fromisoformat(expires) if expires else None,
            attributes=dict(data.get("attributes") or {}),
        )


@dataclass(frozen=True)
class AccessRequest:
    """The action somebody wants to take, as the provider is asked about it."""

    permission: str
    method: str = ""
    path: str = ""
    path_params: Mapping[str, Any] = field(default_factory=dict)


#: The three things a provider may be able to do, by the name of the method.
CAPABILITIES = ("verify_token", "verify_password", "decide")


class IdentityProvider(ABC):
    """The base of every provider plugin.

    A plugin overrides the methods its system can answer and leaves the others
    alone; :meth:`supports` tells them apart. It is constructed once, at
    start-up, with the ``settings`` of its configuration section -- environment
    references already substituted -- and closed at shutdown.

    Every method is a coroutine and is awaited under the timeout of the
    deployment's configuration: a plugin need not add its own, though a client
    with one of its own does no harm.
    """

    def __init__(self, settings: Mapping[str, Any]):
        self.settings = dict(settings or {})

    async def verify_token(self, token: str) -> ExternalIdentity:
        """Whose token this is. Raise IdentityRejected if it is not good."""
        raise NotImplementedError

    async def verify_password(self, username: str, password: str) -> ExternalIdentity:
        """Whose credentials these are. Raise IdentityRejected if they are wrong."""
        raise NotImplementedError

    async def decide(self, identity: Optional[ExternalIdentity], user: Mapping[str, Any],
                     request: AccessRequest) -> bool:
        """Whether this person may take this action.

        ``identity`` is None for somebody who did not come through this
        provider -- a local account; ``user`` is the account here either way.
        """
        raise NotImplementedError

    async def close(self) -> None:
        """Release what the plugin holds -- a client, a pool. Optional."""

    @classmethod
    def supports(cls, capability: str) -> bool:
        """Whether this provider implements ``capability`` (one of CAPABILITIES)."""
        if capability not in CAPABILITIES:
            raise ValueError(f"unknown capability {capability!r}")
        return getattr(cls, capability) is not getattr(IdentityProvider, capability)

    @classmethod
    def capabilities(cls) -> Tuple[str, ...]:
        """Everything this provider implements, in the order of CAPABILITIES."""
        return tuple(name for name in CAPABILITIES if cls.supports(name))
