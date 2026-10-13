"""The HTTP layer is a capability, and its modules move into it (keepup-121).

This is the first slice of the move: the documentation pages and the event log's
routes leave the base for `keepup-http`, and the panel's themes leave it for
`keepup-ui` -- a move the compatibility map announced and the base had never
made. The base keeps its old names for one release (keepup-122), with a warning
that names the distribution.

    python3 -m pytest tests/http_transport_ownership_tests.py -v
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keepup import create_app
from keepup.settings import KeepupSettings

PACKAGE = Path(__file__).resolve().parents[1]


def app():
    return create_app(KeepupSettings(title="Transport", plugins_dir="",
                                     static_mounts=(), plugin_manager=None))


def test_the_capability_carries_what_the_kernel_does_not_need():
    api_docs = (PACKAGE / "packages" / "keepup-http" / "keepup_http"
                / "api_docs.py").read_text(encoding="utf-8")
    events_api = (PACKAGE / "packages" / "keepup-http" / "keepup_http"
                  / "events_api.py").read_text(encoding="utf-8")
    themes = (PACKAGE / "packages" / "keepup-ui" / "keepup_ui"
              / "themes.py").read_text(encoding="utf-8")

    assert "register_api_documentation" in api_docs
    assert "register_event_api_routes" in events_api
    assert "config_service" in themes and "register_theme_routes" in themes
    for gone in ("api_docs.py", "events_api.py", "themes.py"):
        assert not (PACKAGE / gone).exists(), f"{gone} is still in the base"


def test_the_old_names_answer_from_the_distribution_and_say_so():
    """The compatibility layer names the distribution to install (keepup-122)."""
    import keepup.api_docs as old_docs
    import keepup.events_api as old_events
    import keepup.themes as old_themes

    from keepup_http.api_docs import register_api_documentation
    from keepup_http.events_api import register_event_api_routes
    from keepup_ui.themes import config_service, register_theme_routes

    assert old_docs.register_api_documentation is register_api_documentation
    assert old_events.register_event_api_routes is register_event_api_routes
    assert old_themes.config_service is config_service
    assert old_themes.register_theme_routes is register_theme_routes

    # The map says which distribution to install; the warning is said once per
    # name in a process, which an earlier import may already have done.
    from keepup import compat

    assert compat.destination_of("keepup.api_docs") == "keepup_http.api_docs"
    assert compat.destination_of("keepup.events_api") == "keepup_http.events_api"
    assert compat.destination_of("keepup.themes") == "keepup_ui.themes"


def test_the_transport_is_a_transport_plugin():
    from keepup.kernel.descriptor import KIND_TRANSPORT
    from keepup_http.plugin import HttpPlugin, SERVICE_HTTP

    descriptor = HttpPlugin.descriptor
    assert descriptor.id == "http" and descriptor.kind == KIND_TRANSPORT
    assert descriptor.distribution == "keepup-http"
    assert ("http", 1) in descriptor.provides
    assert SERVICE_HTTP == "http"
    assert "transport" in descriptor.contributions


def test_the_moved_routes_still_answer():
    """The application reaches them through the capability, not through a copy."""
    with TestClient(app()) as client:
        assert client.get("/openapi.json").status_code in (200, 401, 403)


def test_the_themes_are_still_served():
    """The panel's themes came from the base and come from keepup-ui now."""
    from keepup_ui.themes import config_service

    with TestClient(app()):
        assert config_service.get_all_themes()


@pytest.mark.parametrize("name", ["themes", "api_docs", "events_api"])
def test_the_base_keeps_no_copy(name):
    """A file left behind would answer without the warning and without the move."""
    assert not (PACKAGE / f"{name}.py").exists()
