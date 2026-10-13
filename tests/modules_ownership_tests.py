"""The catalogue of panel sections is a capability, and a section follows its plugin.

Task keepup-113. The model used to live in the kernel, its tables were created for
every deployment, and a section stayed in the panel after the plugin that drew it
was switched off. It is a capability's model now, the navigation fields are
columns, and the read leaves out the sections of a plugin that is not running --
while the row and the grants an administrator made wait for it.

    python3 -m pytest tests/modules_ownership_tests.py -v
"""

from pathlib import Path

import pytest

from keepup import modules
from keepup.schema import init_db
from keepup_db import DatabaseManagerV2
from keepup_modules import catalogue

PACKAGE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def tables():
    init_db()


@pytest.fixture(autouse=True)
def clean_catalogue():
    DatabaseManagerV2.execute_commit("DELETE FROM role_modules", {})
    DatabaseManagerV2.execute_commit("DELETE FROM frontend_modules", {})
    catalogue.set_declared_plugins(set())
    catalogue.set_running_plugins(set())
    yield
    catalogue.set_declared_plugins(set())
    catalogue.set_running_plugins(set())


def declare(section_id, name, declared_by, nav_order=None, js="/modules/js/x.js"):
    DatabaseManagerV2.execute_commit(
        "INSERT INTO frontend_modules (module_id, name, js_path, declared_by, nav_order,"
        " is_active) VALUES (:id, :name, :js, :by, :order, TRUE)",
        {"id": section_id, "name": name, "js": js, "by": declared_by, "order": nav_order})
    DatabaseManagerV2.execute_commit(
        "INSERT INTO role_modules (role_name, module_id, is_active)"
        " VALUES ('ADMIN', :id, TRUE)", {"id": section_id})


def visible(role="ADMIN"):
    return [row["module_id"] for row in modules.get_modules_for_role(role)]


def test_the_capability_publishes_the_catalogue_model():
    """What another plugin asks for instead of importing the kernel's module."""
    from keepup_modules.plugin import SERVICE_CATALOGUE, SERVICE_PLUGIN_DECISIONS, ModulesPlugin

    assert SERVICE_CATALOGUE == "catalogue" and SERVICE_PLUGIN_DECISIONS == "plugin_decisions"
    assert ("catalogue", 1) in ModulesPlugin.descriptor.provides
    assert ("plugin_decisions", 1) in ModulesPlugin.descriptor.provides


def test_a_section_of_a_plugin_that_is_not_running_is_hidden():
    """The section goes with its plugin; the row and the grant stay."""
    declare("metrics", "System metrics", "metrics")
    declare("users", "Users", "users")
    catalogue.set_declared_plugins({"metrics", "users"})

    catalogue.set_running_plugins({"users"})
    assert visible() == ["users"], "a section of a plugin that is not running was shown"

    catalogue.set_running_plugins({"users", "metrics"})
    assert visible() == ["metrics", "users"], "the section did not come back with its plugin"

    row = DatabaseManagerV2.execute_one(
        "SELECT module_id FROM frontend_modules WHERE module_id = 'metrics'", {})
    assert row is not None, "the row of the switched-off plugin was deleted"


def test_the_navigation_order_is_data_and_not_the_name():
    """A section renamed by an administrator must not move in the menu."""
    declare("zebra", "A section renamed to Zebra", "sections.json", nav_order=1)
    declare("alpha", "Alpha", "sections.json", nav_order=2)
    # A file declares nothing to wait for: only a plugin's sections are filtered.
    catalogue.set_declared_plugins(set())
    catalogue.set_running_plugins(set())

    assert visible() == ["zebra", "alpha"], "the menu is sorted by name, not by nav_order"


def test_the_declaring_plugin_owns_the_code_and_never_the_name():
    """Importing a declaration again corrects what is code and leaves the rest."""
    catalogue.import_declared_sections({"modules": [
        {"id": "reports", "name": "Reports", "js": "/modules/js/one.js",
         "handlerFunction": "renderReports", "routePath": "/reports", "sectionId": "reports",
         "adminOnly": True, "icon": "file", "order": 3}]}, "reports")
    DatabaseManagerV2.execute_commit(
        "UPDATE frontend_modules SET name = :name, js_path = :js WHERE module_id = 'reports'",
        {"name": "My reports", "js": "/modules/js/mine.js"})

    catalogue.import_declared_sections({"modules": [
        {"id": "reports", "name": "Reports", "js": "/modules/js/two.js",
         "handlerFunction": "renderReports", "routePath": "/reports", "sectionId": "reports",
         "adminOnly": True, "icon": "file", "order": 3}]}, "reports")

    row = DatabaseManagerV2.execute_one(
        "SELECT * FROM frontend_modules WHERE module_id = 'reports'", {})
    assert row["name"] == "My reports", "the administrator's name was overwritten"
    assert row["js_path"] == "/modules/js/two.js", "the plugin's own path was not corrected"
    assert row["handler_function"] == "renderReports"
    assert row["route_path"] == "/reports"
    assert row["declared_by"] == "reports"


def test_the_application_s_file_is_not_a_plugin_and_is_always_shown():
    """A file's sections have no plugin to wait for."""
    declare("own", "The application's own", "config/modules.json", nav_order=1)
    catalogue.set_declared_plugins({"metrics"})
    catalogue.set_running_plugins(set())

    assert visible() == ["own"]
