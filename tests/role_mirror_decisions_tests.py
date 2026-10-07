"""The framework decides by the set of roles, not by the `role` mirror (keepup-60).

Since keepup-51 a user holds a set of roles and `users.role` is a deprecated
mirror of it, written with the set and answered for one more release. Some
framework code still decided by the mirror: the lock endpoints refused an
administrator whose mirror said otherwise, and signing in through a provider
compared the provider's role with the mirror. The mirror goes in 0.4.0; until
then nothing decides by it.

    python3 -m pytest keepup/tests/role_mirror_decisions_tests.py -v
"""

import ast
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup.auth import dependencies, user_roles
from keepup.tests.repository import is_not_the_package
from keepup.auth.dependencies import get_current_admin
from keepup.db import DatabaseManagerV2
from keepup.locks import register_lock_routes
from keepup.roles import ROLE_ADMIN, ROLE_CLIENT
from keepup.schema import init_db

PACKAGE = Path(__file__).resolve().parents[1]

#: Where the mirror may still be read: the fallback for an account whose set is
#: empty (user_roles), and the answers that carry it for one more release.
READERS = {"auth/user_roles.py", "auth/routes.py"}


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


def test_no_framework_decision_reads_the_mirror():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        # A build output is a copy of the sources and a local environment is
        # not the package: both would report every finding twice (keepup-28).
        if "tests" in path.parts or is_not_the_package(path) or relative in READERS:
            continue
        offenders += [f"{relative}:{line}" for line in mirror_reads(path)]
    assert offenders == []


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


def test_an_administrator_whose_mirror_lags_still_reaches_the_locks():
    uid = dependencies.save_user_to_db("mirror-lags", "a-long-password-60")
    dependencies.update_user(uid, status="active")
    user_roles.set_roles(uid, [ROLE_ADMIN])
    # The mirror moved on its own -- a hand-written UPDATE, a script.
    DatabaseManagerV2.execute_commit("UPDATE users SET role = :r WHERE id = :id",
                                     {"r": ROLE_CLIENT, "id": uid})
    admin = user_roles.attach_roles(dependencies.get_user_by_id(uid))
    assert admin["role"] == ROLE_CLIENT and ROLE_ADMIN in admin["roles"]

    app = FastAPI()
    register_lock_routes(app)
    app.dependency_overrides[get_current_admin] = lambda: admin
    assert TestClient(app).get("/api/admin/locks").status_code == 200
