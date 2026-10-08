"""Who is calling, and what they may do: the shapes the kernel owns.

The kernel never learns how a deployment signs people in -- a session cookie, a
token, somebody else's directory -- and it never checks a right itself. It owns
the *names*: the shape of the question ("may this actor take this action") and
the shape of the two services that answer it, ``auth`` (who is calling) and
``permissions`` (may they). A deployment puts an implementation behind them; a
transport hands the actor over on the call; a route declares the right it wants
(`doc/plugin_constructor.md` sections 4.4 and 6.0).

Until ``keepup-auth`` is a plugin of its own the framework's own sign-in
registers itself here at start-up, and this module is the single line the
kernel's other modules cross to reach it -- which is what the purity check
watches.
"""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Optional

__all__ = [
    "AccessRequest",
    "Identity",
    "SERVICE_AUTH",
    "SERVICE_PERMISSIONS",
    "checker",
    "clear_identity",
    "get_identity",
    "set_identity",
    "subject_dependency",
]

#: The service that answers who is calling.
SERVICE_AUTH = "auth"
#: The service that answers whether they may.
SERVICE_PERMISSIONS = "permissions"


@dataclass(frozen=True)
class AccessRequest:
    """The action somebody wants to take, as the provider is asked about it.

    Attributes:
        permission: the right the route declares.
        method: the method of the call, for a provider that decides by method.
        path: the route's path as declared.
        path_params: the values taken from the address.
    """

    permission: str
    method: str = ""
    path: str = ""
    path_params: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Identity:
    """What a deployment put behind ``auth`` and ``permissions``.

    Attributes:
        subject_dependency: the callable a transport uses to find the caller --
            for HTTP, the FastAPI dependency the panel's routes depend on.
        checker: ``await checker(actor, AccessRequest)`` -- raises to refuse, or
            returns anything truthy; None means this deployment cannot decide a
            right, and a route that wants one will not run.
        name: what to call it in a log line.
    """

    subject_dependency: Optional[Callable[..., Awaitable[Any]]] = None
    checker: Optional[Callable[..., Awaitable[Any]]] = None
    name: str = ""

    @property
    def can_decide(self) -> bool:
        """Whether this deployment can answer a question about a right."""
        return callable(self.checker)


#: What the deployment registered. One per process, and set at start-up: the
#: HTTP dependencies FastAPI was handed are module-level callables, and there is
#: nowhere else for them to reach the identity of the process they serve. When
#: the transport owns the application (keepup-123) the runtime hands the checker
#: to each call instead, and this becomes unnecessary.
_identity: Optional[Identity] = None


def set_identity(identity: Optional[Identity]) -> None:
    """Put an identity system behind the two services.

    Args:
        identity: what answers, or None to forget the previous one.
    """
    global _identity  # noqa: PLW0603 - the seam described above, and it is one name
    _identity = identity


def get_identity() -> Optional[Identity]:
    """What is behind the two services, or None."""
    return _identity


def clear_identity() -> None:
    """Forget the identity system: a process that no longer serves anything."""
    set_identity(None)


def subject_dependency() -> Optional[Callable[..., Awaitable[Any]]]:
    """The callable a transport uses to find the caller, or None."""
    return _identity.subject_dependency if _identity is not None else None


def checker() -> Optional[Callable[..., Awaitable[Any]]]:
    """What decides a route's right, or None when nobody can."""
    return _identity.checker if _identity is not None else None
