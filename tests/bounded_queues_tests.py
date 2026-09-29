"""Log and audit queues stay bounded while their receiver is away (keepup-68).

Log shipping queued records without a bound while the collector was down or no
token was given, and past a full batch started a thread per record; the request
audit put a failed flush back into its buffer, so with the database away it
grew with every request, and every request past the batch size started one more
flush.

    python3 -m pytest keepup/tests/bounded_queues_tests.py -v
"""

import asyncio
import logging
import threading

import pytest

from keepup import audit, log_shipping


def record(message="m"):
    return logging.LogRecord("t", logging.INFO, __file__, 1, message, None, None)


@pytest.fixture
def wrapper(monkeypatch):
    """A wrapper with a token and no collector address: no thread of its own."""
    monkeypatch.setattr(log_shipping, "REMOTE_LOG_URL", None)
    monkeypatch.setattr(log_shipping, "REMOTE_LOG_TOKEN", "a-token")
    monkeypatch.setattr(log_shipping, "REMOTE_MAX_QUEUED", 5, raising=False)
    return log_shipping.RemoteLoggerWrapper(batch_size=3)


# --- log shipping ---------------------------------------------------------------------

def test_without_a_token_nothing_is_kept(monkeypatch):
    monkeypatch.setattr(log_shipping, "REMOTE_LOG_URL", None)
    monkeypatch.setattr(log_shipping, "REMOTE_LOG_TOKEN", None)
    idle = log_shipping.RemoteLoggerWrapper()
    for _ in range(100):
        idle.emit(record())
    assert idle.log_queue.qsize() == 0


def test_the_queue_keeps_the_newest_and_counts_what_it_dropped(wrapper):
    for i in range(12):
        wrapper.emit(record(f"m{i}"))
    kept = [wrapper.log_queue.get_nowait()["message"] for _ in range(wrapper.log_queue.qsize())]
    assert kept == ["m7", "m8", "m9", "m10", "m11"]
    assert wrapper.dropped == 7


def test_a_full_batch_wakes_the_worker_instead_of_starting_threads(wrapper, monkeypatch):
    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self))
    for _ in range(50):
        wrapper.emit(record())
    assert started == []
    assert wrapper._wake.is_set()


def test_returned_logs_go_first_and_the_oldest_go_when_they_do_not_fit(wrapper):
    for i in range(4):
        wrapper.emit(record(f"new{i}"))
    wrapper._return_logs_to_queue([{"message": "old0"}, {"message": "old1"}, {"message": "old2"}])
    kept = [wrapper.log_queue.get_nowait()["message"] for _ in range(wrapper.log_queue.qsize())]
    assert kept == ["old2", "new0", "new1", "new2", "new3"]
    assert wrapper.dropped == 2


# --- the request audit ----------------------------------------------------------------

@pytest.fixture
def buffer(monkeypatch):
    monkeypatch.setattr(audit, "incoming_requests_buffer", {})
    monkeypatch.setattr(audit, "dropped_requests", 0, raising=False)
    monkeypatch.setattr(audit, "_flush_paused_until", 0.0, raising=False)
    return audit.incoming_requests_buffer


async def requests_seen(count):
    ids = []
    for i in range(count):
        request_id = await audit.IncomingRequestLogger.start_request(
            "replica", "GET", f"/api/r{i}", "host")
        await audit.IncomingRequestLogger.end_request(request_id, http_status=200)
        ids.append(request_id)
        await asyncio.sleep(0)
    return ids


def test_the_audit_buffer_has_a_ceiling(buffer, monkeypatch):
    monkeypatch.setattr(audit, "BUFFER_MAX_SIZE", 10_000)
    monkeypatch.setattr(audit, "BUFFER_HARD_LIMIT", 20, raising=False)
    ids = asyncio.run(requests_seen(50))
    assert list(audit.incoming_requests_buffer) == ids[-20:]
    assert audit.dropped_requests == 30


def test_a_database_away_gets_one_flush_not_one_per_request(buffer, monkeypatch):
    monkeypatch.setattr(audit, "BUFFER_MAX_SIZE", 1)
    attempts = []

    async def away(query, rows):
        attempts.append(len(rows))
        await asyncio.sleep(0.01)
        raise RuntimeError("database away")

    monkeypatch.setattr(audit.DatabaseManagerV2, "execute_many_async", away)

    async def scenario():
        await requests_seen(200)
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    # One flush in flight at a time, then a pause after its failure.
    assert len(attempts) <= 2
    assert len(audit.incoming_requests_buffer) == 200
