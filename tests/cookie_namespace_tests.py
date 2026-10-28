"""Several keepup panels on one host keep their sessions apart (0.7.0).

A cookie belongs to a host, not to a port. Two applications opened as
`localhost:8000` and `localhost:8010` therefore wrote the same `ss_session` and
`ss_csrf`, and whichever signed in last left the other one holding a token it
could not use: the first panel was signed out by using the second. Since 0.7.0
each application's cookies carry its own name (`ss_session_artgallery`), taken
from the deployment's `project_name` and overridable with `cookie_prefix`.

    python3 -m pytest keepup/tests/cookie_namespace_tests.py -v
"""

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keepup.settings import KeepupSettings
from keepup_auth import dependencies, oidc, panel_session
from keepup_http.factory import create_app

PASSWORD = "a-long-password-for-cookies"
NODE = shutil.which("node")
SHELL = (Path(__file__).resolve().parents[1] / "packages" / "keepup-http"
         / "keepup_http" / "static" / "js" / "main_new.js")
CSRF_REGION_START = "const SESSION_MARKER"
CSRF_REGION_END = "installCsrfFetch(window);"


def make_app(project_name="cookie-test", cookie_prefix=None):
    """One application, built the way a deployment builds it."""
    settings = KeepupSettings(
        title="Cookie namespace",
        project_name=project_name,
        cookie_prefix=cookie_prefix,
        plugin_manager=None,
        static_mounts=(),
    )
    return create_app(settings)


def account(name):
    """An active local account, since the seeded administrator's password is random."""
    name = f"{name}-{uuid.uuid4().hex[:8]}"
    uid = dependencies.save_user_to_db(name, PASSWORD)
    dependencies.update_user(uid, status="active")
    return name


def sign_in(client, name):
    answer = client.post("/api/auth/login", json={"username": name, "password": PASSWORD})
    assert answer.status_code == 200, answer.text
    return answer


def session_token(answer, project_name):
    name = f"ss_session_{project_name}"
    assert name in answer.cookies, answer.cookies
    return answer.cookies[name]


# --- the name an application gives its cookies --------------------------------

def test_the_project_name_is_the_cookie_namespace():
    with TestClient(make_app("artgallery-ns")) as client:
        config = client.get("/api/public/config").json()

    assert config["panel_cookies"] == {
        "session": "ss_session_artgallery-ns",
        "csrf": "ss_csrf_artgallery-ns",
    }


def test_signing_in_writes_the_namespaced_cookies():
    name = account("namespaced-login")
    with TestClient(make_app("artgallery-ns")) as client:
        answer = sign_in(client, name)

    assert answer.cookies.get("ss_session_artgallery-ns")
    assert answer.cookies.get("ss_csrf_artgallery-ns")
    assert answer.cookies.get(panel_session.SESSION_COOKIE) is None
    assert answer.cookies.get(panel_session.CSRF_COOKIE) is None


def test_an_explicit_prefix_wins_over_the_project_name():
    with TestClient(make_app("same-project", cookie_prefix="first-on-the-host")) as client:
        config = client.get("/api/public/config").json()

    assert config["panel_cookies"]["session"] == "ss_session_first-on-the-host"


def test_an_empty_prefix_keeps_the_names_of_060():
    """The one-application-per-host deployment says so and is not signed out."""
    with TestClient(make_app("legacy", cookie_prefix="")) as client:
        config = client.get("/api/public/config").json()

    assert config["panel_cookies"] == {"session": "ss_session", "csrf": "ss_csrf"}


# --- what one browser does with two applications ------------------------------

def test_two_applications_on_one_host_keep_their_sessions_apart():
    """One cookie jar, two names, and each application reads only its own."""
    name = account("two-panels")

    with TestClient(make_app("ns-alpha")) as alpha:
        alpha_answer = sign_in(alpha, name)
    alpha_token = session_token(alpha_answer, "ns-alpha")

    with TestClient(make_app("ns-beta")) as beta:
        beta_answer = sign_in(beta, name)
        beta_token = session_token(beta_answer, "ns-beta")

        both = (f"ss_session_ns-alpha={alpha_token}; "
                f"ss_session_ns-beta={beta_token}")
        beta.cookies.clear()

        # The browser holds both; beta finds its own and stays signed in.
        assert beta.get("/api/auth/me", headers={"Cookie": both}).status_code == 200
        # Alpha's cookie alone is not a session in beta.
        assert beta.get("/api/auth/me", headers={
            "Cookie": f"ss_session_ns-alpha={alpha_token}"}).status_code == 401

    # And the other way round, once the first application's name is in force
    # again -- what its own process would have all along.
    panel_session.configure("ns-alpha")
    with TestClient(make_app("ns-alpha")) as alpha:
        both = (f"ss_session_ns-alpha={alpha_token}; "
                f"ss_session_ns-beta={beta_token}")
        alpha.cookies.clear()
        assert alpha.get("/api/auth/me", headers={"Cookie": both}).status_code == 200
        assert alpha.get("/api/auth/me", headers={
            "Cookie": f"ss_session_ns-beta={beta_token}"}).status_code == 401


