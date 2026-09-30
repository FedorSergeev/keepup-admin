"""Whether somebody may take an action: one rule, reached three ways.

A plugin route declares ``permission``; an application route depends on
:func:`require_permission`; the old decorator in ``keepup.auth.permissions``
asks the same. All three end in :func:`check`, so a right means the same thing
whichever way a route was written.

Two modes, chosen by the deployment (``identity_provider.authorization``):

- ``local`` -- also what applies with no provider at all: the right is held by
  an administrator, by an account granted it in ``user_permissions``, or by
  somebody whose identity from the provider lists it;
- ``provider`` -- the provider decides every time (or from its cache, if the
  deployment gave the decisions one).

The door closes on doubt: a provider that fails or does not answer is a 503, and
the handler is never called.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping

from fastapi import Depends, HTTPException, Request, status

from keepup.auth import user_roles
from keepup.auth.identity import runtime as identity_runtime
from keepup.auth.identity.contract import AccessRequest, IdentityRejected, ProviderUnavailable
from keepup.db import DatabaseManagerV2
from keepup.roles import ROLE_ADMIN

logger = logging.getLogger(__name__)

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = ["UNAVAILABLE", "check", "refused", "require_permission"]

#: The answer when the provider could not be asked. Says nothing about why.
UNAVAILABLE = "The identity provider is unavailable"


def refused(permission: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                         detail=f"Permission '{permission}' required")


def unavailable() -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=UNAVAILABLE)


def _granted_here(user_id: Any, permission: str) -> bool:
    """Whether the account holds the right in this application's own table."""
    if user_id is None:
        return False
    row = DatabaseManagerV2.execute_one(
        "SELECT granted FROM user_permissions "
        "WHERE user_id = :user_id AND permission_name = :name",
        {"user_id": user_id, "name": permission})
    return bool(row and row.get("granted"))


async def _local(user: Mapping[str, Any], permission: str) -> bool:
    if user_roles.has_role(user, ROLE_ADMIN):
        return True
    identity = user.get("identity") or {}
    if permission in (identity.get("permissions") or ()):
        return True
    return await asyncio.to_thread(_granted_here, user.get("id"), permission)


async def check(user: Mapping[str, Any], request: AccessRequest) -> None:
    """Return if ``user`` may take this action; raise 403 or 503 otherwise."""
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication required")
    runtime = identity_runtime.current()
    if runtime is None or runtime.config.authorization == "local":
        if not await _local(user, request.permission):
            raise refused(request.permission)
        return

    try:
        allowed = await runtime.decide(user, request)
    except ProviderUnavailable:
        raise unavailable()
    except IdentityRejected as refusal:
        logger.info("Identity provider refused %s to %s: %s", request.permission,
                    user.get("username"), refusal)
        raise refused(request.permission)
    if not allowed:
        raise refused(request.permission)


def action_of(request: Request, permission: str) -> AccessRequest:
    """The action a request takes: its right, its method, its route's path."""
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    return AccessRequest(permission=permission, method=request.method, path=path,
                         path_params=dict(request.path_params))


def require_permission(permission: str):
    """A dependency: the signed-in user, if they may take this action.

    ``Depends(require_permission("reports.export"))`` on an application's route
    signs the caller in the usual way and then asks :func:`check`.
    """
    # Imported here: keepup.auth.dependencies reaches the provider through this
    # package, and a module-level import each way would be a cycle.
    from keepup.auth.dependencies import get_current_user

    async def dependency(request: Request, current_user: dict = Depends(get_current_user)):
        await check(current_user, action_of(request, permission))
        return current_user

    dependency.__name__ = f"require_permission_{permission}"
    return dependency
