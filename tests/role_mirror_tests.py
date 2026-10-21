"""Nobody decides by one role, and the stored mirror is gone.

Task keepup-83, rewritten for keepup-157. `users.role` was a deprecated mirror of
the set in `user_roles`: ADMIN when it was held, otherwise the first role
granted. The column is gone -- not declared, not written, not selected.

Two mentions remain on purpose, and both are named below rather than allowed
silently: the answer of `/api/auth/me` computes a `role` from the set for the
callers that still read one, and `held_by()` answers a user an application or a
test assembled by hand, which carries one role and no set. Neither is a decision:
a decision asks `has_role(user, role)`, which reads the set.

The first check is the one that matters: it fails the day somebody decides by a
single role again.
"""

import re
from pathlib import Path

from keepup.tests.repository import NOT_THE_PACKAGE
from keepup_auth.user_roles import has_role
from keepup_users.tables import USERS

PACKAGE = Path(__file__).resolve().parents[1]

#: What a decision by one role looks like, in any of its spellings.
DECIDES_BY_ONE_ROLE = ('["role"] ==', "['role'] ==", '.get("role") ==', ".get('role') ==")

#: Where a single role is still mentioned, with its reason. Nowhere: the bridge
#: is gone (keepup-168).
MAY_MENTION_IT = {}


def framework_files():
    """Every Python file of the framework, checks and artifacts aside.

    The sign-in's modules live in their distribution since keepup-124, and this
    guard has to keep watching them: a file that moved out of the base is not a
    file that stopped deciding about roles.
    """
    roots = [PACKAGE, PACKAGE / "packages"]
    seen = set()
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            relative = str(path.relative_to(PACKAGE))
            # Everything that is not the package, except the capabilities: this
            # guard watches them too (keepup-124).
            skipped = NOT_THE_PACKAGE - {"packages"}
            if any(part in skipped for part in path.relative_to(PACKAGE).parts):
                continue
            # The first root already holds the second one: a file reached twice
            # is one file, and a list of offenders has to say so.
            if relative in seen:
                continue
            seen.add(relative)
            yield path, relative


def test_nothing_decides_by_one_role():
    """`user["role"] == ROLE_ADMIN` is the mistake the set exists to prevent."""
    offenders = []
    for path, relative in framework_files():
        text = path.read_text(encoding="utf-8")
        for spelling in DECIDES_BY_ONE_ROLE:
            if spelling in text:
                offenders.append((relative, spelling))
    assert offenders == [], (
        f"these compare against one role: {offenders}. Ask has_role(user, role), "
        "which reads the set (AGENTS.md, Roles)."
    )


def test_the_column_is_not_declared():
    """The declaration is where the mirror lived."""
    assert "role" not in {column.name for column in USERS.columns}, (
        "users.role is declared again; the set in user_roles is the truth")


def test_the_legacy_column_is_named_in_one_place():
    """A file that names a `users.role` column reads a column that is gone.

    The patterns are the shapes the column was written and read in -- `u.role`,
    a column list of an insert into `users`, `SET role` -- so that a parameter
    called `role` (the API body field, the role argument of a helper) is not
    caught. Exactly one file may match: the start-up backfill, which reads the
    column out of a database that predates the set.
    """
    patterns = (r"\bu\.role\b", r"(?i)users\s*\([^)]*\brole\b", r"(?i)\bSET\s+role\b",
                r"(?i)\brole\s*=\s*:role\b")
    naming = []
    for path, relative in framework_files():
        text = path.read_text(encoding="utf-8")
        if any(re.search(pattern, text) for pattern in patterns):
            naming.append(relative)
    assert naming == ["packages/keepup-auth/keepup_auth/user_roles.py"], (
        f"these name a users.role column: {naming}")


def test_the_decision_follows_the_set():
    """A person who holds two roles is not described by one of them."""
    user = {"id": 1, "roles": ["CLIENT", "AUDITOR"], "role": "CLIENT"}
    assert has_role(user, "AUDITOR") is True
    assert has_role(user, "ADMIN") is False
    # A user somebody assembled by hand names the set, or holds no role: the
    # single role left with the bridge (keepup-168).
    assert has_role({"id": 2, "role": "ADMIN"}, "ADMIN") is False
    assert has_role({"id": 3, "roles": ["AUDITOR"], "role": "ADMIN"}, "ADMIN") is False


def test_the_answers_name_the_set_and_no_role():
    """The field an older panel read is gone, from the answers and the shell."""
    auth = PACKAGE / "packages" / "keepup-auth" / "keepup_auth"
    routes = (auth / "routes.py").read_text(encoding="utf-8")
    dependencies = (auth / "dependencies.py").read_text(encoding="utf-8")
    assert '"role":' not in routes and '"role":' not in dependencies
    shell = (PACKAGE / "packages" / "keepup-http" / "keepup_http" / "static" / "js"
             / "main_new.js").read_text(encoding="utf-8")
    # `user.roles` is the set; `user.role` would be the bridge.
    assert "user.role" not in shell.replace("user.roles", "")
