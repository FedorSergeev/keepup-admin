"""A timed flush of the request audit does not lose a running request's outcome (keepup-41).

The flush used to write every buffered request, running ones included, and drop
them from the buffer: when such a request finished, end_request found nothing,
logged "not found", and the table kept a request without a status. Now only
finished requests are written; a running one waits for its outcome, unless it
has run so long that nobody will finish it, and everything goes at shutdown.

    python3 -m pytest keepup/tests/audit_flush_tests.py -v
"""

from datetime import datetime, timedelta

import pytest

from keepup import audit, schema
from keepup.db import DatabaseManagerV2, db_config


@pytest.fixture
def buffer(tmp_path, monkeypatch):
    DatabaseManagerV2.dispose()
    monkeypatch.setattr(db_config, "db_path", str(tmp_path / "audit.db"), raising=False)
    monkeypatch.setattr(db_config, "db_type", "sqlite", raising=False)
    schema.init_db()
    audit.incoming_requests_buffer.clear()
    yield audit.incoming_requests_buffer
    audit.incoming_requests_buffer.clear()
    DatabaseManagerV2.dispose()


def rows():
    return DatabaseManagerV2.execute(
        "SELECT endpoint, http_status FROM incoming_requests ORDER BY endpoint")


async def start(endpoint, started=None):
    return await audit.IncomingRequestLogger.start_request(
        "replica-1", "GET", endpoint, "api", {}, request_start_at=started)


async def test_a_running_request_survives_a_timed_flush_and_keeps_its_outcome(buffer, caplog):
    running = await start("/slow")
    done = await start("/fast")
    await audit.IncomingRequestLogger.end_request(done, http_status=200)

    assert await audit.IncomingRequestLogger.flush_buffer() == 1
    assert rows() == [{"endpoint": "/fast", "http_status": 200}]
    assert running in buffer, "a running request is not written before its outcome"

    finished = await audit.IncomingRequestLogger.end_request(running, http_status=500,
                                                             error_message="boom")
    assert finished is True
    assert "not found" not in caplog.text
    await audit.IncomingRequestLogger.flush_buffer()
    assert rows() == [{"endpoint": "/fast", "http_status": 200},
                      {"endpoint": "/slow", "http_status": 500}]
    assert not buffer


async def test_a_request_nobody_will_finish_is_written_without_an_outcome(buffer):
    long_ago = datetime.utcnow() - timedelta(seconds=audit.IN_FLIGHT_STALE_SECONDS + 5)
    await start("/abandoned", started=long_ago)
    await start("/fresh")

    assert await audit.IncomingRequestLogger.flush_buffer() == 1
    assert rows() == [{"endpoint": "/abandoned", "http_status": None}]
    assert len(buffer) == 1, "the buffer does not grow forever, and the fresh one waits"


async def test_at_shutdown_everything_is_written(buffer):
    await start("/still-running")
    assert await audit.IncomingRequestLogger.flush_buffer(include_in_flight=True) == 1
    assert rows() == [{"endpoint": "/still-running", "http_status": None}]
    assert not buffer


async def test_a_failed_write_keeps_the_requests_for_the_next_flush(buffer, monkeypatch):
    done = await start("/fast")
    await audit.IncomingRequestLogger.end_request(done, http_status=200)
    monkeypatch.setattr(DatabaseManagerV2, "execute_many",
                        classmethod(lambda cls, q, p: (_ for _ in ()).throw(RuntimeError("db down"))))
    assert await audit.IncomingRequestLogger.flush_buffer() == 0
    assert done in buffer
