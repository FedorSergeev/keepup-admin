"""The panel's cookies belong to the host that set them (keepup-92).

The session and CSRF cookies were named `ss_session` and `ss_csrf` whatever the
scheme, so a sibling subdomain -- or a hop over plain HTTP -- could plant a
cookie under the same name and the server took it: the CSRF value of keepup-72
was one consequence, and the session cookie another. Over HTTPS the names now
carry the `__Host-` prefix, which a browser accepts only from the exact host,
only with Secure, only with Path=/ and never with a Domain; the plain names are
still read for one release so an open panel survives the upgrade.

    python3 -m pytest keepup/tests/host_prefix_cookies_tests.py -v
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup.auth import dependencies, panel_session
from keepup.auth.routes import register_auth_routes
from keepup.db import DatabaseManagerV2
from keepup.schema import init_db

PASSWORD = "a-long-password-92"
HOST = "testserver"
PREFIXED_SESSION = f"{panel_session.HOST_PREFIX}{panel_session.SESSION_COOKIE}"
PREFIXED_CSRF = f"{panel_session.HOST_PREFIX}{panel_session.CSRF_COOKIE}"

SHELL = Path(__file__).resolve().parents[1] / "static" / "js" / "main_new.js"
CSRF_REGION_START = "const SESSION_MARKER"
CSRF_REGION_END = "installCsrfFetch(window);"
NODE = shutil.which("node")


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


@pytest.fixture(autouse=True)
def no_throttle():
    DatabaseManagerV2.execute_commit("DELETE FROM login_attempts")


def app():
    application = FastAPI()
    register_auth_routes(application, SimpleNamespace(plugins={}))
    application.add_middleware(panel_session.CsrfCookieRefresh)
    return application


def client(https: bool) -> TestClient:
    scheme = "https" if https else "http"
    return TestClient(app(), base_url=f"{scheme}://{HOST}")


def account(name: str) -> str:
    if not dependencies.get_user_by_username(name):
        uid = dependencies.save_user_to_db(name, PASSWORD)
        dependencies.update_user(uid, status="active")
    return name


def a_session_token(name: str) -> str:
    uid = dependencies.get_user_by_username(name)["id"]
    return dependencies.issue_session_token(uid, name)["access_token"]


def sign_in(name: str, https: bool = True):
    signing_in = client(https)
    answer = signing_in.post("/api/auth/login",
                             json={"username": name, "password": PASSWORD})
    assert answer.status_code == 200, answer.text
    return signing_in, answer


def cookie_lines(answer, name: str):
    return [line for line in answer.headers.get_list("set-cookie")
            if line.startswith(f"{name}=")]


# --- the names the browser is given ------------------------------------------

def test_over_https_the_cookies_carry_the_host_prefix():
    signing_in, answer = sign_in(account("prefix-https"))

    assert signing_in.cookies.get(PREFIXED_SESSION)
    assert signing_in.cookies.get(PREFIXED_CSRF)
    assert signing_in.cookies.get(panel_session.SESSION_COOKIE) is None
    assert signing_in.cookies.get(panel_session.CSRF_COOKIE) is None

    given = [line for line in answer.headers.get_list("set-cookie")
             if line.startswith(f"{PREFIXED_SESSION}=") or line.startswith(f"{PREFIXED_CSRF}=")]
    assert len(given) == 2, given
    for line in given:
        assert "Secure" in line and "Path=/" in line, line
        assert "Domain" not in line, line

    # The plain names are not left behind as a second copy: on HTTPS they are
    # exactly what a sibling subdomain may plant, so they are expired instead.
    for line in cookie_lines(answer, panel_session.SESSION_COOKIE) \
            + cookie_lines(answer, panel_session.CSRF_COOKIE):
        assert "Max-Age=0" in line or "max-age=0" in line, line


def test_over_plain_http_the_names_stay_as_they_were():
    """A `__Host-` cookie without Secure is one a browser refuses to keep."""
    signing_in, answer = sign_in(account("prefix-http"), https=False)

    assert signing_in.cookies.get(panel_session.SESSION_COOKIE)
    assert signing_in.cookies.get(panel_session.CSRF_COOKIE)
    assert signing_in.cookies.get(PREFIXED_SESSION) is None
    assert signing_in.cookies.get(PREFIXED_CSRF) is None

    lines = answer.headers.get_list("set-cookie")
    assert not [line for line in lines if line.startswith("__Host-")]
    assert not [line for line in lines if "Secure" in line]


# --- what the server still reads ---------------------------------------------

def with_cookies(pairs) -> dict:
    """A request header carrying exactly the cookies named, as a browser sends."""
    return {"cookie": "; ".join(f"{name}={value}" for name, value in pairs)}


def test_a_panel_signed_in_before_the_upgrade_keeps_working():
    """Its cookie has the plain name, and that name is read for one release."""
    name = account("prefix-old-panel")
    signing_in = client(https=True)

    answer = signing_in.get("/api/auth/me", headers=with_cookies(
        [(panel_session.SESSION_COOKIE, a_session_token(name))]))

    assert answer.status_code == 200


def test_the_prefixed_cookie_wins_over_a_planted_plain_one():
    """A sibling subdomain can set `ss_session`; it cannot set `__Host-ss_session`."""
    name = account("prefix-planted")
    signing_in = client(https=True)

    answer = signing_in.get("/api/auth/me", headers=with_cookies([
        (panel_session.SESSION_COOKIE, "planted-by-a-sibling"),
        (PREFIXED_SESSION, a_session_token(name)),
    ]))

    assert answer.status_code == 200


def test_a_planted_plain_cookie_does_not_stand_in_for_a_missing_session():
    signing_in = client(https=True)

    answer = signing_in.get("/api/auth/me", headers=with_cookies(
        [(panel_session.SESSION_COOKIE, "planted-by-a-sibling")]))

    assert answer.status_code == 401


# --- the value that moves to the new name ------------------------------------

def test_a_read_puts_the_csrf_value_under_the_prefixed_name():
    """What an open panel of the previous release holds: the plain names."""
    name = account("prefix-refresh")
    signing_in = client(https=True)

    answer = signing_in.get("/api/auth/me", headers=with_cookies([
        (panel_session.SESSION_COOKIE, a_session_token(name)),
        (panel_session.CSRF_COOKIE, "random-from-before"),
    ]))

    assert answer.status_code == 200
    assert cookie_lines(answer, PREFIXED_CSRF), answer.headers.get_list("set-cookie")
    assert signing_in.cookies.get(PREFIXED_CSRF)


def test_signing_out_clears_the_cookies_of_both_releases():
    signing_in, _ = sign_in(account("prefix-logout"))
    csrf = signing_in.cookies.get(PREFIXED_CSRF)
    token = signing_in.cookies.get(PREFIXED_SESSION)

    answer = signing_in.post("/api/auth/logout", headers={
        panel_session.CSRF_HEADER: csrf,
        **with_cookies([(PREFIXED_SESSION, token),
                        (panel_session.SESSION_COOKIE, "left-over")]),
    })

    assert answer.status_code == 200
    cleared = answer.headers.get_list("set-cookie")
    for name in (PREFIXED_SESSION, panel_session.SESSION_COOKIE,
                 PREFIXED_CSRF, panel_session.CSRF_COOKIE):
        lines = [line for line in cleared if line.startswith(f"{name}=")]
        assert lines, f"{name} is not cleared"
        assert "Max-Age=0" in lines[0] or "max-age=0" in lines[0], lines[0]


# --- the panel's own script ---------------------------------------------------

needs_node = pytest.mark.skipif(NODE is None, reason="needs node: the panel is JavaScript")

CSRF_HARNESS = r"""
const sent = [];
global.document = { cookie: process.env.COOKIES };
global.window = {
    location: {href: 'https://panel.test/selfcare', origin: 'https://panel.test'},
    fetch: (input, init) => {
        sent.push(init && init.headers ? init.headers.get('X-CSRF-Token') : null);
        return Promise.resolve({ok: true});
    },
};
const vm = require('vm');
vm.runInThisContext(process.env.REGION, {filename: 'main_new.js'});
window.fetch('/api/auth/refresh', {method: 'POST'}).then(() => {
    console.log(JSON.stringify(sent));
});
"""


def csrf_region() -> str:
    text = SHELL.read_text(encoding="utf-8")
    return text[text.index(CSRF_REGION_START):text.index(CSRF_REGION_END) + len(CSRF_REGION_END)]


def run_csrf_harness(cookies: str):
    result = subprocess.run([NODE, "-e", CSRF_HARNESS], capture_output=True, text=True,
                            timeout=30, env={**os.environ, "REGION": csrf_region(),
                                             "COOKIES": cookies})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@needs_node
def test_the_panel_script_sends_the_prefixed_value_when_both_are_there():
    assert run_csrf_harness("__Host-ss_csrf=prefixed; ss_csrf=plain") == ["prefixed"]


@needs_node
def test_the_panel_script_still_sends_the_plain_value_of_an_older_panel():
    assert run_csrf_harness("ss_csrf=plain") == ["plain"]
