"""The account here of somebody the provider vouches for, and the roles it holds.

Blocking: every function reads or writes the database and is called off the
event loop, in the same hop as the rest of the request's reads (keepup-43).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Set

from keepup.auth import external_accounts, user_roles
from keepup.auth.identity.config import IdentityProviderConfig
from keepup.auth.identity.contract import ExternalIdentity, IdentityRejected

logger = logging.getLogger(__name__)

#: Roles already found undeclared, so the warning is written once per role and
#: replica rather than on every request of every person who holds it.
_warned_undeclared: Set[str] = set()


def _warn_if_undeclared(roles) -> None:
    fresh = [role for role in roles if role not in _warned_undeclared]
    if not fresh:
        return
    known = set(user_roles.known_roles())
    for role in fresh:
        _warned_undeclared.add(role)
        if role not in known:
            # Written, not refused: the mapping is the deployment's own
            # decision, and a role nothing grants sections to only means the
            # person sees none -- a worse answer would be refusing them.
            logger.warning("identity_provider.role_mapping gives the role %s, which no "
                           "panel section grants; people holding it will see no sections",
                           role)


def account_for(config: IdentityProviderConfig, identity: ExternalIdentity) -> Dict[str, Any]:
    """The account of this identity with its roles brought in step, or a refusal.

    Found by (provider name, subject). Somebody unknown gets an account only if
    the deployment's policy is ``create``. The roles are what the mapping makes
    of the identity's roles right now, so a role taken away in the other system
    is taken away here; the set is written only when it changed.

    The account's status is the caller's to judge: a blocked account is a 403,
    not a failed identity.

    Raises:
        IdentityRejected: no subject, or an unknown subject under ``refuse``.
        external_accounts.AccountUnavailable: the account could not be made.
    """
    if not identity.subject or not str(identity.subject).strip():
        raise IdentityRejected("the provider vouched for an identity with no subject")

    roles = config.mapped_roles(identity.roles)
    account = external_accounts.find_account(config.name, identity.subject)
    if account is None:
        if config.new_accounts != "create":
            raise IdentityRejected(
                f"{config.name} subject has no account here and new_accounts is refuse")
        account = external_accounts.create_account(
            config.name, identity.subject,
            external_accounts.propose_username(identity.username, identity.email,
                                               identity.subject),
            roles, email=identity.email, full_name=identity.full_name)
        _warn_if_undeclared(roles)
        account["roles"] = sorted(set(roles))
        return account

    held = user_roles.roles_of(account["id"])
    wanted = sorted(set(roles))
    if held != wanted:
        # Through the set, never the mirror alone: the provider decides what
        # this person is on every request, and a mirror moved on its own would
        # leave the framework deciding by the previous roles.
        user_roles.set_roles(account["id"], wanted, checked=False)
        _warn_if_undeclared(wanted)
        logger.info("Roles of %s from %s: %s -> %s", account["username"], config.name,
                    held, wanted)
        # Read again rather than patched by hand: the row carries the mirror
        # set_roles() just wrote, and this is no place to compute it a second time.
        account = external_accounts.find_account(config.name, identity.subject) or account
    account["roles"] = wanted
    return account
