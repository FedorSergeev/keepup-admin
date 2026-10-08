from keepup.tests.repository import source_of
"""Nobody decides by the mirror: the set is what a role question reads.

Task keepup-83. `users.role` is a deprecated mirror of the set in `user_roles`:
ADMIN when it is held, otherwise the first role granted. It is written in one
transaction with the set, it is still carried in the payloads so an older panel
keeps working, and it is *read* by nothing -- which is the whole point, because
`user["role"] == ROLE_ADMIN` is the mistake that hides a second role.

The first check is the one that matters: it fails the day somebody decides by the
mirror again.
"""

from pathlib import Path

from keepup.auth.user_roles import has_role

PACKAGE = Path(__file__).resolve().parents[1]

#: What a decision by the mirror looks like, in any of its spellings.
DECIDES_BY_THE_MIRROR = ('["role"] ==', "['role'] ==", '.get("role") ==', ".get('role') ==")

#: Where the mirror is written and carried on purpose, with its reason.
MAY_MENTION_IT = {
    "packages/keepup-auth/keepup_auth/user_roles.py": "it writes the mirror in the same transaction as the set",
    "packages/keepup-auth/keepup_auth/routes.py": "the payload carries it, marked deprecated, for an older panel",
    "packages/keepup-auth/keepup_auth/dependencies.py": "an account being built carries it, from the set",
    "packages/keepup-auth/keepup_auth/external_accounts.py": "an account being built carries it, from the set",
    "packages/keepup-auth/keepup_auth/oidc_routes.py": "an account being built carries it, from the set",
    "packages/keepup-auth/keepup_auth/providers/base.py": "an account being created carries it, from the set",
    "packages/keepup-auth/keepup_auth/providers/local.py": "an account being created carries it, from the set",
    "migrations.py": "it fills the mirror from the set, and the set from the mirror",
    "schema.py": "the column is declared here until the mirror goes away",
}


def framework_files():
    """Every Python file of the framework, checks and artifacts aside.

    The sign-in's modules live in their distribution since keepup-124, and this
    guard has to keep watching them: a file that moved out of the base is not a
    file that stopped deciding about roles.
    """
    roots = [PACKAGE, PACKAGE / "packages"]
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            relative = str(path.relative_to(PACKAGE))
            parts = relative.split("/")
            if any(part.startswith(".") or part in
                   ("tests", "ci", "openspec", "doc", "build", "__pycache__")
                   for part in parts):
                continue
            yield path, relative


def test_nothing_decides_by_the_mirror():
    """`user["role"] == ROLE_ADMIN` is the mistake the set exists to prevent."""
    offenders = []
    for path, relative in framework_files():
        text = path.read_text(encoding="utf-8")
        for spelling in DECIDES_BY_THE_MIRROR:
            if spelling in text:
                offenders.append((relative, spelling))
    assert offenders == [], (
        f"these compare against the mirror: {offenders}. Ask has_role(user, role), "
        "which reads the set (AGENTS.md, Roles)."
    )


def test_the_mirror_of_a_user_is_only_read_where_it_is_meant_to_be():
    """A mention outside the list is a decision nobody wrote down.

    About the mirror *of a user*, not about the word: a section catalogue has a
    `role` of its own (`section_roles.role`), and reading that one is not this.
    """
    for path, relative in framework_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            mentions_a_user = "user" in line.lower() or "admin" in line.lower()
            if not mentions_a_user:
                continue
            if any(spelling in line for spelling in ('["role"]', ".get(\"role\")", "'role'")):
                if relative not in MAY_MENTION_IT:
                    raise AssertionError(
                        f"{relative}:{number} reads the role mirror of a user and is not "
                        "on the list: read the set, or say why here."
                    )


def test_the_decision_follows_the_set_and_not_the_mirror():
    """A person who holds two roles is not described by the mirror of one."""
    user = {"id": 1, "roles": ["CLIENT", "AUDITOR"], "role": "CLIENT"}
    assert has_role(user, "AUDITOR") is True
    assert has_role(user, "ADMIN") is False
    # The mirror disagrees, and it loses: the set is the fact.
    assert has_role({"id": 2, "roles": ["AUDITOR"], "role": "ADMIN"}, "ADMIN") is False


def test_the_payloads_still_carry_both_for_one_release():
    """An older panel reads the mirror; a newer one reads the set."""
    text = source_of("keepup.auth.routes").read_text(encoding="utf-8")
    assert text.count('"roles": current_user.get("roles", [])') >= 2
    assert text.count('"role": current_user["role"]') >= 2
    assert "deprecated mirror" in text
