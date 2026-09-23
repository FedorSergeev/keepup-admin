"""The panel shell as data of the package.

Task keepup-3. The theme pages, the panel's script and its stylesheets used to
sit in the repository's static/ next to the sections of three applications. An
installed keepup had nowhere to take them from: it could serve a panel only on
a deployment that happened to carry a copy of main_new.js.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/panel_shell_tests.py -v
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keepup import web
from keepup.factory import create_app
from keepup.settings import KeepupSettings

REPO = Path(__file__).resolve().parents[2]
SHELL = REPO / "keepup" / "static"

#: What the package has to carry in order to draw a panel at all.
SHELL_FILES = (
    "index_new.html",
    "index_nebula.html",
    "js/main_new.js",
    "js/tailwind.js",
    "js/feather-icons.js",
    "js/aos.js",
    "css/main_new.css",
    "css/main_nebula.css",
    "css/tailwind.css",
    "css/aos.css",
)


def bare_settings(**overrides):
    """An application that ships no front end of its own."""
    defaults = dict(title="Bare", project_name="bare", plugin_manager=None,
                    plugins_dir=None, static_mounts=())
    defaults.update(overrides)
    return KeepupSettings(**defaults)


# --- what the package carries -------------------------------------------------

@pytest.mark.parametrize("name", SHELL_FILES)
def test_the_package_carries_the_shell(name):
    assert (SHELL / name).is_file(), f"{name} is not in the package"


def test_the_shell_is_found_from_the_package_and_not_the_working_directory():
    """The process may have been started anywhere.

    Paths to code resolve from the package, paths to data from the working
    directory -- and the shell is code's data, not the deployment's.
    """
    assert Path(web.SHELL_DIR) == SHELL



# --- what it serves -----------------------------------------------------------

def test_an_application_with_no_front_end_still_gets_a_panel():
    """The point of the move, stated as a request.

    This application mounts nothing of its own -- and before keepup-3 it would
    have had no panel to show.
    """
    client = TestClient(create_app(bare_settings()))

    assert client.get("/keepup-static/js/main_new.js").status_code == 200
    assert client.get("/keepup-static/css/main_new.css").status_code == 200
    assert client.get("/selfcare").status_code == 200


def test_the_application_can_be_given_another_address_for_it():
    client = TestClient(create_app(bare_settings(shell_mount="/shell")))
    assert client.get("/shell/js/main_new.js").status_code == 200


# --- who wins when both have the page -----------------------------------------

def test_a_page_of_the_application_wins_over_one_of_the_package(tmp_path, monkeypatch):
    """A theme of its own must be able to replace one of the package's by name."""
    (tmp_path / "index_new.html").write_text("the application's own", encoding="utf-8")
    monkeypatch.setattr(web, "STATIC_DIR", str(tmp_path))

    assert web.static_page("index_new.html") == str(tmp_path / "index_new.html")


def test_a_page_only_the_package_has_is_found_there(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "STATIC_DIR", str(tmp_path))
    found = web.static_page("index_nebula.html")
    assert found is not None and found.startswith(str(SHELL))


def test_a_name_leading_out_of_either_directory_is_still_refused():
    """The containment check of keepup-10 now has two roots to hold."""
    for name in ("../config/auth.yaml", "/etc/passwd", "js/../../../etc/passwd"):
        assert web.static_page(name) is None, name


def test_a_name_in_neither_directory_is_not_invented():
    assert web.static_page("index_nowhere.html") is None


# --- the pages ask for the package ---------------------------------------------

@pytest.mark.parametrize("page", ["index_new.html", "index_nebula.html"])
def test_a_theme_page_asks_for_the_package_and_not_the_repository(page):
    """Its assets moved with it; a leftover /static/ reference is a blank panel."""
    text = (SHELL / page).read_text(encoding="utf-8")
    leftovers = re.findall(r'["\'](/static/(?:js|css)/[^"\']+)["\']', text)
    assert leftovers == [], f"{page} still asks the application for {leftovers}"


@pytest.mark.parametrize("asset", ["js/main_new.js", "css/main_new.css",
                                   "js/tailwind.js", "css/tailwind.css"])
def test_every_asset_a_theme_page_asks_for_exists_in_the_package(asset):
    assert (SHELL / asset).is_file()


# --- the build ------------------------------------------------------------------

