"""The account here of somebody another system vouches for.

Two paths arrive with an outside identity: the OpenID Connect sign-in
(:mod:`keepup.auth.oidc_routes`) and the identity provider plugin
(:mod:`keepup.auth.identity`). Both have to find the account of that identity,
or make one, and they must do it the same way -- two ways of matching an
outsider to an account are two chances to hand somebody the wrong one.

The match is on the pair (source, subject): ``users.auth_source`` and
``users.external_id``, under a partial unique index. Never on the name or the
address, which change hands at the source; matching on them would let a new
employee into the account of the one whose address they inherited.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional

from keepup_auth import user_roles
from keepup_auth.usernames import safe_username
from keepup.db import DatabaseManagerV2

logger = logging.getLogger(__name__)

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "AccountUnavailable",
    "NOT_A_HASH",
    "create_account",
    "find_account",
    "free_username",
    "propose_username",
]

#: The password column of an account that has no password here. Not blank: a
#: blank one is something a local sign-in might one day accept; this is not a
#: hash of anything, so no password ever matches it.
NOT_A_HASH = "!external"

#: The columns an account is read with -- everything a route may want from
#: ``current_user``, and not the password column.
_COLUMNS = ("id, username, email, phone, full_name, agree_terms, status, role, "
            "created_at, updated_at, auth_source, external_id")

#: How many free names are tried when another request takes each one first.
_NAME_ATTEMPTS = 5


class AccountUnavailable(RuntimeError):
    """The account could be neither found nor made."""


def find_account(source: str, subject: str) -> Optional[Dict[str, Any]]:
    """The account tied to this source's subject, if there is one."""
    rows = DatabaseManagerV2.execute(
        f"SELECT {_COLUMNS} FROM users "
        "WHERE auth_source = :source AND external_id = :subject",
        {"source": source, "subject": str(subject)},
    )
    return dict(rows[0]) if rows else None


def propose_username(*candidates: Optional[str]) -> str:
    """A name for a new account: the first candidate there is, made plain.

    Whatever the source allows, a name here keeps to the shape the panel shows
    back (keepup-62).
    """
    for value in candidates:
        if value:
            return safe_username(str(value).strip().lower())
    return "user"


def free_username(proposed: str) -> str:
    """The proposed name, or the first free variant of it.

    Names are unique here and come from somewhere else entirely; two sources,
    or two people at one source, can propose the same one.
    """
    candidate = proposed
    suffix = 1
    while DatabaseManagerV2.execute_one(
            "SELECT id FROM users WHERE username = :name", {"name": candidate}):
        suffix += 1
        candidate = f"{proposed}-{suffix}"
    return candidate


def create_account(source: str, subject: str, proposed_username: str,
                   roles: Iterable[str], status: str = "active",
                   email: Optional[str] = None,
                   full_name: Optional[str] = None) -> Dict[str, Any]:
    """Make the account of this subject, or return the one a parallel request made.

    Two first requests of one person can arrive at two replicas at once. The
    unique index on (source, subject) lets only one insert through; the other
    finds that account instead of failing. A name taken in the same moment is
    retried with the next free variant.

    Raises:
        AccountUnavailable: the account could not be made or read back.
    """
    roles = list(roles)
    for _ in range(_NAME_ATTEMPTS):
        username = free_username(proposed_username)
        try:
            DatabaseManagerV2.execute_commit(
                "INSERT INTO users (username, password_hash, status, role, email, "
                "full_name, auth_source, external_id, agree_terms) "
                "VALUES (:username, :password_hash, :status, :role, :email, "
                ":full_name, :source, :subject, :agree_terms)",
                {
                    "username": username,
                    "password_hash": NOT_A_HASH,
                    "status": status,
                    "role": user_roles.primary_role(roles),
                    "email": email,
                    "full_name": full_name,
                    "source": source,
                    "subject": str(subject),
                    "agree_terms": False,
                },
            )
        except Exception as error:
            existing = find_account(source, subject)
            if existing is not None:
                return existing
            logger.info("Account name %s was taken meanwhile (%s); trying the next one",
                        username, type(error).__name__)
            continue

        account = find_account(source, subject)
        if account is None:
            raise AccountUnavailable(f"the account of {source} was created but cannot be read back")
        # The set is the truth about who this is; the column above is its mirror
        # (keepup/auth/user_roles.py).
        user_roles.set_roles(account["id"], roles, checked=False)
        logger.info("Account %s created for a subject of %s", account["username"], source)
        return account

    raise AccountUnavailable(f"no free name for a new account of {source}")