def test_the_shared_name_of_060_is_not_read_by_a_namespaced_application():
    """Reading it is the collision: it belongs to whichever panel wrote it last."""
    name = account("legacy-name")
    with TestClient(make_app("ns-gamma")) as client:
        answer = sign_in(client, name)
        token = session_token(answer, "ns-gamma")
        client.cookies.clear()

        assert client.get("/api/auth/me", headers={
            "Cookie": f"{panel_session.SESSION_COOKIE}={token}"}).status_code == 401


def test_a_namespaced_sign_in_leaves_the_shared_names_alone():
    """They may still be another panel's -- an upgrade here must not log it out."""
    name = account("cleanup")
    with TestClient(make_app("ns-cleanup")) as client:
        answer = sign_in(client, name)

    for legacy in (panel_session.SESSION_COOKIE, panel_session.CSRF_COOKIE):
        lines = [line for line in answer.headers.get_list("set-cookie")
                 if line.startswith(f"{legacy}=")]
        assert not lines, f"a namespaced sign-in touched {legacy}: {lines}"


# --- the name is made from whatever the application calls itself --------------

def test_a_name_a_browser_would_refuse_is_made_safe_without_merging_two_names():
    panel_session.configure("My App")
    spaced = panel_session.cookie_prefix()
    assert re.fullmatch(r"[A-Za-z0-9_-]+", spaced)
    assert spaced != "My-App"

    panel_session.configure("My-App")
    hyphened = panel_session.cookie_prefix()
    assert hyphened == "My-App"
    assert spaced != hyphened


def test_a_name_too_long_for_a_cookie_keeps_its_tail_in_the_digest():
    panel_session.configure("a" * 40)
    first = panel_session.cookie_prefix()
    panel_session.configure("a" * 41)
    second = panel_session.cookie_prefix()

    assert first != second
    assert len(first) <= 42 and len(second) <= 42


def test_the_oidc_attempt_cookie_is_namespaced_too():
    panel_session.configure("oidc-app")
    assert oidc.flow_cookie_name() == "ss_oidc_oidc-app"


# --- the panel's script reads the name the server publishes -------------------

needs_node = pytest.mark.skipif(NODE is None, reason="needs node: the panel is JavaScript")

CSRF_HARNESS = r"""
const sent = [];
global.document = { cookie: process.env.COOKIES };
global.loadPublicConfig = () => Promise.resolve(
    process.env.CONFIG ? JSON.parse(process.env.CONFIG) : null);
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
    return text[text.index(CSRF_REGION_START):
                text.index(CSRF_REGION_END) + len(CSRF_REGION_END)]


def run_csrf_harness(cookies: str, config=None):
    env = {**os.environ, "REGION": csrf_region(), "COOKIES": cookies}
    if config is not None:
        env["CONFIG"] = json.dumps(config)
    result = subprocess.run([NODE, "-e", CSRF_HARNESS], capture_output=True, text=True,
                            timeout=30, env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@needs_node
def test_the_panel_script_reads_the_cookie_the_server_named():
    config = {"panel_cookies": {"session": "ss_session_artgallery", "csrf": "ss_csrf_artgallery"}}
    cookies = ("ss_csrf_artgallery=mine; __Host-ss_csrf_artgallery=preferred; "
               "ss_csrf_another-app=not-mine")

    assert run_csrf_harness(cookies, config) == ["preferred"]


@needs_node
def test_the_panel_script_does_not_send_another_applications_value():
    config = {"panel_cookies": {"session": "ss_session_artgallery", "csrf": "ss_csrf_artgallery"}}

    assert run_csrf_harness("ss_csrf_another-app=not-mine", config) == [None]


@needs_node
def test_without_the_config_the_panel_script_falls_back_to_the_old_names():
    assert run_csrf_harness("__Host-ss_csrf=prefixed; ss_csrf=plain") == ["prefixed"]
