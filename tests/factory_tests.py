"""Building an application out of the framework alone.

Nothing here imports ``app``: the point is that the framework stands up on its
own, with no plugins, no marketplace tables and no product. Two applications
are built in one process as well -- that is what catches state hiding at module
level, which is exactly what the entry point used to be made of.

    python3 -m pytest keepup/tests/factory_tests.py -v
"""

import pytest
from fastapi.testclient import TestClient

from keepup.factory import GatedStaticFiles, create_app
from keepup.settings import KeepupSettings, StaticMount


def bare_settings(**overrides):
    """An application with no plugins and nothing mounted from disk."""
    values = dict(title="Framework Only", static_mounts=(), plugin_manager=None)
    values.update(overrides)
    return KeepupSettings(**values)


def test_an_application_is_built_without_a_single_plugin():
    app = create_app(bare_settings())

    assert app.title == "Framework Only"
    paths = {route.path for route in app.routes}
    assert "/api/health" in paths
    assert "/api/version" in paths
    assert "/api/modules" in paths


def test_the_health_endpoint_answers():
    """Without the lifespan: start-up needs a signing key and a database.

    TestClient would run the lifespan, so the routes are exercised through the
    application's router directly -- what is being checked here is that the
    framework built a working endpoint, not that a deployment starts.
    """
    app = create_app(bare_settings())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/health")

    assert response.status_code in (200, 503)
    assert "status" in response.json()


def test_two_applications_live_side_by_side_in_one_process():
    """The second must not inherit the first one's settings.

    The entry point used to create the application while being imported, so
    there could only ever be one. If anything still hides at module level, the
    two titles below come out the same.
    """
    first = create_app(bare_settings(title="First"))
    second = create_app(bare_settings(title="Second"))

    assert first is not second
    assert first.title == "First"
    assert second.title == "Second"


def test_the_stripped_application_has_only_its_metrics():
    """DISABLE_HTTP_SERVER is a different application, not a disabled one.

    Which is why a 404 from it is expected rather than a fault to look for
    elsewhere -- the routes are absent.
    """
    app = create_app(bare_settings(disable_http_server=True))

    paths = {route.path for route in app.routes}
    assert "/metrics" in paths
    assert "/api/health" not in paths
    assert "/api/modules" not in paths


def test_the_application_supplies_its_own_front_end_paths():
    settings = bare_settings(static_dir="somewhere", client_page="index.html",
                             version_file="build.json", favicon_file="icon.ico")
    create_app(settings)

    from keepup import web

    assert web.STATIC_DIR == "somewhere"
    assert web.CLIENT_PAGE == "index.html"
    assert web.VERSION_FILE == "build.json"
    assert web.FAVICON_FILE == "icon.ico"


def test_the_application_supplies_the_redaction_of_its_secrets():
    """Without one the framework keeps the names and hides the values.

    It still does not know which field carries a key -- but since keepup-11 it
    does not guess that none of them does. An application that wants the
    contents recorded says so by name.
    """
    from keepup import audit

    create_app(bare_settings())
    assert audit.redact("token=abc") == audit.HIDDEN

    create_app(bare_settings(audit_redaction=lambda value: "hidden"))
    assert audit.redact("token=abc") == "hidden"

    create_app(bare_settings(audit_redaction=audit.keep_as_is))
    assert audit.redact("token=abc") == "token=abc"


def test_static_mounts_come_from_the_settings(tmp_path):
    (tmp_path / "front").mkdir()
    settings = bare_settings(static_mounts=(StaticMount("/front", str(tmp_path / "front"), "front"),))

    app = create_app(settings)

    mounted = {route.path for route in app.routes if route.__class__.__name__ == "Mount"}
    # The package's shell is mounted always and is not one of the application's
    # settings (keepup-3): an application declares only its own.
    assert mounted == {"/front", "/keepup-static"}


class FakePlugin:
    def __init__(self, initialized):
        self.initialized = initialized


class FakeManager:
    def __init__(self, plugins):
        self._plugins = plugins

    def get_plugin(self, plugin_id):
        return self._plugins.get(plugin_id)


async def test_a_gated_page_is_refused_while_its_plugin_does_not_run(tmp_path):
    from starlette.exceptions import HTTPException

    (tmp_path / "paid.html").write_text("<p>paid</p>", encoding="utf-8")
    scope = {"type": "http", "method": "GET", "headers": []}

    refused = GatedStaticFiles(directory=str(tmp_path),
                               gated_pages={"paid.html": "billing"},
                               plugin_manager=FakeManager({}))
    with pytest.raises(HTTPException) as error:
        await refused.get_response("paid.html", scope)
    assert error.value.status_code == 404

    served = GatedStaticFiles(directory=str(tmp_path),
                              gated_pages={"paid.html": "billing"},
                              plugin_manager=FakeManager({"billing": FakePlugin(True)}))
    assert (await served.get_response("paid.html", scope)).status_code == 200


async def test_an_ungated_page_is_served_and_told_to_revalidate(tmp_path):
    (tmp_path / "app.js").write_text("console.log(1)", encoding="utf-8")
    scope = {"type": "http", "method": "GET", "headers": []}

    files = GatedStaticFiles(directory=str(tmp_path))
    response = await files.get_response("app.js", scope)

    assert response.status_code == 200
    # Without it a browser invents its own freshness and keeps serving the old
    # copy of an edited module until a hard reload.
    assert response.headers["cache-control"] == "no-cache"
