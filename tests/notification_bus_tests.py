"""The replicas' notification bus as a capability of the framework.

The transport's own rules -- echo, the envelope limit, a failing publish -- are
covered by the application that installed the bus, against its own facade; what
is checked here is what makes it a framework capability: it carries no
channel of any product, an application switches it on with one setting, and
the framework installs it before anything that subscribes and removes it after.

    python3 -m pytest keepup/tests/notification_bus_tests.py -v
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from keepup import notification_bus as nb
from keepup.db import DatabaseManagerV2
from keepup.factory import _start_notification_bus, _stop_notification_bus, create_app
from keepup.settings import KeepupSettings


@pytest.fixture(autouse=True)
def no_bus_left_behind():
    nb.set_notification_bus(None)
    yield
    nb.set_notification_bus(None)


def test_the_framework_has_no_channel_of_its_own():
    with pytest.raises(ValueError):
        nb.NotificationBus("replica-a", "")
    assert not hasattr(nb, "CHANNEL_NAME")


def test_an_own_echo_is_not_delivered_but_a_foreign_envelope_is():
    received = []
    bus = nb.NotificationBus("replica-a", "some_channel")

    async def handler(envelope):
        received.append(envelope["n"])

    bus.subscribe(handler)

    async def scenario():
        bus._on_notify(None, 0, "some_channel", nb.encode_envelope({"n": 1, nb.ORIGIN_FIELD: "replica-a"}))
        bus._on_notify(None, 0, "some_channel", nb.encode_envelope({"n": 2, nb.ORIGIN_FIELD: "replica-b"}))
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert received == [2]


def test_without_a_channel_no_bus_is_installed():
    assert asyncio.run(_start_notification_bus(None)) is None
    assert nb.get_notification_bus() is None


def test_on_sqlite_the_bus_is_installed_and_delivers_locally_only():
    """The development mode: nothing to listen on, local delivery still works."""
    received = []

    async def scenario():
        bus = await _start_notification_bus("some_channel")
        assert nb.get_notification_bus() is bus
        assert not bus.is_running

        async def handler(envelope):
            received.append(envelope["n"])

        bus.subscribe(handler)
        published = await nb.deliver({"n": 7})
        await _stop_notification_bus(bus)
        return published

    assert asyncio.run(scenario()) is False
    assert received == [7]
    assert nb.get_notification_bus() is None


def test_stopping_leaves_a_bus_installed_by_someone_else_alone():
    async def scenario():
        ours = await _start_notification_bus("some_channel")
        theirs = nb.NotificationBus("replica-b", "other_channel")
        nb.set_notification_bus(theirs)
        await _stop_notification_bus(ours)
        return theirs

    theirs = asyncio.run(scenario())
    assert nb.get_notification_bus() is theirs


def test_the_application_sees_the_bus_from_on_startup_until_on_shutdown():
    seen = {}

    async def on_startup(app):
        bus = nb.get_notification_bus()
        seen["startup"] = bus and bus._channel

    async def on_shutdown(app):
        seen["shutdown"] = nb.get_notification_bus() is not None

    app = create_app(KeepupSettings(title="Bus", static_mounts=(), plugin_manager=None,
                                    notification_channel="bus_test_channel",
                                    on_startup=on_startup, on_shutdown=on_shutdown))
    with TestClient(app):
        seen["running"] = nb.get_notification_bus() is not None

    assert seen == {"startup": "bus_test_channel", "running": True, "shutdown": True}
    assert nb.get_notification_bus() is None


# --- publishing is not done on the listening connection (keepup-50) -----------------

class ListeningConnection:
    """asyncpg as it behaves: one operation at a time, and a listener to attach."""

    def __init__(self, attach_delay=0.0):
        self.busy = False
        self.attach_delay = attach_delay
        self.closed = False

    async def execute(self, *args):
        if self.busy:
            raise RuntimeError("cannot perform operation: another operation is in progress")
        self.busy = True
        try:
            await asyncio.sleep(0.01)
        finally:
            self.busy = False

    async def add_listener(self, channel, handler):
        await asyncio.sleep(self.attach_delay)

    def is_closed(self):
        return self.closed

    async def close(self):
        self.closed = True


@pytest.fixture
def sent(monkeypatch):
    """What reached pg_notify through the pool."""
    record = []

    async def notify(query, params=None):
        assert "pg_notify" in query
        await asyncio.sleep(0.01)
        record.append(params)
        return 1
    monkeypatch.setattr(DatabaseManagerV2, "execute_commit_async", notify)
    return record


def started_bus():
    bus = nb.NotificationBus("replica-a", "bus_test_channel")
    bus._started = True
    bus._connection = ListeningConnection()
    return bus


async def test_envelopes_published_at_once_all_go_out(sent):
    bus = started_bus()
    results = await asyncio.gather(*(bus.publish({"kind": "k", "n": n}) for n in range(10)))
    assert results == [True] * 10
    assert sorted(nb.decode_envelope(p["payload"])["n"] for p in sent) == list(range(10))
    assert all(p["channel"] == "bus_test_channel" for p in sent)


async def test_publishing_leaves_the_listening_connection_alone(sent):
    bus = started_bus()
    listening = bus._connection
    listening.busy = True                  # the listener is in the middle of something
    assert await bus.publish({"kind": "k"}) is True


async def test_publishing_works_while_the_listener_reconnects(sent):
    bus = started_bus()
    bus._connection = None                 # the listener lost its connection
    assert not bus.is_running and bus.can_publish
    assert await bus.publish({"kind": "k"}) is True


async def test_a_bus_that_was_not_started_does_not_publish(sent):
    bus = nb.NotificationBus("replica-a", "bus_test_channel")
    assert await bus.publish({"kind": "k"}) is False
    assert sent == []


async def test_a_failed_publish_is_false_and_not_raised(monkeypatch):
    async def broken(query, params=None):
        raise RuntimeError("the database went away")
    monkeypatch.setattr(DatabaseManagerV2, "execute_commit_async", broken)
    assert await started_bus().publish({"kind": "k"}) is False


async def test_the_bus_says_it_runs_only_once_it_listens(monkeypatch):
    bus = nb.NotificationBus("replica-a", "bus_test_channel")
    connection = ListeningConnection(attach_delay=0.2)

    async def connect():
        return connection
    monkeypatch.setattr(bus, "_connect", connect)
    listener = asyncio.create_task(bus._listen_forever())
    await asyncio.sleep(0.05)
    assert not bus.is_running              # still attaching its listener
    await asyncio.sleep(0.3)
    assert bus.is_running
    bus._stopping = True
    listener.cancel()
    with pytest.raises(asyncio.CancelledError):
        await listener
