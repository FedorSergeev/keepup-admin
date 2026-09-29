"""Registering with the log collector writes no token to disk (keepup-75).

The issued token was written in the clear to `config/logger_token.json` -- a file
with default permissions that git did not ignore, in a directory that is
committed with environment values -- and the request had no timeout, so an
unanswering collector held the caller for ever. The token is now returned for
the caller to keep in the environment, and the request is bounded.

    python3 -m pytest keepup/tests/collector_token_tests.py -v
"""

import logging

import pytest

from keepup import log_shipping


class Issued:
    status_code = 200

    @staticmethod
    def json():
        return {"api_token": "issued-secret-token", "id": 7, "name": "product"}


@pytest.fixture
def collector(monkeypatch, tmp_path):
    monkeypatch.setattr(log_shipping, "REMOTE_LOG_URL", "http://collector.invalid/logs")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs)
        return Issued()

    monkeypatch.setattr(log_shipping.requests, "post", post)
    return calls


def test_the_token_is_returned_and_written_nowhere(collector, tmp_path, caplog):
    with caplog.at_level(logging.DEBUG):
        token = log_shipping.create_logger_token("admin-token")
    assert token == "issued-secret-token"
    assert list((tmp_path / "config").iterdir()) == []
    assert "issued-secret-token" not in caplog.text


def test_the_registration_does_not_wait_for_ever(collector):
    log_shipping.create_logger_token("admin-token")
    assert collector[0].get("timeout") == log_shipping.REGISTRATION_TIMEOUT


def test_an_unanswering_collector_is_a_refusal_not_an_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(log_shipping, "REMOTE_LOG_URL", "http://collector.invalid/logs")

    def hang(url, **kwargs):
        raise log_shipping.requests.exceptions.Timeout("no answer")

    monkeypatch.setattr(log_shipping.requests, "post", hang)
    assert log_shipping.create_logger_token("admin-token") is None
