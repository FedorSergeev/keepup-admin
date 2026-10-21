"""A sign-in has a price, per address and for the deployment (keepup-171).

The per-name lockout answers "is this password being guessed". It cannot see one
address trying one password across a thousand names, and it says nothing about
the bcrypt every one of those attempts costs. These checks are about the two
buckets that can: the wall-clock minute of one address, and the minute of the
whole deployment.

    python -m pytest tests/auth_rate_limit_tests.py -v
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from keepup.settings import KeepupSettings
from keepup_http.factory import create_app
from keepup_db import DatabaseManagerV2
from keepup_auth import auth_rate_limit

PACKAGE = Path(__file__).resolve().parents[1]
STATIC = PACKAGE / "packages" / "keepup-http" / "keepup_http" / "static"
NOW = datetime(2026, 10, 21, 12, 0, 0)


def call_from(address: str) -> Request:
    """A request whose caller is this address, and nothing else about it."""
    return Request({"type": "http", "method": "POST", "path": "/api/auth/login",
                    "query_string": b"", "headers": [], "client": (address, 1234)})


@pytest.fixture(autouse=True)
def an_empty_window():
    """Each check starts with no count in the table, and leaves none."""
    DatabaseManagerV2.execute_commit("DELETE FROM auth_rate_limit")
    yield
    DatabaseManagerV2.execute_commit("DELETE FROM auth_rate_limit")


def budgets(monkeypatch, per_address=1000, overall=1000):
    monkeypatch.setenv("AUTH_RATE_LIMIT_PER_MINUTE", str(per_address))
    monkeypatch.setenv("AUTH_RATE_LIMIT_GLOBAL_PER_MINUTE", str(overall))


def test_one_address_is_refused_after_its_budget(monkeypatch):
    budgets(monkeypatch, per_address=3)
    for _ in range(3):
        auth_rate_limit.check(call_from("10.0.0.1"), now=NOW)

    with pytest.raises(Exception) as refused:
        auth_rate_limit.check(call_from("10.0.0.1"), now=NOW)
    assert getattr(refused.value, "status_code", None) == 429


def test_a_refusal_carries_retry_after(monkeypatch):
    budgets(monkeypatch, per_address=1)
    auth_rate_limit.check(call_from("10.0.0.2"), now=NOW)
    with pytest.raises(Exception) as refused:
        auth_rate_limit.check(call_from("10.0.0.2"), now=NOW)
    retry = refused.value.headers["Retry-After"]
    assert 1 <= int(retry) <= 60


def test_another_address_does_not_spend_the_first_budget(monkeypatch):
    budgets(monkeypatch, per_address=2)
    for _ in range(2):
        auth_rate_limit.check(call_from("10.0.0.3"), now=NOW)
    auth_rate_limit.check(call_from("10.0.0.4"), now=NOW)


def test_the_deployment_budget_covers_every_address(monkeypatch):
    budgets(monkeypatch, per_address=1000, overall=2)
    auth_rate_limit.check(call_from("10.0.0.5"), now=NOW)
    auth_rate_limit.check(call_from("10.0.0.6"), now=NOW)
    with pytest.raises(Exception) as refused:
        auth_rate_limit.check(call_from("10.0.0.7"), now=NOW)
    assert getattr(refused.value, "status_code", None) == 429


def test_a_new_minute_starts_the_count_again(monkeypatch):
    budgets(monkeypatch, per_address=1)
    auth_rate_limit.check(call_from("10.0.0.8"), now=NOW)
    auth_rate_limit.check(call_from("10.0.0.8"), now=NOW + timedelta(minutes=1))


def test_zero_switches_a_bucket_off(monkeypatch):
    budgets(monkeypatch, per_address=0, overall=0)
    for _ in range(100):
        auth_rate_limit.check(call_from("10.0.0.9"), now=NOW)


def test_the_login_route_counts_the_address_before_the_password(monkeypatch):
    """The route calls the limiter, not only the module: this is the wiring."""
    budgets(monkeypatch, per_address=2, overall=1000)
    app = create_app(KeepupSettings(title="Rate", project_name="rate",
                                    static_dir=str(STATIC), static_mounts=()))
    client = TestClient(app)

    answers = [client.post("/api/auth/login",
                           json={"username": "nobody", "password": "wrong"})
               for _ in range(3)]
    assert [answer.status_code for answer in answers[:2]] == [401, 401]
    assert answers[2].status_code == 429
    assert "Retry-After" in answers[2].headers
