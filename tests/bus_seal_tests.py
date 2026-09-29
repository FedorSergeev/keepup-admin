"""Envelopes on the replicas' bus are sealed (keepup-66).

Any role that can connect to the database can NOTIFY on the application's
channel, and an envelope reached host agents' sockets on nothing but its shape.
An envelope is now accepted only under a seal made with a key derived from the
application's signing secret, and only while fresh.

    python3 -m pytest keepup/tests/bus_seal_tests.py -v
"""

import json
import time

import pytest

from keepup import notification_bus as nb


def delivered(payload):
    got = []
    bus = nb.NotificationBus("replica-b", "some_channel")

    async def handler(envelope):
        got.append(envelope)

    bus.subscribe(handler)

    import asyncio

    async def run():
        bus._on_notify(None, 0, "some_channel", payload)
        await asyncio.sleep(0)

    asyncio.run(run())
    return got


def test_a_sealed_envelope_is_delivered_without_its_seal():
    payload = nb.encode_envelope({"kind": "x", nb.ORIGIN_FIELD: "replica-a"})
    assert delivered(payload) == [{"kind": "x", nb.ORIGIN_FIELD: "replica-a"}]


def test_what_somebody_else_notifies_is_dropped():
    """What a database role would send with a bare pg_notify."""
    forged = json.dumps({"kind": "participant_message", "to": "agent-7", "command": "stop_vm"})
    assert delivered(forged) == []


def test_a_changed_envelope_loses_its_seal():
    sealed = json.loads(nb.encode_envelope({"kind": "x", "node": 1}))
    sealed["node"] = 2
    assert delivered(json.dumps(sealed)) == []


def test_a_seal_made_with_another_secret_is_refused(monkeypatch):
    payload = nb.encode_envelope({"kind": "x"})
    monkeypatch.setenv("SECRET_KEY", "a-different-deployment-secret-of-enough-length")
    assert nb.decode_envelope(payload) is None


def test_a_captured_envelope_cannot_be_replayed_later(monkeypatch):
    payload = nb.encode_envelope({"kind": "x"})
    later = time.time() + nb.MAX_ENVELOPE_AGE_SECONDS + 1
    monkeypatch.setattr(nb.time, "time", lambda: later)
    assert nb.decode_envelope(payload) is None


def test_the_seal_is_not_the_signing_secret(monkeypatch):
    import os
    payload = nb.encode_envelope({"kind": "x"})
    assert os.environ["SECRET_KEY"] not in payload


@pytest.mark.parametrize("value", ["", None, 12, "0" * 64])
def test_a_missing_or_malformed_seal_is_refused(value):
    sealed = json.loads(nb.encode_envelope({"kind": "x"}))
    sealed[nb.SEAL_FIELD] = value
    assert nb.decode_envelope(json.dumps(sealed)) is None


def test_publishing_without_a_secret_does_not_raise(monkeypatch):
    import asyncio
    monkeypatch.delenv("SECRET_KEY")
    monkeypatch.setattr("keepup.auth.signing_key._configured_key", lambda config: "")
    bus = nb.NotificationBus("replica-a", "some_channel")
    bus._started = True
    assert asyncio.run(bus.publish({"kind": "x"})) is False
