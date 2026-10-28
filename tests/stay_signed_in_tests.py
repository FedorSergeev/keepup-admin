"""A session that was asked to stay signed in is renewed, not expired (keepup-177).

The access token lives an hour and the ordinary session was held to a day of
renewals counted from the login. An open panel renewed itself every minute and
was still signed out in the middle of the work after that day -- and a closed
browser ended the session no matter what. The checkbox on the sign-in screen
promises the other behaviour: the cookies outlive the browser, the session
renews for as long as it is used, and after the token has expired a long-lived,
HttpOnly renewal credential buys the next one.

These checks drive the real routes: the credential, its CSRF pair, the idle
window, revocation and the second factor's challenge carrying the answer.

    python3 -m pytest tests/stay_signed_in_tests.py -v
"""

import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keepup import clock
from keepup.settings import KeepupSettings
from keepup_auth import dependencies, panel_session
from keepup_auth.signing_key import decode_token
from keepup_http.factory import create_app
from keepup_db import DatabaseManagerV2

PACKAGE = Path(__file__).resolve().parents[1]
SHELL = (PACKAGE / "packages" / "keepup-http" / "keepup_http" / "static"
         / "js" / "main_new.js")
STATIC = PACKAGE / "packages" / "keepup-http" / "keepup_http" / "static"
NODE = shutil.which("node")
PASSWORD = "a-long-password-for-staying-signed-in"


def make_app():
    """One application whose cookies carry the plain names (no namespace)."""
    return create_app(KeepupSettings(
        title="Stay signed in",
        project_name="stay-signed-in",
        cookie_prefix="",
        plugin_manager=None,
        static_mounts=(),
    ))


def account(name):
    """An active local account with a known password."""
    name = f"{name}-{uuid.uuid4().hex[:8]}"
    uid = dependencies.save_user_to_db(name, PASSWORD)
    dependencies.update_user(uid, status="active")
    return name, uid


def sign_in(client, name, remember):
    answer = client.post("/api/auth/login", json={
        "username": name, "password": PASSWORD, "remember": remember})
    assert answer.status_code == 200, answer.text
    return answer


def jar(client):
    return {cookie.name: cookie.value for cookie in client.cookies.jar}


def cookie_header(cookies):
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def csrf_of(answer):
    return answer.cookies.get(panel_session.CSRF_COOKIE)


def sid_of(answer):
    token = answer.cookies.get(panel_session.SESSION_COOKIE)
    return decode_token(token, options={"verify_exp": False})[panel_session.SESSION_CLAIM]


def expired_token(name, sid):
    """A token of this session that is past its end, as a slept tab holds one."""
    return dependencies.create_access_token(
        {"sub": name, panel_session.SESSION_CLAIM: sid},
        expires_delta=timedelta(seconds=-5),
        session_started_at=clock.utcnow() - timedelta(hours=2))


def refresh(client, cookies, csrf=None, bearer=None):
    """Exchange the session for a new token, the way the panel does."""
    headers = {"Cookie": cookie_header(cookies)}
    if csrf:
        headers[panel_session.CSRF_HEADER] = csrf
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return client.post("/api/auth/refresh", headers=headers)


# --- what the checkbox puts in the browser ------------------------------------

def test_a_remembered_sign_in_keeps_a_renewal_credential():
    name, _ = account("stays")
    with TestClient(make_app()) as client:
        answer = sign_in(client, name, remember=True)

    cookies = jar(client)
    assert panel_session.refresh_token_in(cookies)
    assert panel_session.refresh_cookie_name(False) in cookies

    lines = [line for line in answer.headers.get_list("set-cookie")
             if line.startswith(f"{panel_session.refresh_cookie_name(False)}=")]
    assert lines, "the renewal credential is not in the answer"
    assert "HttpOnly" in lines[0], lines[0]
    # Not the token's hour: the browser must keep it while the session may live.
    assert "Max-Age=0" not in lines[0] and "max-age=0" not in lines[0], lines[0]
    assert f"Max-Age={panel_session.remember_cookie_age()}" in lines[0], lines[0]


def test_an_ordinary_sign_in_has_no_renewal_credential_and_a_session_cookie():
    name, _ = account("does-not-stay")
    with TestClient(make_app()) as client:
        answer = sign_in(client, name, remember=False)

    cookies = jar(client)
    assert panel_session.refresh_token_in(cookies) is None

    session_line = next(
        line for line in answer.headers.get_list("set-cookie")
        if line.startswith(f"{panel_session.SESSION_COOKIE}="))
    assert "Max-Age" not in session_line, session_line
    assert "expires" not in session_line.lower(), session_line

    refresh_lines = [line for line in answer.headers.get_list("set-cookie")
                     if line.startswith(f"{panel_session.refresh_cookie_name(False)}=")]
    for line in refresh_lines:
        assert "Max-Age=0" in line or "max-age=0" in line, line


# --- the hour that used to end everything ------------------------------------

