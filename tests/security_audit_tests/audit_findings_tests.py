"""One check per finding of the audit after 0.2.0 (keepup-52), by its number.

Each finding was fixed by its own task, with its own tests next to the fix. This
file does not repeat them: it holds each finding shut from the outside, in one
place, so a report can say "finding 7: holds" -- and a finding that comes back
is named by the number it had in the audit (doc/keepup_security.md).

Where a finding is observable through the application, the check goes through
a started profile; where it is about how a part is built (a queue, a clock, a
script of the panel), the check looks at the part that closed it.

    python3 -m pytest keepup/tests/security_audit_tests/audit_findings_tests.py -v
"""

import asyncio
import io
import json
import logging
import re
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from keepup.auth import dependencies, login_throttle, panel_session, user_roles
from keepup.db import DatabaseManagerV2
from keepup.roles import ROLE_ADMIN, ROLE_CLIENT

import audit_actors

pytestmark = pytest.mark.area("findings")

PACKAGE = Path(__file__).resolve().parents[2]
AUDIT = "keepup-52"
FINDINGS = 20


def finding(number: int):
    return pytest.mark.finding(f"{AUDIT}#{number}")


@pytest.fixture
def throttle(monkeypatch):
    """A known limit and a clean count, for the checks that sign in."""
    from keepup.schema import init_db
    init_db()

    def set_limit(limit):
        monkeypatch.setenv(login_throttle.MAX_ATTEMPTS_ENV, str(limit))
        DatabaseManagerV2.execute_commit("DELETE FROM login_attempts")
    set_limit(100)
    yield set_limit
    DatabaseManagerV2.execute_commit("DELETE FROM login_attempts")


def test_every_finding_has_exactly_one_check():
    """The file is the audit's table: a number missing is a finding nobody holds."""
    module = sys.modules[__name__]
    numbers = []
    for name in dir(module):
        function = getattr(module, name)
        if not name.startswith("test_") or not callable(function):
            continue
        for mark in getattr(function, "pytestmark", []):
            if mark.name == "finding":
                numbers.append(int(mark.args[0].split("#")[1]))
    assert sorted(numbers) == list(range(1, FINDINGS + 1))


# --- 1-5 ---------------------------------------------------------------------------

@finding(1)
def test_1_a_large_body_is_refused_before_it_is_parsed_or_signed_in(start, throttle):
    running = start("bare")
    body = json.dumps({"username": "a" * (3 * 1024 * 1024), "password": "x"})
    answer = running.client.post("/api/auth/login", content=body,
                                 headers={"content-type": "application/json"})
    assert answer.status_code == 413
    # Refused before the sign-in: no attempt was counted against any name.
    assert DatabaseManagerV2.execute_one("SELECT COUNT(*) AS n FROM login_attempts")["n"] == 0


@finding(2)
def test_2_a_name_with_markup_never_becomes_an_account(start, monkeypatch):
    hostile = "<img src=x onerror=alert(1)>"
    running = start("bare")
    monkeypatch.setenv("SELF_REGISTRATION_ENABLED", "true")
    answer = running.client.post("/api/auth/register", json={
        "username": hostile, "password": audit_actors.ACTOR_PASSWORD, "agree_terms": True})
    assert answer.status_code in (400, 422), answer.text
    assert dependencies.get_user_by_username(hostile) is None
    # And the name an outside identity brings is made plain before it is used.
    from keepup.auth.external_accounts import propose_username
    from keepup.auth.usernames import is_valid_username
    assert is_valid_username(propose_username(hostile))


