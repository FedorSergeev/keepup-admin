"""The audit names who called, and a refused sign-in leaves a trace (keepup-172).

The incoming-request audit kept the actor inside ``request_data``, and redaction
replaces every value there: every row said ``<hidden>`` where the account should
be. The framework's own administrative routes were not written at all. These
checks hold both down -- the actor is a column and stays readable, a framework
route reaches the same seam as a plugin route, and a refused sign-in is visible
in the log and in the attempt it counted.

    python -m pytest tests/audit_actor_tests.py -v
"""

import asyncio
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from framework_schema import create_framework_tables

from keepup.kernel.observability import audited
from keepup.settings import KeepupSettings
from keepup_http.factory import create_app
from keepup_audit import audit
from keepup_db import DatabaseManagerV2, db_config
from keepup_auth import login_throttle

PACKAGE = Path(__file__).resolve().parents[1]
STATIC = PACKAGE / "packages" / "keepup-http" / "keepup_http" / "static"


@pytest.fixture
def database(tmp_path, monkeypatch):
    """A database of this check's own, and an empty audit buffer."""
    DatabaseManagerV2.dispose()
    monkeypatch.setattr(db_config, "db_path", str(tmp_path / "audit.db"), raising=False)
    monkeypatch.setattr(db_config, "db_type", "sqlite", raising=False)
    create_framework_tables()
    audit.incoming_requests_buffer.clear()
    yield
    audit.incoming_requests_buffer.clear()
    DatabaseManagerV2.dispose()


def rows():
    return DatabaseManagerV2.execute("SELECT * FROM incoming_requests")


def test_the_actor_is_a_column_and_stays_readable(database):
    """Redaction hides the body; it must not hide who called."""

    async def record():
        request_id = await audit.IncomingRequestLogger.start_request(
            "replica-1", "POST", "/api/admin/users/2/roles", "framework",
            {"role": "ADMIN"}, actor={"id": 7, "username": "ada"})
        await audit.IncomingRequestLogger.end_request(request_id, http_status=200)
        return await audit.IncomingRequestLogger.flush_buffer()

    assert asyncio.run(record()) == 1
    [row] = rows()
    assert row["user_id"] == 7
    assert row["username"] == "ada"
    assert "ada" not in (row["request_data"] or ""), "the body stays redacted"


def test_a_framework_route_reaches_the_same_seam(database):
    """A route that is not a plugin route is still an administrator's action."""
    app = FastAPI()

    def signed_in():
        return {"id": 1, "username": "root"}

    @app.put("/admin/roles")
    @audited("users.roles", method="PUT", actor="admin")
    async def set_roles(admin: dict = Depends(signed_in)):
        return {"success": True}

    answer = TestClient(app).put("/admin/roles")
    assert answer.status_code == 200

    asyncio.run(audit.IncomingRequestLogger.flush_buffer())
    [row] = rows()
    assert row["endpoint"] == "users.roles"
    assert row["username"] == "root"
    assert row["http_status"] == 200


def test_a_refused_sign_in_is_logged_with_its_address(database, caplog):
    """The name and the address, never the password."""
    DatabaseManagerV2.execute_commit("DELETE FROM auth_rate_limit")
    app = create_app(KeepupSettings(title="Audit", project_name="audit",
                                    static_dir=str(STATIC), static_mounts=()))

    answer = TestClient(app).post("/api/auth/login",
                                  json={"username": "nobody", "password": "wrong"})
    assert answer.status_code == 401
    assert "Sign-in refused" in caplog.text
    assert "nobody" in caplog.text

    row = DatabaseManagerV2.execute_one(
        "SELECT failures, last_address FROM login_attempts WHERE username = :name",
        {"name": "nobody"})
    assert row["failures"] >= 1
    assert row["last_address"] == "testclient"


def test_the_attempt_counter_keeps_the_address(database):
    login_throttle.reserve_attempt("ada", address="203.0.113.9")
    row = DatabaseManagerV2.execute_one(
        "SELECT last_address FROM login_attempts WHERE username = :name",
        {"name": "ada"})
    assert row["last_address"] == "203.0.113.9"
