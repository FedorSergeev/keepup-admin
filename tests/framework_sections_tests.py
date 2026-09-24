"""The panel sections the framework ships, and how they reach the catalogue.

Task keepup-4. Eight sections -- users, the section catalogue itself, the
cluster, metrics, themes, events, background tasks and the integration log --
belong to the framework: they are the administrator's own tools and they stand
on nothing an application provides. An application that had to copy eight
entries into its own file would get them wrong one at a time.

One rule here differs from the ordinary catalogue sync, and it is what most of
this file checks:

    the framework owns *where the files of its sections are*;
    the administrator owns *whether they are shown and to whom*.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/framework_sections_tests.py -v
"""

import json
from pathlib import Path

import pytest

from keepup import modules
from keepup.db import DatabaseManagerV2
from keepup.schema import init_db

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parent
CATALOGUE = PACKAGE / "sections.json"

#: What the framework ships. Named here so that adding a section to the package
#: without thinking about this file is not possible.
FRAMEWORK_SECTIONS = {
    "users", "modules_management", "cluster", "metrics",
    "themes", "event_manager", "background_tasks", "integration_logs",
}


@pytest.fixture(scope="module", autouse=True)
def tables():
    init_db()


def declared():
    return json.loads(CATALOGUE.read_text(encoding="utf-8"))


def row(module_id):
    return DatabaseManagerV2.execute_one(
        "SELECT module_id, name, js_path, css_path, init_function "
        "FROM frontend_modules WHERE module_id = :id", {"id": module_id})


# --- what the package carries -------------------------------------------------

def test_the_package_declares_its_sections():
    assert {m["id"] for m in declared()["modules"]} == FRAMEWORK_SECTIONS


@pytest.mark.parametrize("section", sorted(FRAMEWORK_SECTIONS))
def test_every_declared_section_has_its_files_in_the_package(section):
    """A declaration pointing at a file nobody ships is a blank panel section."""
    [entry] = [m for m in declared()["modules"] if m["id"] == section]
    for url in (entry["js"], entry["css"]):
        assert url.startswith("/keepup-static/"), url
        assert (PACKAGE / "static" / url[len("/keepup-static/"):]).is_file(), url




# --- how they reach the catalogue ---------------------------------------------

def test_a_fresh_database_gets_them_without_the_application_asking():
    for section in FRAMEWORK_SECTIONS:
        present = row(section)
        assert present is not None, f"{section} is not in the catalogue"
        assert present["js_path"].startswith("/keepup-static/"), present["js_path"]


def test_the_administrator_is_granted_them():
    """A panel where the administrator cannot see the users section is not one."""
    granted = DatabaseManagerV2.execute(
        "SELECT module_id FROM role_modules WHERE role_name = :role", {"role": "ADMIN"})
    assert FRAMEWORK_SECTIONS <= {g["module_id"] for g in granted}


# --- the deployment that has been running since before the move ---------------

def test_a_stale_path_is_corrected():
    """The case that would otherwise break a running stand silently.

    The catalogue lives in the database. A deployment that has been running
    since before the sections moved into the package keeps
    `/static/modules/js/users.js` in its row -- and there is no file there any
    more, so the panel fetches a 404 and shows nothing at all.
    """
    DatabaseManagerV2.execute_commit(
        "UPDATE frontend_modules SET js_path = :js, css_path = :css "
        "WHERE module_id = 'users'",
        {"js": "/static/modules/js/users.js", "css": "/static/modules/css/users.css"})

    counted = modules.sync_framework_sections()

    assert counted["repointed"] >= 1
    assert row("users")["js_path"] == "/keepup-static/modules/js/users.js"


def test_a_name_the_administrator_chose_survives():
    """What a person reads in the menu is not the package's to overwrite."""
    DatabaseManagerV2.execute_commit(
        "UPDATE frontend_modules SET name = :name WHERE module_id = 'cluster'",
        {"name": "Our replicas"})

    modules.sync_framework_sections()

    assert row("cluster")["name"] == "Our replicas"


def test_running_it_twice_changes_nothing_the_second_time():
    modules.sync_framework_sections()
    counted = modules.sync_framework_sections()
    assert counted["modules"] == 0 and counted["repointed"] == 0


def test_a_missing_catalogue_is_said_out_loud_and_not_fatal(tmp_path, caplog):
    """A framework that cannot find its own sections must still let the
    application start: a panel with no sections is bad, a server that refuses
    to boot is worse."""
    with caplog.at_level("ERROR"):
        counted = modules.sync_framework_sections(str(tmp_path / "nothing.json"))

    assert counted == {"modules": 0, "repointed": 0, "grants": 0}
    assert any("not synchronised" in record.getMessage() for record in caplog.records)
