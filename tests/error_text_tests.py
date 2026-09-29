"""Answers and logs carry no text the server did not write (keepup-76).

The text of an exception went to the client in the route's answer and into the
request audit unredacted -- a database message names tables, values, sometimes
the query -- and a request path went into the log as it was decoded, so
``%0a`` in an address wrote a log line of the sender's choosing.

    python3 -m pytest keepup/tests/error_text_tests.py -v
"""

import asyncio
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup import events
from keepup.auth.dependencies import get_current_admin
from keepup.body_limit import BodyLimitMiddleware
from keepup.events_api import register_event_api_routes
from keepup.logging_setup import for_log
from keepup.schema import init_db

SECRET = "relation users: password_hash='$2b$12$abc' for token sk-live-1"


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()
    events.init_event_manager()


# --- one log line per line --------------------------------------------------------

@pytest.mark.parametrize("raw, written", [
    ("/api/x\nFAKE 200 admin signed in", "/api/x\\nFAKE 200 admin signed in"),
    ("/a\r\nb", "/a\\r\\nb"),
    ("/esc\x1b[31m", "/esc\\x1b[31m"),
    ("/tab\tend", "/tab\\tend"),
    ("/café path", "/café path"),
])
def test_control_characters_are_written_as_escapes(raw, written):
    assert for_log(raw) == written


def test_a_long_value_is_cut_and_says_so():
    assert for_log("x" * 600, limit=10) == "x" * 10 + "...[590 more]"


def test_a_refused_path_cannot_forge_a_log_line(caplog):
    async def app(scope, receive, send):
        pass

    limited = BodyLimitMiddleware(app, default_limit=10)
    scope = {"type": "http", "method": "POST", "path": "/api/x\nFORGED line",
             "headers": [(b"content-length", b"100")]}
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    with caplog.at_level(logging.WARNING):
        asyncio.run(limited(scope, receive, send))
    assert sent[0]["status"] == 413
    refused = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert refused and all("\n" not in message for message in refused)


# --- answers ------------------------------------------------------------------------

def test_a_failing_route_answers_without_the_exception_s_text(monkeypatch, caplog):
    async def broken(**kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(events.event_manager, "get_events", broken)
    app = FastAPI()
    register_event_api_routes(app)
    app.dependency_overrides[get_current_admin] = lambda: {"id": 1, "username": "a"}
    with caplog.at_level(logging.ERROR):
        answer = TestClient(app).get("/api/events")
    assert answer.status_code == 500
    assert SECRET not in answer.text and "sk-live" not in answer.text
    # Still diagnosable: the text is in the application log.
    assert "sk-live-1" in caplog.text


# --- the audit ----------------------------------------------------------------------

def test_the_audit_records_the_kind_of_failure_not_its_text(monkeypatch):
    from keepup import audit
    monkeypatch.setattr(audit, "incoming_requests_buffer", {})

    async def failing_request():
        with pytest.raises(ValueError):
            async with audit.log_api_request("GET", "/api/thing"):
                raise ValueError(SECRET)

    asyncio.run(failing_request())
    recorded = [entry.get("error_message") for entry in audit.incoming_requests_buffer.values()]
    assert recorded == ["ValueError"]
