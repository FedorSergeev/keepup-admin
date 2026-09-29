"""The roles one account holds, and what they add up to.

A user used to have one role -- the ``role`` column -- and three different
decisions were taken from it: whether this is an administrator, which panel
sections the person sees, and which plugins are advertised to them. One column
cannot answer all three for somebody who both rents a machine out and rents one,
and switching the role in the panel answers by taking one ability away to give
another.

So the set lives here, in ``user_roles``, and it is the single source of truth.
The sections and the plugins of every role in the set are glued together
(``keepup.modules``, ``keepup.plugins.admin``), and the administrative right is
``ADMIN`` being in the set rather than one role being equal to it.

``users.role`` stays for one release as a **deprecated mirror** of the set:
``ADMIN`` when that role is held, otherwise the first role granted. That rule
looks arbitrary until one looks at why the field is read -- in every application
on this framework it is compared with ``ROLE_ADMIN`` -- so the mirror keeps
exactly that answer. It is written in one place, together with the set, and no
decision of the framework is taken from it. It goes away in keepup 0.3.0, once
the applications read ``roles``.

An empty set means "not filled in yet", never "no rights": a row created by code
that knows nothing of the set reads as one role from the mirror. Which is why
writing an empty set is refused -- access is taken away by blocking the account.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from sqlalchemy import text as sql_text

from keepup.db import DatabaseManagerV2
from keepup.roles import ROLE_ADMIN, ROLE_CLIENT

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "AdministratorKept",
    "attach_roles",
    "ensure_an_administrator_remains",
    "has_role",
    "held_by",
    "known_roles",
    "primary_role",
    "roles_of",
    "set_roles",
]

logger = logging.getLogger(__name__)

#: The roles the framework itself knows. A deployment's own roles come from the
#: section catalogue, which is where a role is declared in the first place.
FRAMEWORK_ROLES = (ROLE_ADMIN, ROLE_CLIENT)


class UnknownRole(ValueError):
    """A role nobody declared. Carries the names that are known, for the answer."""

    def __init__(self, name: str, known: Sequence[str]):
        self.name = name
        self.known = list(known)
        super().__init__(
            f"Unknown role '{name}'. The roles of this deployment are: "
            f"{', '.join(self.known)}")


class AdministratorKept(ValueError):
    """The change would leave the deployment, or the one making it, without ADMIN."""


class EmptyRoleSet(ValueError):
    """Nobody may be left with no role at all."""

    def __init__(self):
        super().__init__(
            "A user holds at least one role. To take access away, block the "
            "account instead of emptying its roles.")


def known_roles() -> List[str]:
    """Every role this deployment has, in a stable order.

    The section catalogue is where a role comes into being: a grant in
    ``role_modules`` names it, and the panel offers it. The framework's own two
    are always there, including on a deployment whose catalogue has no grant for
    one of them yet.

    Returns:
        The role names, sorted.
    """
    names = set(FRAMEWORK_ROLES)
    try:
        for row in DatabaseManagerV2.execute(
                "SELECT DISTINCT role_name FROM role_modules") or []:
            name = (row["role_name"] or "").strip()
            if name:
                names.add(name)
    except Exception as error:
        # A deployment being brought up has no catalogue yet. The framework's
        # own roles are still an answer, and a wrong refusal here would keep an
        # administrator from granting anything at all.
        logger.warning("Could not read the roles of the section catalogue: %s", error)
    return sorted(names)


def _resolve(name: str, known: Sequence[str]) -> str:
    """The known spelling of this role, or a refusal.

    ``role_modules`` is joined by the exact name, so a role written in another
    case would look granted in the panel and grant nothing. The comparison
    ignores case and what is stored is the spelling the deployment declared.

    Args:
        name: the role as it was asked for.
        known: the roles this deployment has.

    Returns:
        The declared spelling.

    Raises:
        UnknownRole: nothing declares this role.
    """
    asked = (name or "").strip()
    for candidate in known:
        if candidate.lower() == asked.lower():
            return candidate
    raise UnknownRole(asked, known)


def normalise(names: Iterable[str], known: Optional[Sequence[str]] = None,
              checked: bool = True) -> List[str]:
    """The set as it will be stored: declared spellings, no repeats, ordered.

    Args:
        names: the roles as they were asked for.
        known: the roles this deployment has; read when omitted.
        checked: whether an undeclared role is refused. False for a role that
            came from the deployment's own configuration rather than from
            somebody typing it -- see set_roles().

    Returns:
        The role names, sorted.

    Raises:
        EmptyRoleSet: nothing was asked for.
        UnknownRole: one of the names is not declared anywhere and checked.
    """
    if checked:
        known = list(known) if known is not None else known_roles()
        resolved = {_resolve(name, known) for name in names if (name or "").strip()}
    else:
        resolved = {(name or "").strip() for name in names if (name or "").strip()}
    if not resolved:
        raise EmptyRoleSet()
    return sorted(resolved)


def _other_active_administrators(user_id: int) -> int:
    """Active accounts other than this one that hold ADMIN -- in the set, or
    in the mirror for an account whose set is still empty (see roles_of)."""
    row = DatabaseManagerV2.execute_one(
        "SELECT COUNT(*) AS n FROM users u WHERE u.id <> :id AND u.status = 'active' AND ("
        " EXISTS (SELECT 1 FROM user_roles r WHERE r.user_id = u.id AND r.role_name = :admin)"
        " OR (u.role = :admin AND NOT EXISTS (SELECT 1 FROM user_roles r WHERE r.user_id = u.id)))",
        {"id": user_id, "admin": ROLE_ADMIN})
    return int((row or {}).get("n") or 0)


def ensure_an_administrator_remains(user_id: int, roles: Sequence[str],
                                    actor_id: Optional[int]) -> None:
    """Refuse a change that takes ADMIN from its author or from the last holder.

    An administrator who took the role from themselves, or from the last
    account holding it, left the deployment with nobody able to give it back
    short of editing the database (keepup-71). Taking it from another
    administrator while one remains is an ordinary decision.

    Two such changes made at the same moment could still both pass; the window
    is one request wide and the answer to it is the database, as before.

    Raises:
        AdministratorKept: the change is one of the two above.
    """
    if ROLE_ADMIN in roles or ROLE_ADMIN not in roles_of(user_id):
        return
    if actor_id is not None and int(actor_id) == int(user_id):
        raise AdministratorKept(
            "An administrator cannot take the administrator role from themselves. "
            "Ask another administrator to do it.")
    if _other_active_administrators(user_id) == 0:
        raise AdministratorKept(
            "This is the last active administrator. Give the role to another "
            "account first.")


def primary_role(roles: Sequence[str]) -> str:
    """The deprecated ``users.role`` mirror for this set.

    ``ADMIN`` when it is held, otherwise the first role of the set. Every reader
    of the old field asks the same question -- is this an administrator -- and
    this keeps that answer true.

    Args:
        roles: the set, already normalised.

    Returns:
        The role to mirror, or CLIENT for an empty set.
    """
    for role in roles:
        if role == ROLE_ADMIN:
            return ROLE_ADMIN
    return roles[0] if roles else ROLE_CLIENT


def roles_of(user_id: int, mirror: Optional[str] = None) -> List[str]:
    """The roles this account holds.

    Args:
        user_id: the account.
        mirror: its ``users.role``, when the caller has already read it. Saves
            the read below for an account whose set has not been filled in.

    Returns:
        The role names, sorted; never empty.
    """
    rows = []
    try:
        rows = DatabaseManagerV2.execute(
            "SELECT role_name FROM user_roles WHERE user_id = :id", {"id": user_id}) or []
    except Exception as error:
        # The table is created at start-up; a deployment mid-upgrade may not
        # have it yet, and the mirror is a complete answer for one role.
        logger.warning("Could not read the roles of user %s: %s", user_id, error)
    held = sorted({(row["role_name"] or "").strip() for row in rows} - {""})
    if held:
        return held

    # Nothing granted: the account predates the set, and the mirror is what it
    # was judged by until now. Read only on this path -- it does not happen on a
    # deployment whose start has filled the sets in.
    if not (mirror or "").strip():
        try:
            row = DatabaseManagerV2.execute_one(
                "SELECT role FROM users WHERE id = :id", {"id": user_id})
            mirror = (row or {}).get("role")
        except Exception as error:
            logger.warning("Could not read the role of user %s: %s", user_id, error)
    return [(mirror or "").strip() or ROLE_CLIENT]


def held_by(user: Optional[Mapping[str, Any]]) -> List[str]:
    """The roles of a user the caller already has in hand.

    A user the framework handed out carries its set. One an application or a test
    assembled itself may carry only the deprecated single role, and that is still
    an answer -- which is why this rule lives in one function rather than at each
    of the places that decide by it.

    Args:
        user: the user as a route receives it, or None.

    Returns:
        The role names; empty only for a user that names none.
    """
    roles = (user or {}).get("roles")
    if roles:
        return list(roles)
    single = (user or {}).get("role")
    return [single] if single else []


def has_role(user: Optional[Mapping[str, Any]], role: str) -> bool:
    """Whether this user holds the role.

    Args:
        user: the user as the framework hands it to a route, or None.
        role: the role to look for.

    Returns:
        True when the role is among the ones held.
    """
    return role in held_by(user)


def attach_roles(user: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Put the set on a user just read from the database.

    Called where the account itself is read -- one more statement in the worker
    thread that read it, not a second hop out of the event loop (keepup-43). The
    set is deliberately not cached: a cache of rights keeps a revoked role
    working until it expires, and the sections cache is keyed by role name, so
    the gluing still reads the database once per role rather than once per
    person.

    Args:
        user: the row, or None.

    Returns:
        The same mapping as a dict with ``roles`` on it, or None.
    """
    if user is None:
        return None
    user = dict(user)
    user["roles"] = roles_of(user.get("id"), user.get("role"))
    return user


