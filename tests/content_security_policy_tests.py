"""The panel is served with a Content-Security-Policy (keepup-93).

An inline handler is a script the page did not load, and the one escaping
mistake of keepup-62 was enough to turn one into a running script in the
session of the administrator who opened the section. The header is the general
answer: what the page did not load itself does not run, whatever got past the
escaping. It is written here as a request, and the policy itself is checked for
the directives that make it one.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/content_security_policy_tests.py -v
"""

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from keepup.factory import SecurityHeadersMiddleware, create_app
from keepup.security import DEFAULT_CONTENT_SECURITY_POLICY
from keepup.settings import KeepupSettings


def bare_settings(**overrides):
    """An application with a panel of the package's own and nothing else."""
    defaults = dict(title="Bare", project_name="bare", plugin_manager=None,
                    plugins_dir=None, static_mounts=())
    defaults.update(overrides)
    return KeepupSettings(**defaults)


# --- what the panel is served with ---------------------------------------------

def test_the_panel_carries_a_policy_by_default():
    answer = TestClient(create_app(bare_settings())).get("/api/health")
    assert answer.headers["content-security-policy"] == DEFAULT_CONTENT_SECURITY_POLICY


def test_the_policy_refuses_script_the_page_did_not_load():
    """The one directive the whole task exists for."""
    policy = bare_settings().content_security_policy
    assert "script-src 'self'" in policy
    assert "'unsafe-inline'" not in policy.split("script-src")[1].split(";")[0]
    assert "'unsafe-eval'" not in policy


def test_the_policy_holds_the_page_itself():
    """A page the framework serves is not framed, posted or scripted elsewhere."""
    policy = bare_settings().content_security_policy
    for directive in ("default-src 'self'", "base-uri 'self'", "object-src 'none'",
                      "frame-ancestors 'self'", "form-action 'self'",
                      "connect-src 'self'"):
        assert directive in policy


# --- what an application may say about it ---------------------------------------

def test_an_application_replaces_the_policy():
    """Its own sections bring their own sources -- a CDN, a report address."""
    policy = "default-src 'none'; script-src 'self' https://cdn.example.test"
    answer = TestClient(create_app(
        bare_settings(content_security_policy=policy))).get("/api/health")
    assert answer.headers["content-security-policy"] == policy


def test_an_application_sends_it_as_a_report_until_its_sections_are_ready():
    """Report-Only blocks nothing: the browser reports what it would have refused."""
    answer = TestClient(create_app(
        bare_settings(csp_report_only=True))).get("/api/health")
    assert answer.headers["content-security-policy-report-only"] == DEFAULT_CONTENT_SECURITY_POLICY
    assert "content-security-policy" not in answer.headers


def test_an_application_that_adds_its_own_header_keeps_it():
    app = FastAPI()

    @app.get("/own")
    def own():
        return PlainTextResponse("ok", headers={"Content-Security-Policy": "default-src 'none'"})

    app.add_middleware(SecurityHeadersMiddleware)
    answer = TestClient(app).get("/own")
    assert answer.headers["content-security-policy"] == "default-src 'none'"


def test_no_policy_is_sent_when_none_is_configured():
    """A deployment behind something that writes its own headers says so."""
    answer = TestClient(create_app(
        bare_settings(content_security_policy=None))).get("/api/health")
    assert "content-security-policy" not in answer.headers


def test_the_headers_can_be_switched_off_whole():
    answer = TestClient(create_app(
        bare_settings(security_headers=False))).get("/api/health")
    assert "content-security-policy" not in answer.headers
    assert "x-frame-options" not in answer.headers


# --- the page the policy is served with ------------------------------------------

def test_the_panel_page_is_revalidated_like_its_assets():
    """A page cached before the upgrade carries handlers the policy now refuses.

    GatedStaticFiles already tells the browser to revalidate the shell's assets;
    the page itself was left to the browser's invented freshness, which is 10%
    of the age of the file it cached.
    """
    answer = TestClient(create_app(bare_settings())).get("/selfcare")
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "no-cache"


# --- the middleware on its own ---------------------------------------------------

def test_the_middleware_sends_a_report_only_policy():
    app = FastAPI()

    @app.get("/plain")
    def plain():
        return PlainTextResponse("ok")

    app.add_middleware(SecurityHeadersMiddleware, csp="default-src 'none'",
                       csp_report_only=True)
    answer = TestClient(app).get("/plain")
    assert answer.headers["content-security-policy-report-only"] == "default-src 'none'"
    assert "content-security-policy" not in answer.headers