@finding(3)
def test_3_parallel_guesses_stop_at_the_limit(start, throttle):
    throttle(5)
    running = start("bare")
    target = audit_actors.client_actor()

    async def guess():
        transport = httpx.ASGITransport(app=running.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://audit") as client:
            answers = await asyncio.gather(*(
                client.post("/api/auth/login", json={"username": target.username,
                                                     "password": "wrong"})
                for _ in range(20)))
        return [answer.status_code for answer in answers]

    codes = asyncio.run(guess())
    assert codes.count(401) == 5 and codes.count(429) == 15, codes


@finding(4)
def test_4_the_time_of_the_answer_does_not_say_whether_a_name_exists(start, throttle):
    running = start("bare")
    target = audit_actors.client_actor()

    def timed(name):
        began = time.perf_counter()
        running.client.post("/api/auth/login", json={"username": name, "password": "wrong"})
        return time.perf_counter() - began

    existing = statistics.median(timed(target.username) for _ in range(3))
    missing = statistics.median(timed(f"nobody-{i}-{target.username}") for i in range(3))
    # Before the fix: 0.002 s against 0.27 s. A generous margin, for a busy machine.
    assert missing > existing * 0.3, (missing, existing)


@finding(5)
def test_5_an_old_session_is_renewed_by_neither_door(start):
    running = start("bare")
    actor = audit_actors.client_actor()
    sid = panel_session.open_session(actor.id, timedelta(hours=1))
    old = dependencies.create_access_token(
        {"sub": actor.username, panel_session.SESSION_CLAIM: sid},
        expires_delta=timedelta(hours=1),
        session_started_at=datetime.utcnow() - timedelta(days=365))
    for door in ("/api/auth/refresh", "/api/auth/session"):
        assert running.client.post(door, headers={"Authorization": f"Bearer {old}"}
                                   ).status_code == 401, door
    # And a token that names no session at all is not a token (keepup-81).
    orphan = audit_actors.without_session(actor)
    assert running.client.get("/api/auth/me",
                              headers={"Authorization": f"Bearer {orphan}"}).status_code == 401

# --- 6-10 --------------------------------------------------------------------------

class HeldSocket:
    """What socket_sessions needs of a socket: somewhere to be closed."""

    def __init__(self):
        self.closed = None

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


@finding(6)
def test_6_a_revoked_session_closes_its_open_socket(start):
    from keepup.auth import socket_sessions

    start("plugins")
    ended, going_on = audit_actors.client_actor(), audit_actors.client_actor()
    sockets = {ended.sid: HeldSocket(), going_on.sid: HeldSocket()}
    for sid, socket in sockets.items():
        socket_sessions.hold(socket, {"session_id": sid})
    panel_session.revoke(ended.sid)
    asyncio.run(socket_sessions.sweep())
    assert sockets[ended.sid].closed == (socket_sessions.CLOSE_CODE, socket_sessions.CLOSE_REASON)
    assert sockets[going_on.sid].closed is None


@finding(7)
def test_7_an_envelope_on_the_bus_that_nobody_sealed_is_dropped():
    from keepup import notification_bus

    sealed = notification_bus.encode_envelope({"kind": "keepup.sessions.revoked", "sid": "a"})
    assert notification_bus.decode_envelope(sealed) is not None
    tampered = json.loads(sealed)
    tampered_text = json.dumps(tampered).replace('"sid": "a"', '"sid": "b"')
    assert tampered_text != json.dumps(tampered)
    assert notification_bus.decode_envelope(tampered_text) is None
    # What anybody with pg_notify could send: an envelope with no seal at all.
    assert notification_bus.decode_envelope(json.dumps(
        {"kind": "keepup.sessions.revoked", "sid": "a"})) is None


@finding(8)
def test_8_the_administrator_leaves_a_trail_and_cannot_forge_one(start):
    from keepup import admin_trail

    running = start("bare")
    admin = audit_actors.admin()
    for reserved in admin_trail.ADMIN_EVENT_TYPES:
        forged = running.client.post("/api/events", headers=admin.bearer(),
                                     json={"event_type": reserved, "event_text": "forged"})
        assert forged.status_code == 400, reserved
    purged = running.client.delete("/api/events/cleanup?days=30", headers=admin.bearer())
    assert purged.status_code == 200
    trail = DatabaseManagerV2.execute(
        "SELECT event_text, event_data FROM app_events WHERE event_type = :t",
        {"t": admin_trail.EVENTS_PURGED})
    assert trail and all(row["event_text"] != "forged" for row in trail)
    assert running.client.post("/api/admin/instances/no-such-replica/restart",
                               headers=admin.bearer()).status_code == 404


@finding(9)
def test_9_log_and_audit_queues_are_bounded(monkeypatch):
    from keepup import audit, log_shipping

    monkeypatch.setattr(log_shipping, "REMOTE_LOG_URL", None)
    monkeypatch.setattr(log_shipping, "REMOTE_LOG_TOKEN", "a-token")
    monkeypatch.setattr(log_shipping, "REMOTE_MAX_QUEUED", 5, raising=False)
    wrapper = log_shipping.RemoteLoggerWrapper(batch_size=10_000)
    for index in range(12):
        wrapper.emit(logging.LogRecord("audit", logging.INFO, __file__, 1, f"m{index}", None, None))
    assert wrapper.log_queue.qsize() <= 5
    assert wrapper.dropped >= 7
    # The audit buffer has a ceiling of its own, finite whatever is configured.
    assert 0 < audit.BUFFER_HARD_LIMIT < float("inf")


@finding(10)
def test_10_the_dependency_floors_are_past_every_known_advisory():
    spec = PACKAGE / ".github" / "scripts" / "floor_requirements.py"
    if not spec.is_file():
        pytest.skip("floor_requirements.py is not in this checkout")
    import importlib.util

    loader = importlib.util.spec_from_file_location("floor_requirements", spec)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    floors = dict(module.floors_everywhere(PACKAGE))
    clean = {"pyjwt": "2.13", "python-multipart": "0.0.31", "requests": "2.33",
             "cryptography": "50.0", "urllib3": "2.7"}

    def version(text):
        return tuple(int(part) for part in re.findall(r"\d+", text)[:3])

    names = {name.lower(): floor for name, floor in floors.items()}
    for name, safe in clean.items():
        assert name in names, f"{name} has no floor"
        assert version(names[name]) >= version(safe), (name, names[name], safe)

# --- 11-15 -------------------------------------------------------------------------

@finding(11)
def test_11_lock_times_are_written_and_compared_by_one_clock():
    """The database's clock was written, the application's compared against it."""
    source = (PACKAGE / "locks.py").read_text(encoding="utf-8")
    statements = re.findall(r'"""(.*?)"""|"([^"\n]*)"', source, flags=re.S)
    sql = " ".join(a or b for a, b in statements if re.search(r"\b(INSERT|UPDATE|SELECT)\b",
                                                               a or b))
    assert "CURRENT_TIMESTAMP" not in sql and "NOW()" not in sql.upper()


@finding(12)
def test_12_the_last_administrator_keeps_the_role(start):
    running = start("bare")
    me = audit_actors.admin()
    answer = running.client.put(f"/api/admin/users/{me.id}/roles", headers=me.bearer(),
                                json={"roles": [ROLE_CLIENT]})
    assert answer.status_code == 400
    assert ROLE_ADMIN in user_roles.roles_of(me.id)
    unknown = running.client.patch(f"/api/admin/users/{me.id}", headers=me.bearer(),
                                   json={"role": "SUPERUSER"})
    assert unknown.status_code == 400


@finding(13)
def test_13_the_csrf_value_belongs_to_the_session_and_changes_with_it(start, throttle):
    running = start("bare")
    actor = audit_actors.client_actor()
    planted = {"Cookie": f"{panel_session.SESSION_COOKIE}={actor.token}; "
                         f"{panel_session.CSRF_COOKIE}=chosen-by-the-attacker",
               panel_session.CSRF_HEADER: "chosen-by-the-attacker"}
    assert running.client.post("/api/auth/refresh", headers=planted).status_code == 403

    values = []
    for _ in range(2):
        answer = running.client.post("/api/auth/login", json={
            "username": actor.username, "password": audit_actors.ACTOR_PASSWORD})
        assert answer.status_code == 200, answer.text
        values.append(answer.cookies.get(panel_session.CSRF_COOKIE))
        running.client.cookies.clear()
    assert values[0] and values[1] and values[0] != values[1]


@finding(14)
@pytest.mark.parametrize("verified", [False, "false", "False", "", "yes", 1, None])
def test_14_the_domain_policy_admits_only_a_verified_address(verified):
    from keepup.auth import oidc_policy

    policy = oidc_policy.create_if_email_domain(["example.com"])
    decision = oidc_policy.decide(policy, {"sub": "s", "email": "person@example.com",
                                           "email_verified": verified})
    assert decision.admit is False


@finding(15)
def test_15_the_first_administrator_s_password_stays_out_of_the_logs(caplog, start, throttle):
    from keepup.auth import seed_accounts

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, "
                       "password_hash TEXT, status TEXT, role TEXT, auth_source TEXT)")
    console, announced = io.StringIO(), {}
    original = seed_accounts.announce_generated_admin_password

    def capture(password, log=seed_accounts.logger):
        announced["password"] = password
        original(password, log=log, console=console)

    with caplog.at_level(logging.DEBUG):
        seed_accounts.announce_generated_admin_password = capture
        try:
            seed_accounts.ensure_admin(connection.cursor(), role=ROLE_ADMIN, auth_source="local",
                                       is_postgres=False, environ={})
        finally:
            seed_accounts.announce_generated_admin_password = original
            connection.close()
    assert announced["password"] in console.getvalue()
    assert announced["password"] not in caplog.text

    # And the public password of earlier builds signs nobody in.
    running = start("bare")
    answer = running.client.post("/api/auth/login", json={
        "username": seed_accounts.ADMIN_USERNAME,
        "password": seed_accounts.RETIRED_ADMIN_PASSWORDS[0]})
    assert answer.status_code in (401, 403)
    assert "access_token" not in answer.text

