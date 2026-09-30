"""An application without plugins can be signed into (keepup-89).

The framework registered sign-in, sign-out and the management of users only
when the application passed a plugin manager: an application that needs no
plugins got a panel nobody could sign into. Found by the load stand
(keepup-53), which had to be given an empty manager.

    python3 -m pytest keepup/tests/sign_in_without_plugins_tests.py -v
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from keepup.auth import dependencies, user_routes
from keepup.factory import create_app
from keepup.schema import init_db
from keepup.settings import KeepupSettings

PASSWORD = "a-long-password-89"


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


def test_an_application_without_plugins_signs_in():
    if not dependencies.get_user_by_username("no-plugins"):
        uid = dependencies.save_user_to_db("no-plugins", PASSWORD)
        dependencies.update_user(uid, status="active")
    app = create_app(KeepupSettings(title="No plugins", static_mounts=(), plugin_manager=None))
    with TestClient(app) as client:
        answer = client.post("/api/auth/login", json={"username": "no-plugins", "password": PASSWORD})
        assert answer.status_code == 200, answer.text
        token = answer.json()["access_token"]
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["username"] == "no-plugins"


def test_blocking_an_account_has_no_plugins_to_tell_and_does_not_fail():
    asyncio.run(user_routes.notify_account_blocked(None, 1, {"id": 2}))
