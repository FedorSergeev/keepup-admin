"""The theme an application declares, and how it reaches the database.

Themes had no tests at all, and a defect lived on that unnoticed: the theme
service created them when its own module was imported -- that is, before there
was an application -- and a declaration made after the import was only
remembered in the process. An empty database got one nameless theme, and the
declared `nebula` with its brand never appeared at all.

Run by path, like the other `*_tests.py` files:

    python3 -m pytest keepup/tests/themes_tests.py -v
"""

import subprocess
import sys
from pathlib import Path

import pytest

from keepup.db import DatabaseManagerV2
from keepup.themes import THEMES_TABLE, ConfigService

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parent

#: What an application declares: name, page, brand, logo.
NEBULA = ("nebula", "index_nebula.html", "example.test", None)


@pytest.fixture
def empty_themes():
    """An empty themes table: every test starts from a database with none."""
    DatabaseManagerV2.execute_commit(f"DROP TABLE IF EXISTS {THEMES_TABLE}")
    yield
    DatabaseManagerV2.execute_commit(f"DROP TABLE IF EXISTS {THEMES_TABLE}")


def service(themes=()):
    """The theme service with an application's declaration, brought up as
    building an application brings it up."""
    fresh = ConfigService()
    fresh.register_built_in_themes(themes)
    fresh.initialize()
    return fresh


def names_of(config_service):
    return {theme["theme_name"] for theme in config_service.get_all_themes()}


def active_of(config_service):
    active = config_service.get_active_theme()
    return active["theme_name"] if active else None


# --- what caused the defect ------------------------------------------------


def test_importing_the_module_does_not_touch_the_database():
    """Importing must not create themes.

    This is precisely why a declaration never arrived: the bootstrap ran on
    import, when there is no application yet, and nothing can be declared
    before it by construction.
    """
    code = (
        "import keepup.themes as t;"
        "print('INITIALIZED:' + str(t.config_service._initialized))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    [reported] = [line for line in result.stdout.splitlines()
                  if line.startswith("INITIALIZED:")]
    assert reported == "INITIALIZED:False", reported


def test_a_theme_declared_by_the_application_reaches_the_database(empty_themes):
    """The defect itself: a declared theme never reached the database."""
    config_service = service((NEBULA,))
    assert "nebula" in names_of(config_service)


def test_the_declared_theme_keeps_its_brand(empty_themes):
    """The brand is what a theme is declared for."""
    config_service = service((NEBULA,))

    declared = [theme for theme in config_service.get_all_themes()
                if theme["theme_name"] == "nebula"]
    assert declared and declared[0]["brand_name"] == "example.test"


def test_declaring_after_the_service_is_up_still_reaches_the_database(empty_themes):
    """The order of the calls decides nothing any more."""
    config_service = ConfigService()
    config_service.initialize()
    assert "nebula" not in names_of(config_service)

    config_service.register_built_in_themes((NEBULA,))

    assert "nebula" in names_of(config_service)


# --- a fresh database ------------------------------------------------------


def test_a_fresh_database_wears_the_face_of_the_application(empty_themes):
    """The only one who knows the panel's name at that moment is the application."""
    config_service = service((NEBULA,))

    assert active_of(config_service) == "nebula"
    assert config_service.get_active_theme_brand()["brand_name"] == "example.test"


def test_the_first_declared_theme_is_the_one_that_becomes_active(empty_themes):
    second = ("second", "index_new.html", "a second brand", None)
    config_service = service((NEBULA, second))

    assert active_of(config_service) == "nebula"
    assert names_of(config_service) == {"nebula", "second"}


def test_an_application_without_themes_still_gets_a_default(empty_themes):
    """The application declared none -- the panel still has to open."""
    config_service = service(())

    assert names_of(config_service) == {"default"}
    assert active_of(config_service) == "default"
    assert config_service.get_active_theme_page_file() == "index_new.html"


def test_the_nameless_default_is_not_added_beside_a_declared_theme(empty_themes):
    """Otherwise a fresh deployment greets a person with a name from the build."""
    config_service = service((NEBULA,))

    assert "default" not in names_of(config_service)


# --- a database that has already been running ------------------------------


def test_a_restart_keeps_the_theme_an_administrator_chose(empty_themes):
    """A restart has no right to undo a person's choice."""
    config_service = service((NEBULA,))
    chosen = [theme for theme in config_service.get_all_themes()
              if theme["theme_name"] == "nebula"][0]

    other = config_service.create_theme("chosen by hand", "index_new.html",
                                        is_active=True, brand_name="the administrator's choice")
    assert active_of(config_service) == "chosen by hand"

    # A second start of the same application against the same database.
    restarted = service((NEBULA,))

    assert active_of(restarted) == "chosen by hand"
    assert other is not None
    still = [theme for theme in restarted.get_all_themes()
             if theme["theme_name"] == "nebula"][0]
    assert still["id"] == chosen["id"], "the theme was recreated and should have stayed"


def test_a_restart_does_not_overwrite_the_brand_of_an_existing_theme(empty_themes):
    """Creation is idempotent: an existing row is not rewritten."""
    service((NEBULA,))
    DatabaseManagerV2.execute_commit(
        f"UPDATE {THEMES_TABLE} SET brand_name = :brand WHERE theme_name = :name",
        {"brand": "corrected by the administrator", "name": "nebula"},
    )

    restarted = service((NEBULA,))

    kept = [theme for theme in restarted.get_all_themes()
            if theme["theme_name"] == "nebula"][0]
    assert kept["brand_name"] == "corrected by the administrator"


def test_a_theme_declared_later_is_added_but_does_not_steal_the_active_flag(empty_themes):
    """A new theme in the declaration must not switch the panel to itself."""
    config_service = service((NEBULA,))
    added = ("added later", "index_new.html", "a new brand", None)

    config_service.register_built_in_themes((NEBULA, added))

    assert "added later" in names_of(config_service)
    assert active_of(config_service) == "nebula"


# --- the service brings itself up -------------------------------------------


def test_the_service_raises_itself_on_first_use(empty_themes):
    """Whoever arrives before the application is built gets a working service."""
    config_service = ConfigService()
    assert config_service._initialized is False

    page = config_service.get_active_theme_page_file()

    assert page == "index_new.html"
    assert config_service._initialized is True


def test_initialisation_is_idempotent(empty_themes):
    config_service = service((NEBULA,))
    before = names_of(config_service)

    config_service.initialize()

    assert names_of(config_service) == before
