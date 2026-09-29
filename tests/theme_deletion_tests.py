"""Deleting a theme says why it cannot (keepup-58).

The check compared the theme with the cached active theme, whose query does not
select the id, so it never fired: the active theme was kept only by the
condition in the delete itself, and the answer could not tell "not found" from
"active".

    python3 -m pytest keepup/tests/theme_deletion_tests.py -v
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup import cache
from keepup.auth.dependencies import get_current_admin
from keepup.db import DatabaseManagerV2
from keepup.schema import init_db
from keepup.themes import config_service, register_theme_routes


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()
    config_service.initialize()


@pytest.fixture
def client():
    cache.invalidate_all()
    app = FastAPI()
    register_theme_routes(app, config_service)
    app.dependency_overrides[get_current_admin] = lambda: {"id": 1, "username": "admin"}
    yield TestClient(app)
    # The theme cache is the process's; leaving it filled leaks into the next file.
    cache.invalidate_all()


def active_id():
    return DatabaseManagerV2.execute_one(
        "SELECT id FROM visual_themes WHERE is_active = TRUE")["id"]


def test_the_active_theme_is_refused_with_the_reason(client):
    answer = client.delete(f"/themes/{active_id()}").json()
    assert answer["success"] is False
    assert "active theme cannot be deleted" in answer["message"]


def test_a_missing_theme_is_named_as_missing(client):
    answer = client.delete("/themes/987654").json()
    assert answer == {"success": False, "message": "There is no theme 987654."}


def test_the_service_itself_refuses_the_active_theme():
    assert config_service.deletion_refusal(active_id())
    assert config_service.delete_theme(active_id()) is False
    cache.invalidate_all()


def test_an_inactive_theme_is_deleted(client):
    config_service.create_theme("to-be-deleted-58", "index_new.html", False)
    # By name: on SQLite the id create_theme answers is read on another pooled
    # connection and need not be this row's (keepup-82).
    theme_id = DatabaseManagerV2.execute_one(
        "SELECT id FROM visual_themes WHERE theme_name = :n", {"n": "to-be-deleted-58"})["id"]
    assert config_service.deletion_refusal(theme_id) is None
    assert client.delete(f"/themes/{theme_id}").json()["success"] is True
