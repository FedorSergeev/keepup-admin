"""The replicas' notification bus as a capability of the framework.

The transport's own rules (echo, envelope limit, a failing publish) are covered
against ServerShare's facade in tests/servershare_notification_bus_tests.py;
what is checked here is what makes it a framework capability: it carries no
channel of any product, an application switches it on with one setting, and
the framework installs it before anything that subscribes and removes it after.

    python3 -m pytest keepup/tests/notification_bus_tests.py -v
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from keepup import notification_bus as nb
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
