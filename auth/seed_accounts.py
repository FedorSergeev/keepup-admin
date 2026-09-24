"""The accounts an application creates for itself on the first start.

There are two. `admin` is a person's first way in: without it nobody can sign
into a fresh database and there would be nothing to bring the panel up with.
`system` is not a person but a signature: background jobs record their work
under somebody's name and need a row in `users`.

Both used to be created with a password typed into the source: `admin123` for
the first and `system_internal_use_only_123` for the second, while two other
places that created the same `system` also typed `system_password`. A password
in a repository is not a password: it is known to everyone who has seen the
code, it is the same on every deployment, and it does not change because the
deployment became reachable from outside. `admin123` is on top of that in the
breach lists, and the browser warns about it at every sign-in -- truthfully.

Three decisions follow.

**The administrator's password is set by whoever deploys, not by the code.** It
is read from `ADMIN_INITIAL_PASSWORD`; without that variable it is generated at
random and printed once into the start-up log -- that is exactly where it can
be read, and it differs on every deployment. A default fit for everyone does
not exist here.

**`system` has no password at all.** Nobody signs in as it, so it is given a
random value known to no one, ourselves included, and a status that sign-in
rejects. The account stays a signature and stops being a way in.

**One place creates the accounts.** Three implementations of the same thing had
already drifted apart in password, role and even in what to write into
`status`; when one of them is fixed, the others go on creating the hole.

Existing databases are not healed by this on their own: `system` is already
there, and the creating code does not look at it. So a known password on an
existing row is retired at start-up -- see `retire_known_system_password`.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Iterable, Mapping, Optional, Sequence, Tuple

import bcrypt

logger = logging.getLogger(__name__)

#: The first administrator's password, when whoever deploys wants to set it.
ADMIN_PASSWORD_ENV = "ADMIN_INITIAL_PASSWORD"

ADMIN_USERNAME = "admin"
SYSTEM_USERNAME = "system"

#: The status sign-in refuses: it admits `active` only
#: (`keepup/auth/routes.py`). The same trick is already used for the platform's
#: own account in token settlement.
SYSTEM_STATUS = "system"

#: The passwords earlier builds handed `system` -- all three, because there
#: were three places creating it. An existing row holding any of them stops
#: being a way in at the next start.
RETIRED_SYSTEM_PASSWORDS: Tuple[str, ...] = (
    "system_internal_use_only_123",
    "system_password",
)

#: The same for the administrator. Its password is not overwritten silently --
#: somebody may be working with the deployment right now -- but it is named at
#: every start.
RETIRED_ADMIN_PASSWORDS: Tuple[str, ...] = ("admin123",)

#: Length of a generated password, in bytes of randomness before encoding.
GENERATED_PASSWORD_BYTES = 18


# --- decisions that can be checked without a database ----------------------


def generated_password(nbytes: int = GENERATED_PASSWORD_BYTES) -> str:
    """A random password fit both for typing by hand and for pasting.

    Args:
        nbytes: bytes of randomness before encoding.

    Returns:
        The password.
    """
    return secrets.token_urlsafe(nbytes)


def initial_admin_password(environ: Optional[Mapping[str, str]] = None) -> Tuple[str, bool]:
    """The first administrator's password, and whether it was generated.

    An empty variable is not a password that was set, it is a variable that was
    forgotten: an empty password cannot be signed in with, and accepting it
    silently would mean creating an account nobody can use.

    Args:
        environ: the environment to read; the process environment when omitted.

    Returns:
        A pair (password, generated), where generated says it has to be
        announced because nobody else knows it.
    """
    if environ is None:
        environ = os.environ
    given = (environ.get(ADMIN_PASSWORD_ENV) or "").strip()
    if given:
        return given, False
    return generated_password(), True


def unusable_password() -> str:
    """A password nobody knows: it does not leave this function.

    Returns:
        The value to store as a hash for an account that must not be a way in.
    """
    return secrets.token_urlsafe(32)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def matches_any(password_hash: Optional[str], candidates: Iterable[str]) -> bool:
    """Whether the hash matches one of the known retired passwords.

    A hash that cannot be read (empty, from another scheme, a marker left by an
    external provider) is not a match but a case that is not ours: the answer
    is a silent no, because the only action here is retiring a known password,
    and retiring somebody else's scheme is not ours to do.

    Args:
        password_hash: the stored hash, whatever it holds.
        candidates: the retired passwords to check against.

    Returns:
        True when one of them matches.
    """
    if not password_hash:
        return False
    for candidate in candidates:
        try:
            if bcrypt.checkpw(candidate.encode("utf-8"), password_hash.encode("utf-8")):
                return True
        except (ValueError, TypeError):
            return False
    return False


def announce_generated_admin_password(password: str, log=logger) -> None:
    """The only place a generated password is shown to a person.

    Args:
        password: the generated password.
        log: the logger to announce through.
    """
    log.warning(
        "Created the administrator account '%s' with a generated password: %s\n"
        "It is stored nowhere else and will not be shown again. Sign in and "
        "change it, or set %s before the first start.",
        ADMIN_USERNAME, password, ADMIN_PASSWORD_ENV,
    )


def warn_about_retired_admin_password(log=logger) -> None:
    """Say out loud what would otherwise be learned from the browser.

    Args:
        log: the logger to warn through.
    """
    log.warning(
        "Account '%s' still carries a password from earlier builds. It is the "
        "same on every deployment, known to everyone who has seen the source, "
        "and is in the breach lists. Change it in the panel: the Users section "
        "-> Change password.",
        ADMIN_USERNAME,
    )


# --- what is written to the database ---------------------------------------


def _sql(query: str, is_postgres: bool) -> str:
    """Queries are written with `?`; PostgreSQL gets `%s`, as elsewhere in the schema.

    Args:
        query: the query as written.
        is_postgres: whether the connection is PostgreSQL.

    Returns:
        The query in the dialect of that connection.
    """
    return query.replace("?", "%s") if is_postgres else query


def _scalar(row) -> int:
    """The row's first column, however the cursor returns the row.

    Args:
        row: a row, as a mapping or a sequence, or nothing.

    Returns:
        The value, or 0 when there is no row.
    """
    if not row:
        return 0
    if hasattr(row, "get") and callable(getattr(row, "get")):
        return row.get("count", 0) or 0
    return row[0] if len(row) > 0 else 0


def _exists(cursor, username: str, is_postgres: bool) -> bool:
    cursor.execute(_sql("SELECT COUNT(*) as count FROM users WHERE username = ?", is_postgres),
                   (username,))
    return _scalar(cursor.fetchone()) > 0


def _insert(cursor, *, username: str, password_hash: str, status: str, role: str,
            auth_source: str, is_postgres: bool) -> None:
    cursor.execute(
        _sql("INSERT INTO users (username, password_hash, status, role, auth_source) "
             "VALUES (?, ?, ?, ?, ?)", is_postgres),
        (username, password_hash, status, role, auth_source),
    )


def ensure_admin(cursor, *, role: str, auth_source: str, is_postgres: bool,
                 environ: Optional[Mapping[str, str]] = None) -> None:
    """Create the administrator when absent; otherwise check its password.

    An existing password is left alone: somebody may be working with the
    deployment right now, and changing it from a background procedure would cut
    a person off from their own panel.

    Args:
        cursor: an open cursor on the users table.
        role: the role to give a newly created account.
        auth_source: what to record as the account's origin.
        is_postgres: whether the connection is PostgreSQL.
        environ: the environment to read the password from.
    """
    if not _exists(cursor, ADMIN_USERNAME, is_postgres):
        password, generated = initial_admin_password(environ)
        _insert(cursor, username=ADMIN_USERNAME, password_hash=hash_password(password),
                status="active", role=role, auth_source=auth_source,
                is_postgres=is_postgres)
        if generated:
            announce_generated_admin_password(password)
        else:
            logger.info("Created the administrator account '%s' with the given password",
                        ADMIN_USERNAME)
        return

    cursor.execute(_sql("SELECT password_hash FROM users WHERE username = ?", is_postgres),
                   (ADMIN_USERNAME,))
    row = cursor.fetchone()
    stored = row.get("password_hash") if hasattr(row, "get") else (row[0] if row else None)
    if matches_any(stored, RETIRED_ADMIN_PASSWORDS):
        warn_about_retired_admin_password()


def ensure_system_user(cursor, *, role: str, auth_source: str, is_postgres: bool) -> None:
    """Create the signature background jobs sign with -- with no usable password.

    Args:
        cursor: an open cursor on the users table.
        role: the role to give the account.
        auth_source: what to record as the account's origin.
        is_postgres: whether the connection is PostgreSQL.
    """
    if _exists(cursor, SYSTEM_USERNAME, is_postgres):
        retire_known_system_password(cursor, is_postgres=is_postgres)
        return

    _insert(cursor, username=SYSTEM_USERNAME,
            password_hash=hash_password(unusable_password()),
            status=SYSTEM_STATUS, role=role, auth_source=auth_source,
            is_postgres=is_postgres)
    logger.info("Created the service account '%s'; it cannot be signed in as",
                SYSTEM_USERNAME)


def retire_known_system_password(cursor, *, is_postgres: bool,
                                 candidates: Sequence[str] = RETIRED_SYSTEM_PASSWORDS) -> bool:
    """Retire the `system` password in a database left by earlier builds.

    Idempotent: the second time round the password matches none of the known
    ones and no statement runs.

    Args:
        cursor: an open cursor on the users table.
        is_postgres: whether the connection is PostgreSQL.
        candidates: the retired passwords to look for.

    Returns:
        Whether anything had to be changed.
    """
    cursor.execute(_sql("SELECT password_hash, status FROM users WHERE username = ?", is_postgres),
                   (SYSTEM_USERNAME,))
    row = cursor.fetchone()
    if not row:
        return False
    if hasattr(row, "get") and callable(getattr(row, "get")):
        stored, status = row.get("password_hash"), row.get("status")
    else:
        stored, status = row[0], row[1]

    if not matches_any(stored, candidates) and status == SYSTEM_STATUS:
        return False

    cursor.execute(
        _sql("UPDATE users SET password_hash = ?, status = ? WHERE username = ?", is_postgres),
        (hash_password(unusable_password()), SYSTEM_STATUS, SYSTEM_USERNAME),
    )
    logger.warning(
        "Service account '%s' carried a password from earlier builds; it has "
        "been retired and the account can no longer be signed in as",
        SYSTEM_USERNAME,
    )
    return True
