"""Panel sections can be created and changed through the API (keepup-80).

The routes handed the section to the writer under their own field names
(`module_id`, `js_path`, ...) while the writer reads the catalogue file's
(`id`, `js`, ...): every create and every change answered 400, and a section
could only be added through the file and a full import.

    python3 -m pytest keepup/tests/section_routes_tests.py -v
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup import cache
from keepup.auth.dependencies import get_current_admin
from keepup.modules import register_module_routes
from keepup.schema import init_db


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


@pytest.fixture
def client():
    cache.invalidate_all()
    app = FastAPI()
    register_module_routes(app)
    app.dependency_overrides[get_current_admin] = lambda: {"id": 1, "username": "admin"}
    return TestClient(app)


SECTION = {"module_id": "api_section", "name": "Made through the API",
           "js_path": "/static/modules/js/api_section.js", "init_function": "initApiSection",
           "config": {"refresh": 30}}


def test_a_section_is_created_through_the_api(client):
    created = client.post("/api/admin/modules", json=SECTION)
    assert created.status_code == 200, created.text
    stored = client.get("/api/admin/modules/api_section").json()
    assert stored["name"] == "Made through the API"
    assert stored["js_path"] == "/static/modules/js/api_section.js"
    assert stored["init_function"] == "initApiSection"


def test_a_change_keeps_what_it_does_not_name(client):
    client.post("/api/admin/modules", json={**SECTION, "module_id": "api_changed"})
    changed = client.put("/api/admin/modules/api_changed", json={"name": "Renamed"})
    assert changed.status_code == 200, changed.text
    stored = client.get("/api/admin/modules/api_changed").json()
    assert stored["name"] == "Renamed"
    assert stored["js_path"] == "/static/modules/js/api_section.js"
    assert stored["init_function"] == "initApiSection"


def test_switching_a_section_off_takes_it_away(client):
    client.post("/api/admin/modules", json={**SECTION, "module_id": "api_off"})
    assert client.put("/api/admin/modules/api_off", json={"is_active": False}).status_code == 200
    assert client.get("/api/admin/modules/api_off").status_code == 404


def test_a_missing_section_is_not_found(client):
    assert client.put("/api/admin/modules/nobody_here", json={"name": "x"}).status_code == 404
