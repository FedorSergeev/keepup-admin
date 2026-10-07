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

import pytest
from fastapi.testclient import TestClient

from keepup import modules
from keepup.db import DatabaseManagerV2
from keepup.factory import create_app
from keepup.schema import init_db
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


@pytest.fixture(scope="module", autouse=True)
def tables():
    init_db()


# --- the file the application named --------------------------------------------

def test_the_start_seeds_the_catalogue_the_application_named(tmp_path):
    """The plugins already came up from it; the sections did not."""
    path = catalogue(tmp_path, "catalogue-98-start")

    app = create_app(bare_settings(plugins_config_path=str(path)))
    with TestClient(app):
        pass

    assert section("catalogue-98-start")["name"] == "Reports"
    # And the roles it grants the section to, not only the section.
    assert grant("catalogue-98-start")["role_name"] == "ADMIN"


def test_init_db_seeds_it_too_when_it_is_handed_the_path(tmp_path):
    """An application that calls init_db() itself can name the same file."""
    path = catalogue(tmp_path, "catalogue-98-init")

    init_db(plugins_config_path=str(path))

    assert section("catalogue-98-init") is not None


def test_init_db_reads_the_path_the_application_configured(tmp_path):
    """Called with nothing, it reads what create_app() handed to the module.

    The application usually calls init_db() before create_app(), and then it
    passes its own path; this is the other order, and either way the file that
    is read is the one the deployment named.
    """
    path = catalogue(tmp_path, "catalogue-98-configured")
    create_app(bare_settings(plugins_config_path=str(path)))

    init_db()

    assert section("catalogue-98-configured") is not None


def test_an_application_that_names_nothing_keeps_the_default():
    create_app(bare_settings())

    assert modules.MODULES_CONFIG_PATH == "config/modules.json"


def test_a_missing_catalogue_is_a_warning_and_not_a_refusal(tmp_path):
    """A panel with only the framework's sections is bad; a server that will
    not boot is worse."""
    app = create_app(bare_settings(
        plugins_config_path=str(tmp_path / "nothing.json")))

    with TestClient(app):
        pass

    assert modules.MODULES_CONFIG_PATH == str(tmp_path / "nothing.json")
