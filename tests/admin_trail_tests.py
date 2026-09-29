"""What an administrator changes leaves a trace in the event log (keepup-67).

An administrator could create events of the application's own types, which
looked exactly like the real ones, and purge the log, and neither left a trace;
changes to sections, themes and plugin decisions reached only the application
log, and the restart route answered "sent" and did nothing. Against the
session's throwaway SQLite database, through the real routes.

    python3 -m pytest keepup/tests/admin_trail_tests.py -v
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup import admin_trail, events
from keepup.auth.dependencies import get_current_admin
from keepup.db import DatabaseManagerV2
from keepup.events_api import register_event_api_routes
from keepup.metrics_api import register_metrics_routes
from keepup.modules import register_module_routes
from keepup.schema import init_db
from keepup.themes import config_service, register_theme_routes

ADMIN = {"id": 41, "username": "trail-admin"}
DECLARED = ("audit_user_login", "audit_order_repriced")


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()
    events.init_event_manager()


@pytest.fixture
def client():
    app = FastAPI()
    register_event_api_routes(app, declared_event_types=lambda: DECLARED)
    register_module_routes(app)
    register_theme_routes(app, config_service)
    register_metrics_routes(app)
    app.dependency_overrides[get_current_admin] = lambda: ADMIN
    return TestClient(app)


def trail(event_type):
    rows = DatabaseManagerV2.execute(
        "SELECT event_text, event_data FROM app_events WHERE event_type = :t ORDER BY id",
        {"t": event_type})
    return [dict(row, event_data=json.loads(row["event_data"] or "{}")) for row in rows]


# --- events written by hand ---------------------------------------------------------

@pytest.mark.parametrize("event_type", DECLARED + (admin_trail.EVENTS_PURGED,))
def test_a_type_the_server_writes_cannot_be_created_by_hand(client, event_type):
    answer = client.post("/api/events", json={"event_type": event_type, "event_text": "forged"})
    assert answer.status_code == 400
    assert trail(event_type) == [] or all(e["event_text"] != "forged" for e in trail(event_type))


def test_an_event_created_by_hand_says_who_wrote_it(client):
    answer = client.post("/api/events", json={
        "event_type": "operator_note", "event_text": "maintenance window",
        "event_data": {"manual": "pretending to be the server"}})
    assert answer.status_code == 200
    written = trail("operator_note")[-1]["event_data"]
    assert written["manual"] == {"user_id": 41, "username": "trail-admin"}


# --- purging the log -----------------------------------------------------------------

def test_purging_the_log_is_itself_written_into_it(client):
    answer = client.delete("/api/events/cleanup?days=30")
    assert answer.status_code == 200
    last = trail(admin_trail.EVENTS_PURGED)[-1]["event_data"]
    assert last["by"] == {"user_id": 41, "username": "trail-admin"}
    assert last["days"] == 30 and last["deleted_count"] == answer.json()["deleted_count"]


# --- settings ------------------------------------------------------------------------

def test_a_section_change_names_its_author(client):
    """Section routes read and write the database off the loop and await the trail."""
    from keepup.modules import create_or_update_module
    create_or_update_module({"id": "trail_section", "name": "Trail"})

    answer = client.post("/api/admin/role-modules",
                         json={"role_name": "TRAIL", "module_ids": ["trail_section"]})
    assert answer.status_code == 200, answer.text
    granted = trail(admin_trail.SECTIONS_CHANGED)[-1]["event_data"]
    assert granted["by"] == {"user_id": 41, "username": "trail-admin"}
    assert granted["action"] == "grant" and granted["module_ids"] == ["trail_section"]

    assert client.delete("/api/admin/modules/trail_section").status_code == 200
    deleted = trail(admin_trail.SECTIONS_CHANGED)[-1]["event_data"]
    assert deleted["action"] == "delete" and deleted["module_id"] == "trail_section"


def test_a_theme_change_names_its_author(client):
    config_service.initialize()
    theme_id = next(t["id"] for t in config_service.get_all_themes() if t.get("is_active"))
    assert client.post(f"/themes/{theme_id}/activate").json()["success"] is True
    changed = trail(admin_trail.THEMES_CHANGED)[-1]["event_data"]
    assert changed == {"by": {"user_id": 41, "username": "trail-admin"},
                       "action": "activate", "theme_id": theme_id}


def test_a_plugin_decision_names_its_author(monkeypatch):
    import asyncio
    from keepup.plugins import admin as plugin_admin
    monkeypatch.setattr(plugin_admin, "_refuse_if_the_deployment_decides", lambda plugin_id: None)
    monkeypatch.setattr(plugin_admin, "clear_plugin_override", lambda plugin_id: None)
    asyncio.run(plugin_admin.clear_plugin_enabled("trail_plugin", ADMIN))
    changed = trail(admin_trail.PLUGIN_DECISION_CHANGED)[-1]["event_data"]
    assert changed["plugin_id"] == "trail_plugin" and changed["enabled"] is None
    assert changed["by"]["username"] == "trail-admin"


def test_a_trail_that_cannot_be_written_does_not_fail_the_action(monkeypatch):
    import asyncio

    async def broken(**kwargs):
        raise RuntimeError("database away")

    monkeypatch.setattr(events.event_manager, "create_event", broken)
    assert asyncio.run(admin_trail.record(admin_trail.THEMES_CHANGED, ADMIN, "x")) is None


# --- restart -------------------------------------------------------------------------

def test_restarting_an_unknown_replica_is_refused_not_reported_as_sent(client):
    answer = client.post("/api/admin/instances/no-such-replica/restart")
    assert answer.status_code == 404
    assert "sent" not in answer.text