def test_a_slept_tab_renews_from_the_credential_after_the_token_expired():
    name, _ = account("slept")
    with TestClient(make_app()) as client:
        answer = sign_in(client, name, remember=True)
        sid = sid_of(answer)
        csrf = csrf_of(answer)
        cookies = jar(client)
        cookies[panel_session.SESSION_COOKIE] = expired_token(name, sid)
        client.cookies.clear()

        renewed = refresh(client, cookies, csrf=csrf)

        assert renewed.status_code == 200, renewed.text
        data = renewed.json()
        assert data["success"] is True and data["remember"] is True
        payload = decode_token(data["access_token"])
        assert payload[panel_session.SESSION_CLAIM] == sid
        # The session is the same one a logout will revoke.
        assert renewed.cookies.get(panel_session.SESSION_COOKIE)
        assert renewed.cookies.get(panel_session.refresh_cookie_name(False))


def test_the_credential_without_the_csrf_pair_is_refused():
    name, _ = account("no-pair")
    with TestClient(make_app()) as client:
        answer = sign_in(client, name, remember=True)
        sid = sid_of(answer)
        cookies = jar(client)
        cookies[panel_session.SESSION_COOKIE] = expired_token(name, sid)
        client.cookies.clear()

        for csrf in (None, "wrong"):
            refused = refresh(client, cookies, csrf=csrf)
            assert refused.status_code == 403, refused.text
            assert "CSRF" in refused.text


def test_a_session_that_was_not_remembered_cannot_be_revived_by_a_cookie():
    name, _ = account("ordinary-expired")
    with TestClient(make_app()) as client:
        answer = sign_in(client, name, remember=False)
        sid = sid_of(answer)
        cookies = jar(client)
        cookies[panel_session.SESSION_COOKIE] = expired_token(name, sid)
        client.cookies.clear()

        refused = refresh(client, cookies, csrf=csrf_of(answer))
        assert refused.status_code == 401, refused.text


# --- how long the promise lasts ----------------------------------------------

