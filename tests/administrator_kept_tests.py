"""The deployment keeps an administrator, and a role is one it declares (keepup-71).

An administrator could take the administrator role from themselves, or from
the last account holding it, through either route that changes roles -- and
then nobody could give it back short of editing the database. The single-role
field of the user update also wrote any name it was given.

Each test has a database of its own, since "the last administrator" depends
on who else is in it.

    python3 -m pytest keepup/tests/administrator_kept_tests.py -v
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup import cache
from keepup.auth import dependencies, user_roles
from keepup.auth.dependencies import get_current_admin
from keepup.auth.routes import register_auth_routes
from keepup.db import DatabaseManagerV2, db_config
from keepup.roles import ROLE_ADMIN, ROLE_CLIENT
from keepup.schema import init_db


@pytest.fixture(autouse=True)
def fresh_database(tmp_path, monkeypatch):
    DatabaseManagerV2.dispose()
    monkeypatch.setattr(db_config, "db_path", str(tmp_path / "roles.db"), raising=False)
    monkeypatch.setattr(db_config, "db_type", "sqlite", raising=False)
    init_db()
    cache.invalidate_all()
    # Whatever the start seeded is not an active administrator here.
    DatabaseManagerV2.execute_commit("UPDATE users SET status = 'blocked'")
    yield
    cache.invalidate_all()
    DatabaseManagerV2.dispose()


def account(name, role):
    uid = dependencies.save_user_to_db(name, "a-long-password-71")
    dependencies.update_user(uid, status="active", role=role)
    return uid


def client_as(actor_id):
    app = FastAPI()
    register_auth_routes(app, SimpleNamespace(plugins={}))
    app.dependency_overrides[get_current_admin] = lambda: {
        "id": actor_id, "username": "acting", "roles": [ROLE_ADMIN]}
    return TestClient(app)


def roles(uid):
    return user_roles.roles_of(uid)


# --- the set -------------------------------------------------------------------------

def test_an_administrator_cannot_take_the_role_from_themselves():
    me = account("admin-self", ROLE_ADMIN)
    account("admin-other", ROLE_ADMIN)
    answer = client_as(me).put(f"/api/admin/users/{me}/roles", json={"roles": [ROLE_CLIENT]})
    assert answer.status_code == 400
    assert "themselves" in answer.json()["detail"]
    assert roles(me) == [ROLE_ADMIN]


def test_the_last_administrator_keeps_the_role():
    last = account("admin-last", ROLE_ADMIN)
    helper = account("support", ROLE_CLIENT)
    answer = client_as(helper).put(f"/api/admin/users/{last}/roles", json={"roles": [ROLE_CLIENT]})
    assert answer.status_code == 400
    assert "last active administrator" in answer.json()["detail"]
    assert roles(last) == [ROLE_ADMIN]


def test_another_administrator_may_lose_the_role_while_one_remains():
    me, other = account("admin-a", ROLE_ADMIN), account("admin-b", ROLE_ADMIN)
    answer = client_as(me).put(f"/api/admin/users/{other}/roles", json={"roles": [ROLE_CLIENT]})
    assert answer.status_code == 200
    assert roles(other) == [ROLE_CLIENT]


def test_a_blocked_administrator_does_not_count_as_remaining():
    me, other = account("admin-c", ROLE_ADMIN), account("admin-d", ROLE_ADMIN)
    dependencies.update_user(me, status="blocked")
    answer = client_as(me).put(f"/api/admin/users/{other}/roles", json={"roles": [ROLE_CLIENT]})
    assert answer.status_code == 400


# --- the single field ----------------------------------------------------------------

def test_the_single_role_is_held_to_the_same_rule():
    me = account("admin-e", ROLE_ADMIN)
    account("admin-f", ROLE_ADMIN)
    answer = client_as(me).patch(f"/api/admin/users/{me}", json={"role": ROLE_CLIENT})
    assert answer.status_code == 400
    assert roles(me) == [ROLE_ADMIN]


def test_the_single_role_must_be_declared():
    me = account("admin-g", ROLE_ADMIN)
    user = account("person", ROLE_CLIENT)
    answer = client_as(me).patch(f"/api/admin/users/{user}", json={"role": "SUPERUSER"})
    assert answer.status_code == 400
    assert "Unknown role" in answer.json()["detail"]
    assert roles(user) == [ROLE_CLIENT]


def test_the_single_role_is_stored_as_declared():
    me = account("admin-h", ROLE_ADMIN)
    user = account("person-2", ROLE_CLIENT)
    answer = client_as(me).patch(f"/api/admin/users/{user}", json={"role": "admin"})
    assert answer.status_code == 200
    assert roles(user) == [ROLE_ADMIN]