def set_roles(user_id: int, names: Iterable[str], checked: bool = True) -> List[str]:
    """Replace the set this account holds, and the mirror with it.

    Args:
        user_id: the account.
        names: the roles it is to hold.
        checked: whether an undeclared role is refused. The administrator's route
            checks, because a name typed there that nothing declares grants
            nothing and looks granted. The framework's own paths do not: the role
            of an account created on sign-in comes from the deployment's own
            policy, and refusing a sign-in because a section grant is missing
            would be a worse answer than a role that shows no sections.

    Returns:
        The set as it was stored.

    Raises:
        EmptyRoleSet: the set is empty.
        UnknownRole: one of the names is not declared anywhere and checked.
    """
    roles = normalise(names, checked=checked)

    # One transaction: between the delete and the inserts the account holds no
    # role at all, and a request arriving in that moment would be judged by an
    # empty set.
    with DatabaseManagerV2.get_session() as session:
        session.execute(sql_text("DELETE FROM user_roles WHERE user_id = :id"),
                        {"id": user_id})
        for role in roles:
            session.execute(
                sql_text("INSERT INTO user_roles (user_id, role_name) VALUES (:id, :role)"),
                {"id": user_id, "role": role})
        # The mirror is written here and nowhere else, which is what keeps two
        # representations of one fact from drifting.
        session.execute(
            sql_text("UPDATE users SET role = :role, updated_at = CURRENT_TIMESTAMP "
                     "WHERE id = :id"),
            {"role": primary_role(roles), "id": user_id})
    return roles


def fill_from_mirror(cursor, is_postgres: bool) -> int:
    """Give every account with no roles the one its mirror names.

    Run at start-up. Idempotent: an account that already holds a role is not
    touched, so the second start writes nothing. Without this the release day
    would find ``user_roles`` empty and everybody relying on the fallback in
    roles_of() -- which works, but leaves the panel with nothing to show.

    Args:
        cursor: an open cursor on the users table.
        is_postgres: whether the connection is PostgreSQL.

    Returns:
        How many accounts were given a role.
    """
    query = (
        "INSERT INTO user_roles (user_id, role_name) "
        "SELECT u.id, COALESCE(NULLIF(TRIM(u.role), ''), ?) FROM users u "
        "WHERE NOT EXISTS (SELECT 1 FROM user_roles r WHERE r.user_id = u.id)"
    )
    cursor.execute(query.replace("?", "%s") if is_postgres else query, (ROLE_CLIENT,))
    filled = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
    if filled:
        logger.info("Gave %s account(s) the role their 'role' column named", filled)
    return filled
