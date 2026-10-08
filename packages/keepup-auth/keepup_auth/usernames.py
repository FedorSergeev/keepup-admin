"""What a user name may be.

A name is shown back to administrators in the panel, written into logs and
passed to other services; one with markup or quotes in it ran as a script in the
session of the administrator who opened the users list (keepup-62). The panel
escapes what it shows, and the name is held to a plain shape at the door as
well: letters of any alphabet, digits and ``. _ @ + -``, at most 64 characters.
Accounts that already exist are not touched -- the rule applies when an account
is created.
"""

import re

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "USERNAME_MAX_LENGTH",
    "is_valid_username",
    "safe_username",
]

USERNAME_MAX_LENGTH = 64
_ALLOWED = re.compile(r"^[\w.@+-]+$")
_NOT_ALLOWED = re.compile(r"[^\w.@+-]+")


def is_valid_username(name) -> bool:
    """Whether this is a name an account may be created with."""
    return (isinstance(name, str) and 0 < len(name) <= USERNAME_MAX_LENGTH
            and bool(_ALLOWED.match(name)))


def safe_username(name: str, fallback: str = "user") -> str:
    """The name made to fit the rule: what it may not contain becomes ``_``."""
    cleaned = _NOT_ALLOWED.sub("_", str(name or "")).strip("_")[:USERNAME_MAX_LENGTH]
    return cleaned or fallback