# --- 16-20 -------------------------------------------------------------------------

@finding(16)
def test_16_the_collector_s_token_is_written_nowhere(monkeypatch, tmp_path, caplog):
    from keepup import log_shipping

    class Issued:
        status_code = 200

        @staticmethod
        def json():
            return {"api_token": "issued-collector-token-94", "id": 7, "name": "audit"}

    calls = []
    monkeypatch.setattr(log_shipping, "REMOTE_LOG_URL", "http://collector.invalid/logs")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    monkeypatch.setattr(log_shipping.requests, "post",
                        lambda url, **kwargs: calls.append(kwargs) or Issued())
    with caplog.at_level(logging.DEBUG):
        token = log_shipping.create_logger_token("admin-token")
    assert token == "issued-collector-token-94"
    assert list((tmp_path / "config").iterdir()) == []
    assert token not in caplog.text
    assert calls and calls[0].get("timeout")


@finding(17)
def test_17_a_failure_answers_without_its_text_and_a_path_cannot_forge_a_log_line(
        start, monkeypatch):
    from keepup import events
    from keepup.logging_setup import for_log

    running = start("bare")
    admin = audit_actors.admin()
    secret = "sk-live-audit-94-do-not-show"

    def explode(*args, **kwargs):
        raise RuntimeError(f"connection string with {secret}")

    monkeypatch.setattr(events.event_manager, "get_events", explode)
    answer = running.client.get("/api/events", headers=admin.bearer())
    assert answer.status_code == 500
    assert secret not in answer.text

    forged = for_log("/api/x\n2026-09-30 INFO admin signed in")
    assert "\n" not in forged