def test_a_remembered_session_outlives_the_day_an_ordinary_one_is_bound_to():
    name, _ = account("long-window")
    with TestClient(make_app()) as client:
        answer = sign_in(client, name, remember=True)
        sid = sid_of(answer)
        # A live token of the same session, whose window began 25 hours ago.
        older = dependencies.create_access_token(
            {"sub": name, panel_session.SESSION_CLAIM: sid},
            expires_delta=timedelta(hours=1),
            session_started_at=clock.utcnow() - timedelta(hours=25),
            remember=True)

        renewed = refresh(client, {}, bearer=older)

        assert renewed.status_code == 200, renewed.text
        payload = decode_token(renewed.json()["access_token"])
        assert payload["rmb"] is True
        # The window started again: the same session now counts a month.
        assert payload["sst"] > int((clock.utcnow() - timedelta(hours=1)).timestamp())
        row = DatabaseManagerV2.execute_one(
            "SELECT expires_at FROM auth_session WHERE sid = :sid", {"sid": sid})
        expires_at = row["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(str(expires_at))
        assert expires_at > clock.utcnow() + timedelta(days=29)


def test_the_credential_ends_after_a_month_without_a_visit():
    name, _ = account("idle")
    with TestClient(make_app()) as client:
        answer = sign_in(client, name, remember=True)
        sid = sid_of(answer)
        csrf = csrf_of(answer)
        DatabaseManagerV2.execute_commit(
            "UPDATE auth_session SET expires_at = :past WHERE sid = :sid",
            {"past": clock.utcnow() - timedelta(days=1), "sid": sid})
        cookies = jar(client)
        cookies[panel_session.SESSION_COOKIE] = expired_token(name, sid)
        client.cookies.clear()

        refused = refresh(client, cookies, csrf=csrf)

        assert refused.status_code == 401, refused.text
        # The browser is told to stop carrying a credential nothing accepts.
        cleared = [line for line in refused.headers.get_list("set-cookie")
                   if line.startswith(f"{panel_session.refresh_cookie_name(False)}=")]
        assert cleared and ("Max-Age=0" in cleared[0] or "max-age=0" in cleared[0]), cleared


def test_signing_out_revokes_the_session_behind_an_expired_token():
    name, _ = account("sign-out")
    with TestClient(make_app()) as client:
        answer = sign_in(client, name, remember=True)
        sid = sid_of(answer)
        csrf = csrf_of(answer)
        cookies = jar(client)
        cookies[panel_session.SESSION_COOKIE] = expired_token(name, sid)
        client.cookies.clear()

        left = client.post("/api/auth/logout", headers={
            "Cookie": cookie_header(cookies),
            panel_session.CSRF_HEADER: csrf,
        })

        assert left.status_code == 200, left.text
        assert left.json()["revoked"] is True
        # The credential of the revoked session buys nothing any more.
        assert refresh(client, cookies, csrf=csrf).status_code == 401


def test_a_blocked_account_cannot_renew_even_with_a_remembered_session():
    name, uid = account("blocked")
    with TestClient(make_app()) as client:
        answer = sign_in(client, name, remember=True)
        sid = sid_of(answer)
        csrf = csrf_of(answer)
        dependencies.update_user(uid, status="blocked")
        cookies = jar(client)
        cookies[panel_session.SESSION_COOKIE] = expired_token(name, sid)
        client.cookies.clear()

        refused = refresh(client, cookies, csrf=csrf)
        assert refused.status_code == 403, refused.text
        assert "blocked" in refused.text.lower()


def test_the_remember_window_is_a_setting():
    from keepup_auth import session_lifetime

    now = clock.utcnow()
    assert session_lifetime.remember_window({}) == timedelta(days=30)
    assert session_lifetime.remember_window({"SESSION_REMEMBER_DAYS": "2"}) == \
        timedelta(days=2)
    assert session_lifetime.is_renewable(
        now - timedelta(hours=25), now=now, remember=True) is True
    assert session_lifetime.is_renewable(
        now - timedelta(days=3), now=now, environ={"SESSION_REMEMBER_DAYS": "2"},
        remember=True) is False


def test_the_second_factor_challenge_carries_the_checkbox():
    from keepup_auth import mfa

    assert mfa.challenge_remember(mfa.challenge_token("ada", remember=True)) is True
    assert mfa.challenge_remember(mfa.challenge_token("ada")) is False


# --- the screen the person actually uses --------------------------------------

@pytest.mark.parametrize("page", ["index_new.html", "index_nebula.html"])
def test_the_sign_in_screen_offers_the_checkbox(page):
    markup = (STATIC / page).read_text(encoding="utf-8")

    assert 'id="loginRemember"' in markup
    assert "Stay signed in" in markup


def test_the_shell_reads_the_checkbox_and_sends_it():
    source = SHELL.read_text(encoding="utf-8")

    assert "document.getElementById('loginRemember')" in source
    assert "JSON.stringify({ username, password, remember })" in source


def test_the_shell_renews_before_the_end_and_survives_a_sleep():
    source = SHELL.read_text(encoding="utf-8")

    assert "const SESSION_REFRESH_LEAD_SECONDS = 300;" in source
    assert "installSessionKeepAlive" in source
    assert "await refreshSession(false)" in source, "a load does not fall back to the credential"


# --- the script, run under node ----------------------------------------------

needs_node = pytest.mark.skipif(NODE is None, reason="needs node: the panel is JavaScript")

REGION_START = "const SESSION_REFRESH_LEAD_SECONDS"
REGION_END = "// --- end of the panel session renewal"


def session_region() -> str:
    text = SHELL.read_text(encoding="utf-8")
    assert REGION_START in text and REGION_END in text, "the renewal region is gone"
    return text[text.index(REGION_START):text.index(REGION_END)]


HARNESS = r"""
const SESSION_MARKER = 'cookie';
global.window = global;
global.document = {hidden: false, addEventListener() {}};
const calls = [];
const timers = [];
let marker = 'cookie';
let signInShown = false;
global.localStorage = {
    getItem: (name) => (name === 'authToken' ? marker : null),
    setItem: (name, value) => { if (name === 'authToken') marker = value; },
    removeItem: (name) => { if (name === 'authToken') marker = null; },
};
global.currentUser = null;
global.headerBadge = null;
global.updateUIAfterAuth = () => {};
global.showAuthModal = () => { signInShown = true; };
global.verifyToken = async () => true;
global.MfaPanel = { clearChallenge() {} };
global.setTimeout = (fn, ms) => { timers.push(ms); return timers.length; };
global.clearTimeout = () => {};
global.fetch = async (url, init) => {
    calls.push({url, method: init && init.method});
    if (process.env.ANSWER === '401') {
        return {ok: false, status: 401, json: async () => ({})};
    }
    if (process.env.ANSWER === '500') {
        return {ok: false, status: 500, json: async () => ({})};
    }
    return {ok: true, status: 200,
            json: async () => ({access_token: 'new', expires_in: 3600, remember: true})};
};
const vm = require('vm');
vm.runInThisContext(process.env.REGION, {filename: 'main_new.js'});
refreshSession().then((renewed) => {
    console.log(JSON.stringify({renewed, calls, timers, marker, signInShown}));
});
"""


def run_harness(answer: str):
    result = subprocess.run(
        [NODE, "-e", HARNESS], capture_output=True, text=True, timeout=30,
        env={**os.environ, "REGION": session_region(), "ANSWER": answer})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@needs_node
def test_the_script_asks_for_the_next_token_five_minutes_before_the_end():
    answered = run_harness("200")

    assert answered["renewed"] is True
    assert answered["calls"] == [{"url": "/api/auth/refresh", "method": "POST"}]
    # 3600 seconds less the five-minute lead, in milliseconds.
    assert answered["timers"] == [3_300_000], answered["timers"]
    assert answered["marker"] == "cookie"


@needs_node
def test_the_script_asks_for_a_sign_in_only_when_nothing_can_be_renewed():
    answered = run_harness("401")

    assert answered["renewed"] is False
    assert answered["marker"] is None
    assert answered["signInShown"] is True


@needs_node
def test_a_server_that_is_down_is_retried_not_treated_as_signing_out():
    answered = run_harness("500")

    assert answered["renewed"] is False
    assert answered["marker"] == "cookie"
    assert answered["signInShown"] is False
    assert answered["timers"] == [60_000], answered["timers"]
