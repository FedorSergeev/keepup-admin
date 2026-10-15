"""The framework decides by the set of roles, not by one role (keepup-60).

Since keepup-51 a user holds a set of roles, and `users.role` was a deprecated
mirror of it: written with the set, read by applications, and answered for one
more release. Some framework code still decided by it -- the lock endpoints
refused an administrator whose mirror said otherwise -- and nothing does now. The
column itself is gone (keepup-157).

    python3 -m pytest keepup/tests/role_mirror_decisions_tests.py -v
"""

import ast
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup_auth import dependencies, user_roles
from keepup_auth.dependencies import get_current_admin
from keepup_http.lock_routes import register_lock_routes
from keepup.roles import ROLE_ADMIN, ROLE_CLIENT

PACKAGE = Path(__file__).resolve().parents[1]

#: Where one role of a user may still be read, with the reason: the bridge for a
#: user somebody assembled by hand (`held_by`), the one-time read of the legacy
#: column (the backfill), and the answers that carry a computed role.
READERS = {
    "packages/keepup-auth/keepup_auth/user_roles.py",
    "packages/keepup-auth/keepup_auth/routes.py",
}


def framework_files():
    """Every Python file of the framework, the same set the other guard walks.

    The sign-in's modules moved into their distribution (keepup-124) and a guard
    that walks only the base stops watching them -- which is how this one missed
    `user_roles.py` for two releases.
    """
    seen = set()
    for root in (PACKAGE, PACKAGE / "packages"):
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(PACKAGE).as_posix()
            if any(part in ("tests", "ci", "openspec", "doc", "build", "__pycache__")
                   or part.startswith(".") for part in path.relative_to(PACKAGE).parts):
                continue
            if relative in seen:
                continue
            seen.add(relative)
            yield path, relative


def mirror_reads(path):
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and node.slice.value == "role":
            found.append(node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and node.args \
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "role":
            found.append(node.lineno)
    return found


def test_no_framework_decision_reads_one_role():
    offenders = []
    for path, relative in framework_files():
        if relative in READERS:
            continue
        offenders += [f"{relative}:{line}" for line in mirror_reads(path)]
    assert offenders == []


def test_a_field_that_disagrees_with_the_set_loses():
    """An administrator reaches the locks while an older payload field says not.

    That field is what a caller assembled, not what the framework stores: the set
    is the fact, and the endpoint asks `has_role` (AGENTS.md, Roles).
    """
    uid = dependencies.save_user_to_db("mirror-lags", "a-long-password-60")
    dependencies.update_user(uid, status="active")
    user_roles.set_roles(uid, [ROLE_ADMIN])
    admin = user_roles.attach_roles(dependencies.get_user_by_id(uid))
    admin["role"] = ROLE_CLIENT

    app = FastAPI()
    register_lock_routes(app)
    app.dependency_overrides[get_current_admin] = lambda: admin
    assert TestClient(app).get("/api/admin/locks").status_code == 200