@finding(18)
def test_18_the_panel_shell_hears_only_its_own_origin():
    """The message handler of the shell; its behaviour under node is keepup/tests'."""
    shell = (PACKAGE / "static" / "js" / "main_new.js").read_text(encoding="utf-8")
    start_at = shell.find("addEventListener('message'")
    if start_at < 0:
        start_at = shell.find('addEventListener("message"')
    assert start_at >= 0, "the shell listens to no window message at all"
    assert re.search(r"\.origin\s*!==?\s*window\.location\.origin|"
                     r"window\.location\.origin\s*!==?\s*\w+\.origin", shell), \
        "no check of the sender's origin in the shell"


@finding(19)
@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf", "Infinity", "1e999"])
def test_19_the_request_mask_refuses_what_is_not_a_finite_number(value):
    from keepup.plugins import route_mask

    admitted, problem = route_mask.Parameter("share", {"type": "float", "min": 0, "max": 1},
                                             ()).admit(value)
    assert problem, (value, admitted)


@finding(20)
def test_20_creating_a_user_asks_the_provider_once(monkeypatch, caplog):
    calls = []
    original = dependencies.auth_provider.create_user_in_db

    def counted(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(dependencies.auth_provider, "create_user_in_db", counted)
    name = f"audit-once-{time.time_ns()}"
    with caplog.at_level(logging.ERROR):
        user_id = asyncio.run(dependencies.create_user(name, audit_actors.ACTOR_PASSWORD,
                                                       agree_terms=True))
    assert user_id is not None and len(calls) == 1
    assert DatabaseManagerV2.execute_one(
        "SELECT COUNT(*) AS n FROM users WHERE username = :n", {"n": name})["n"] == 1
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
