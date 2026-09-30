"""The API schema answers an administrator, and the application decides the rest.

keepup-96. /openapi.json described every route -- the administrative ones with
their parameters -- to anybody, and no setting switched it off: create_app()
handed FastAPI the documentation pages' addresses but not the schema's.

    python3 -m pytest keepup/tests/api_schema_tests.py -v
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from keepup.auth import dependencies, user_roles
from keepup.factory import create_app
from keepup.roles import ROLE_ADMIN, ROLE_CLIENT
from keepup.schema import init_db
from keepup.settings import KeepupSettings


@pytest.fixture(scope="module", autouse=True)
def tables():
    init_db()


def build(**settings):
    return TestClient(create_app(KeepupSettings(title="Schema", static_mounts=(),
                                                plugin_manager=None, **settings)),
                      raise_server_exceptions=False)


def bearer(role):
    name = f"schema-{uuid.uuid4().hex[:8]}"
    uid = dependencies.save_user_to_db(name, "a-long-password-96")
    dependencies.update_user(uid, status="active")
    user_roles.set_roles(uid, [role], checked=False)
    token = dependencies.issue_session_token(uid, name)["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_nobody_gets_the_schema():
    answer = build().get("/openapi.json")
    assert answer.status_code == 401
    assert "/api/admin/" not in answer.text


def test_a_client_gets_no_schema():
    assert build().get("/openapi.json", headers=bearer(ROLE_CLIENT)).status_code == 403


def test_an_administrator_gets_the_whole_schema():
    answer = build().get("/openapi.json", headers=bearer(ROLE_ADMIN))
    assert answer.status_code == 200
    paths = answer.json()["paths"]
    assert "/api/admin/users" in paths and "/api/auth/login" in paths
    # The documentation routes themselves are not part of what they document.
    assert "/openapi.json" not in paths and "/api/docs" not in paths


def test_an_application_may_publish_its_schema_on_purpose():
    assert build(openapi_public=True).get("/openapi.json").status_code == 200


def test_the_schema_can_move():
    client = build(openapi_url="/api/schema.json")
    assert client.get("/api/schema.json", headers=bearer(ROLE_ADMIN)).status_code == 200
    assert client.get("/openapi.json").status_code == 404
    assert "/api/schema.json" in client.get("/api/docs").text


def test_without_a_schema_there_are_no_pages_and_code_still_builds_it():
    client = build(openapi_url=None)
    for path in ("/openapi.json", "/api/docs", "/api/redoc", "/docs/oauth2-redirect"):
        assert client.get(path).status_code == 404, path
    assert "/api/auth/login" in client.app.openapi()["paths"]


def test_the_pages_are_shells_that_fetch_the_schema():
    client = build()
    for path in ("/api/docs", "/api/redoc"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert "/openapi.json" in page.text
    assert client.get("/docs/oauth2-redirect").status_code == 200


def test_the_pages_can_be_switched_off_one_by_one():
    client = build(docs_url=None, redoc_url=None)
    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/redoc").status_code == 404
    assert client.get("/openapi.json", headers=bearer(ROLE_ADMIN)).status_code == 200


def test_the_documentation_is_registered_before_any_other_route():
    """FastAPI's constructor put it first; a later route must not shadow it."""
    paths = [getattr(route, "path", None) for route in build().app.routes]
    assert paths[:4] == ["/openapi.json", "/api/docs", "/docs/oauth2-redirect", "/api/redoc"]
