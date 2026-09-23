"""The checks that were written in docstrings and not in the code.

Task keepup-10. An audit before publishing the framework found three doors that
answered anybody who knocked, and none of them looked wrong in normal use --
which is why each one is pinned here by the request that used to get through.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/route_authorisation_tests.py -v
"""

import ast
import os
import re
from pathlib import Path

import pytest

from keepup.tests.repository import some_application
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup import themes, web

REPO = Path(__file__).resolve().parents[2]


class StubThemeService:
    """A theme catalogue that answers from memory, with no database behind it."""

    def __init__(self, page_file="index_new.html"):
        self.page_file = page_file
        self.created = []

    def get_active_theme_page_file(self):
        return self.page_file

    def get_all_themes(self):
        return []

    def get_active_theme(self):
        return None

    def create_theme(self, *args, **kwargs):
        self.created.append((args, kwargs))
        return 1

    def set_active_theme(self, theme_id):
        return True

    def delete_theme(self, theme_id):
        return True

    def get_active_theme_brand(self):
        return {"brand_name": "Stub", "logo_url": None}


@pytest.fixture
def theme_app(monkeypatch):
    """An application with the theme routes and the pages, and no database."""
    stub = StubThemeService()
    monkeypatch.setattr(themes, "config_service", stub)
    monkeypatch.setattr(web, "config_service", stub)

    app = FastAPI()
    themes.register_theme_routes(app, stub)
    web.register_web_routes(app)
    return app, stub


# --- the theme routes ---------------------------------------------------------

@pytest.mark.parametrize("method, path", [
    ("get", "/themes"),
    ("post", "/themes/1/activate"),
    ("post", "/themes/create?theme_name=x&main_page_file=index_new.html"),
    ("delete", "/themes/1"),
])
def test_a_theme_route_refuses_a_request_with_no_credentials(theme_app, method, path):
    """Each of these used to answer 200 to anybody on the network.

    They decide what every visitor of the panel is served, so they are
    administrative -- which until now was stated only in their docstrings.
    """
    app, _ = theme_app
    response = getattr(TestClient(app), method)(path)
    assert response.status_code in (401, 403), (
        f"{method.upper()} {path} answered {response.status_code} without credentials")


def test_the_branding_stays_open(theme_app):
    """The logo is on the sign-in screen, before there is anybody to authorise."""
    app, _ = theme_app
    response = TestClient(app).get("/api/theme/brand")
    assert response.status_code == 200


# --- the page a theme names ---------------------------------------------------

@pytest.mark.parametrize("name", [
    "../config/auth.yaml",
    "../../etc/passwd",
    "subdir/../../config/postgres.properties",
    "/etc/passwd",
])
def test_a_page_outside_the_front_end_directory_is_refused(name):
    """Resolution first, then containment: `..`, an absolute name and a link alike."""
    assert web.static_page(name) is None, f"{name!r} was accepted"


@pytest.mark.parametrize("name", ["index_new.html", "index_nebula.html"])
def test_a_page_inside_the_front_end_directory_is_served(name):
    assert web.static_page(name) is not None


def test_a_theme_naming_a_page_outside_serves_the_default_instead(theme_app, caplog):
    """A row already in the database is the case authorisation cannot cover.

    The check has to stand where the file is served, not only where the name is
    written, because the name may have been written before the check existed.
    """
    app, stub = theme_app
    stub.page_file = "../config/auth.yaml"

    with caplog.at_level("ERROR"):
        served = web.theme_page()

    assert served.endswith(web.DEFAULT_PANEL_PAGE)
    assert any("outside the front ends" in r.getMessage() for r in caplog.records)


def test_selfcare_does_not_serve_a_file_outside_the_front_end(theme_app):
    app, stub = theme_app
    stub.page_file = "../config/auth.yaml"

    response = TestClient(app).get("/selfcare")
    assert "provider" not in response.text, "the contents of config/auth.yaml were served"


# --- the dependency that determines the user ----------------------------------

def test_the_user_dependency_takes_nothing_from_the_query():
    """A scalar parameter with a default on a dependency is a query parameter.

    `ignore_empty_user` stood here and was published on every route of the
    framework and of every plugin -- as a switch a caller could set.
    """
    from keepup.auth import dependencies

    signature = ast.parse(
        (REPO / "keepup/auth/dependencies.py").read_text(encoding="utf-8"))
    found = [node for node in ast.walk(signature)
             if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_current_user"]
    assert found, "get_current_user is gone"

    scalars = []
    for argument, default in zip(reversed(found[0].args.args),
                                 reversed(found[0].args.defaults)):
        if isinstance(default, ast.Constant):
            scalars.append(argument.arg)
    assert scalars == [], (
        "these parameters of get_current_user are query parameters: " + ", ".join(scalars))

    assert hasattr(dependencies, "get_optional_user"), (
        "the caller that wants None instead of a refusal needs a function of its own")


def test_nothing_passes_the_old_switch_any_more():
    """The one caller in the application moved to get_optional_user."""
    offenders = []
    tests_of_the_package = REPO / "keepup" / "tests"
    trees = [REPO / "keepup"] + some_application()
    for path in [p for tree in trees for p in tree.rglob("*.py")]:
        # The package's own tests name this parameter for a reason -- they
        # check that it is gone (keepup-6).
        if tests_of_the_package in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if "ignore_empty_user" in line and "_get_local_user" not in line \
                    and "def _get_local_user" not in line:
                if "get_current_user" in line or "get_optional_user(" in line:
                    offenders.append(f"{path.relative_to(REPO)}:{number}")
    assert offenders == [], "\n".join(offenders)


# --- the cross-plugin call mechanism ------------------------------------------

def test_no_route_calls_a_plugin_handler_by_name():
    """get_handlers() is how plugins call each other, not an endpoint.

    A handler is written for a caller that has already decided who may do this,
    so many of them check nothing themselves.
    """
    source = (REPO / "keepup/plugins/registry.py").read_text(encoding="utf-8")
    assert not re.search(r'@app\.\w+\(\s*["\']\/api\/plugins\/\{plugin_id\}\/\{handler_name\}',
                         source), "the handler route is back"
    assert "call_plugin_handler" not in source
