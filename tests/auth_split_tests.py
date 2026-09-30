"""Signing in and the management of users are separate modules (keepup-59).

`keepup/auth/routes.py` held both -- sessions, sign-out and token renewal next
to profiles, the administration of accounts and roles -- two subjects with
different readers. Users are `keepup/auth/user_routes.py` now; the application
still registers everything with one call, and the names that moved still answer
from the old module, with a warning.

    python3 -m pytest keepup/tests/auth_split_tests.py -v
"""

import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from keepup.auth import routes, user_routes

AUTH = Path(routes.__file__).parent

USER_PATHS = {"/api/auth/profile", "/api/admin/users", "/api/admin/users/{user_id}",
              "/api/admin/users/{user_id}/password", "/api/admin/users/{user_id}/roles"}
SIGN_IN_PATHS = {"/api/auth/login", "/api/auth/logout", "/api/auth/refresh",
                 "/api/auth/session", "/api/auth/verify", "/api/auth/me"}


def test_one_call_still_registers_both():
    app = FastAPI()
    routes.register_auth_routes(app, SimpleNamespace(plugins={}))
    paths = {route.path for route in app.routes}
    assert USER_PATHS <= paths and SIGN_IN_PATHS <= paths


def test_each_path_is_declared_in_its_own_module():
    sign_in = (AUTH / "routes.py").read_text(encoding="utf-8")
    users = (AUTH / "user_routes.py").read_text(encoding="utf-8")
    for path in USER_PATHS:
        assert f'"{path}"' in users and f'"{path}"' not in sign_in
    for path in SIGN_IN_PATHS:
        assert f'"{path}"' in sign_in and f'"{path}"' not in users


@pytest.mark.parametrize("name", sorted(routes._MOVED_TO_USER_ROUTES))
def test_a_moved_name_still_answers_and_says_where_it_went(name):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = getattr(routes, name)
    assert value is getattr(user_routes, name)
    assert any(f"keepup.auth.user_routes.{name}" in str(w.message) for w in caught)


def test_the_password_rule_configured_for_sign_in_governs_the_administrator_too():
    """One rule for both doors, as before the split."""
    from fastapi.testclient import TestClient
    from keepup.auth.dependencies import get_current_admin, save_user_to_db, update_user
    from keepup.schema import init_db
    init_db()
    uid = save_user_to_db("split-rule", "a-long-password-59")
    update_user(uid, status="active")
    routes.configure(password=lambda password, username: "too plain")
    try:
        app = FastAPI()
        routes.register_auth_routes(app, SimpleNamespace(plugins={}))
        app.dependency_overrides[get_current_admin] = lambda: {"id": 1, "username": "admin"}
        answer = TestClient(app).put(f"/api/admin/users/{uid}/password",
                                     json={"new_password": "whatever-long"})
        assert answer.status_code == 400
        assert "too plain" in answer.text
    finally:
        routes.configure(password=None)
