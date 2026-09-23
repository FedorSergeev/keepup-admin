"""What the audit records, what it hides, and how long it keeps it.

Task keepup-11. Three things the audit got wrong in the same quiet way: it
wrote every value it saw, it threw away the outcome it had just recorded, and
it never deleted anything. None of the three shows up in the behaviour of a
request -- the server answers correctly throughout.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/audit_outcome_tests.py -v
"""

from datetime import datetime, timedelta

import pytest

from keepup import audit
from keepup.db import DatabaseManager, DatabaseManagerV2
from keepup.schema import init_db


@pytest.fixture(scope="module", autouse=True)
def tables():
    init_db()


@pytest.fixture(autouse=True)
def empty_buffer():
    audit.incoming_requests_buffer.clear()
    yield audit.incoming_requests_buffer
    audit.incoming_requests_buffer.clear()


@pytest.fixture(autouse=True)
def default_redaction(monkeypatch):
    monkeypatch.setattr(audit, "redact", audit.names_only)


# --- what is written ----------------------------------------------------------

def test_the_names_are_kept_and_the_values_are_not():
    hidden = audit.names_only({"token": "abc", "page": 3, "nested": {"key": "v"},
                               "enabled": True, "missing": None})
    assert hidden == {"token": audit.HIDDEN, "page": audit.HIDDEN,
                      "nested": {"key": audit.HIDDEN},
                      # A flag and an absence stay: nothing hides in them, and
                      # they are what keeps the row readable afterwards.
                      "enabled": True, "missing": None}


def test_a_list_keeps_its_shape():
    assert audit.names_only(["a", "b"]) == [audit.HIDDEN, audit.HIDDEN]


def test_nothing_of_the_value_survives_in_the_text():
    """The point of the default is that the secret is not in the row at all."""
    hidden = audit.names_only({"authorization": "Bearer sk-live-4242"})
    assert "sk-live-4242" not in str(hidden)


# --- the outcome --------------------------------------------------------------

async def test_the_outcome_survives_the_second_call(empty_buffer):
    """log_api_request() ends the request twice: with the outcome, then without.

    The second call used to write http_status, response_data and error_message
    back to None, so every row of the table carried an empty outcome and a wave
    of refusals was indistinguishable from a wave of successful calls.
    """
    request_id = await audit.IncomingRequestLogger.start_request(
        instance_id="test", method="GET", endpoint="/api/x", host="plugin_api")

    await audit.IncomingRequestLogger.end_request(
        request_id, duration_ms=12, http_status=403,
        error_message="Admin access required")
    await audit.IncomingRequestLogger.end_request(request_id, duration_ms=12)

    recorded = empty_buffer[request_id]
    assert recorded["http_status"] == 403
    assert recorded["error_message"] == "Admin access required"


async def test_a_successful_call_keeps_its_status(empty_buffer):
    request_id = await audit.IncomingRequestLogger.start_request(
        instance_id="test", method="GET", endpoint="/api/x", host="plugin_api")

    await audit.IncomingRequestLogger.end_request(
        request_id, http_status=200, response_data={"ok": True})
    await audit.IncomingRequestLogger.end_request(request_id)

    assert empty_buffer[request_id]["http_status"] == 200
    # A boolean is kept: nothing hides in two values, and a row saying only
    # "a boolean was here" would stop being useful for reading an incident.
    assert empty_buffer[request_id]["response_data"] == {"ok": True}


async def test_the_wrapper_itself_records_the_outcome(empty_buffer):
    """Through log_api_request, the way a plugin route uses it."""
    async with audit.log_api_request(
            method="GET", endpoint="/api/x", host="plugin_api") as request_id:
        await audit.IncomingRequestLogger.end_request(
            request_id, http_status=201, response_data={"id": 7})

    assert empty_buffer[request_id]["http_status"] == 201


# --- how long it is kept ------------------------------------------------------

def test_rows_past_the_retention_period_are_deleted():
    DatabaseManager.execute_commit_only("DELETE FROM incoming_requests", ())
    old = datetime.utcnow() - timedelta(days=90)
    fresh = datetime.utcnow()

    for created_at in (old, fresh):
        DatabaseManagerV2.execute_commit(
            "INSERT INTO incoming_requests "
            "(instance_id, method, endpoint, host, request_start_at, created_at) "
            "VALUES (:instance_id, :method, :endpoint, :host, :start, :created_at)",
            {"instance_id": "test", "method": "GET", "endpoint": "/api/x",
             "host": "plugin_api", "start": created_at, "created_at": created_at})

    removed = audit.purge_old_requests(days=30)

    assert removed == 1
    left = DatabaseManagerV2.execute("SELECT created_at FROM incoming_requests")
    assert len(left) == 1


def test_the_retention_period_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "7")
    assert audit.retention_days() == 7


@pytest.mark.parametrize("value", ["", "nonsense", "0", "-5"])
def test_an_unusable_period_falls_back_rather_than_switching_the_sweep_off(monkeypatch, value):
    """A sweep that switched itself off silently is worse than one that sweeps."""
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", value)
    assert audit.retention_days() == audit.DEFAULT_AUDIT_RETENTION_DAYS
