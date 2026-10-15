"""The catalogue of panel sections comes from the file the application named.

An application says where its sections and plugins are declared in
``KeepupSettings.plugins_config_path``. The plugins came up from that file,
because initialize_plugins() was handed the path -- and the panel's own
catalogue was seeded from ``config/modules.json`` whatever the application had
said, so a deployment that keeps its file anywhere else started with the
framework's sections and none of its own, and nothing said why.

    python3 -m pytest keepup/tests/catalogue_path_tests.py -v
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from keepup_modules import catalogue as modules
from keepup_db import DatabaseManagerV2
from keepup_http.factory import create_app
from keepup.settings import KeepupSettings


def bare_settings(**overrides):
    """An application that ships no front end and no plugins of its own."""
    defaults = dict(title="Elsewhere", project_name="elsewhere", plugin_manager=None,
                    plugins_dir=None, static_mounts=())
    defaults.update(overrides)
    return KeepupSettings(**defaults)


def catalogue(tmp_path: Path, section_id: str) -> Path:
    """A catalogue elsewhere than config/modules.json, declaring one section."""
    path = tmp_path / "elsewhere.json"
    path.write_text(json.dumps({
        "modules": [{"id": section_id, "name": "Reports", "js": "/modules/js/reports.js"}],
        "roles": [{"name": "ADMIN", "modules": [section_id]}],
    }), encoding="utf-8")
    return path


def section(section_id: str):
    return DatabaseManagerV2.execute_one(
        "SELECT module_id, name FROM frontend_modules WHERE module_id = :id",
        {"id": section_id})


def grant(section_id: str):
    return DatabaseManagerV2.execute_one(
        "SELECT role_name FROM role_modules WHERE module_id = :id", {"id": section_id})


# --- the file the application named --------------------------------------------

def test_the_start_seeds_the_catalogue_the_application_named(tmp_path):
    """The plugins already came up from it; the sections did not."""
    path = catalogue(tmp_path, "catalogue-98-start")

    app = create_app(bare_settings(plugins_config_path=str(path), profile="panel"))
    with TestClient(app):
        pass

    assert section("catalogue-98-start")["name"] == "Reports"
    # And the roles it grants the section to, not only the section.
    assert grant("catalogue-98-start")["role_name"] == "ADMIN"


def test_the_sync_reads_the_path_the_application_configured(tmp_path):
    """The read is the modules capability's, and it takes the path in hand.

    `keepup_db.schema.init_db` used to be the caller an application could reach
    for; it is gone (keepup-158), and the step it ran is this one.
    """
    path = catalogue(tmp_path, "catalogue-98-configured")

    modules.sync_new_modules_from_json(str(path))

    assert section("catalogue-98-configured") is not None


def test_an_application_that_names_nothing_keeps_the_default():
    create_app(bare_settings())

    assert modules.MODULES_CONFIG_PATH == "config/modules.json"


def test_a_missing_catalogue_is_a_warning_and_not_a_refusal(tmp_path):
    """A panel with only the framework's sections is bad; a server that will
    not boot is worse."""
    app = create_app(bare_settings(
        plugins_config_path=str(tmp_path / "nothing.json"), profile="panel"))

    with TestClient(app):
        pass

    assert modules.MODULES_CONFIG_PATH == str(tmp_path / "nothing.json")
