"""What the framework leaves open when nobody has said otherwise.

Task keepup-13. Every default here used to point outward, and each was right
for the one deployment the framework grew up in and wrong for a package
somebody else installs. The shape of the fix is the same in all five: the
framework closes, and the application says what it needs open.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/perimeter_defaults_tests.py -v
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keepup.factory import create_app
from keepup.settings import KeepupSettings

REPO = Path(__file__).resolve().parents[2]


def bare_settings(**overrides):
    """An application with nothing of its own, which is the point."""
    defaults = dict(title="Bare", project_name="bare", plugin_manager=None,
                    plugins_dir=None, static_mounts=())
    defaults.update(overrides)
    return KeepupSettings(**defaults)


# --- who may read the answers -------------------------------------------------

def test_no_origin_is_allowed_unless_the_application_names_one():
    """"*" is a decision, not an absence of one.

    A package cannot know whose pages should be able to read the application it
    is installed into.
    """
    assert tuple(bare_settings().cors_origins) == ()


def test_the_application_names_its_origins():
    settings = bare_settings(cors_origins=("https://example.test",))
    app = create_app(settings)

    middleware = [m for m in app.user_middleware if "CORS" in m.cls.__name__]
    assert middleware, "the CORS middleware is gone"
    assert middleware[0].kwargs["allow_origins"] == ["https://example.test"]


# --- the metrics --------------------------------------------------------------

def test_the_metrics_are_closed_by_default():
    """The collection carries the load of the host and the names of the replicas.

    That is reconnaissance for whoever is choosing a moment, and it used to be
    served to anybody who asked.
    """
    assert bare_settings().metrics_public is False

    response = TestClient(create_app(bare_settings())).get("/metrics")
    assert response.status_code in (401, 403)


def test_a_deployment_can_open_the_metrics_deliberately():
    """A collector that cannot present credentials is a real case.

    Checked on the route rather than on an answer: conftest stubs
    prometheus_client out of sys.modules, so the body cannot be generated here
    -- but whether the route asks for credentials is exactly the claim.
    """
    app = create_app(bare_settings(metrics_public=True))
    [route] = [r for r in app.routes if getattr(r, "path", None) == "/metrics"]
    assert route.dependant.dependencies == [], "the open route still asks for credentials"

    closed = create_app(bare_settings())
    [route] = [r for r in closed.routes if getattr(r, "path", None) == "/metrics"]
    assert route.dependant.dependencies, "the closed route asks for nothing"


# --- the response headers -----------------------------------------------------

def test_the_panel_cannot_be_framed_by_anybody():
    """The panel is an ordinary page: without this header a click is stealable."""
    response = TestClient(create_app(bare_settings())).get("/api/health")
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("Referrer-Policy") == "same-origin"


def test_the_headers_can_be_switched_off():
    """An application behind something that adds its own must be able to."""
    response = TestClient(create_app(bare_settings(security_headers=False))).get("/api/health")
    assert "X-Frame-Options" not in response.headers


def test_strict_transport_is_only_sent_over_tls(monkeypatch):
    """Over plain HTTP it is ignored, and a local network without TLS stays reachable."""
    monkeypatch.setenv("SSL_ENABLED", "false")
    response = TestClient(create_app(bare_settings())).get("/api/health")
    assert "Strict-Transport-Security" not in response.headers

    monkeypatch.setenv("SSL_ENABLED", "true")
    response = TestClient(create_app(bare_settings())).get("/api/health")
    assert "Strict-Transport-Security" in response.headers


# --- the log ------------------------------------------------------------------

def test_the_log_goes_where_the_application_said(tmp_path, monkeypatch):
    """LOG_DIR was declared and then used nowhere.

    The log went to /tmp under a predictable name, which on a host with more
    than one user is the whole log of the application, readable by all of them.
    """
    import logging

    from keepup import logging_setup

    # The root handlers are put back by hand: setup_logging() closes every one
    # it finds, and the one pytest captures output with is among them. See the
    # same dance in runtime_tests.py, where skipping it made unrelated tests
    # fail three files later.
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    try:
        monkeypatch.setattr(logging_setup, "LOG_DIR", str(tmp_path / "logs"))
        logging_setup.setup_logging()

        written = list((tmp_path / "logs").glob("app_*.log"))
        assert written, "nothing was written to the configured directory"
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
        for handler in saved:
            root.addHandler(handler)
        root.setLevel(level)


def test_the_source_names_no_temporary_directory():
    source = (REPO / "keepup/logging_setup.py").read_text(encoding="utf-8")
    assert "/tmp/app_" not in source


# --- the debug mode -----------------------------------------------------------

def test_the_debug_middleware_writes_no_header_values():
    """One environment variable used to turn a replica into a recorder.

    DISABLE_HTTP_SERVER logged every header in full -- Authorization and Cookie
    among them -- then the same again as text and as hex, and the remote
    logger shipped the lot to a collector.
    """
    source = (REPO / "keepup/factory.py").read_text(encoding="utf-8")

    assert "Raw request hex" not in source
    assert "Body hex" not in source
    assert 'logger.info(f"  {key}: {value}")' not in source
    assert "Header names" in source
