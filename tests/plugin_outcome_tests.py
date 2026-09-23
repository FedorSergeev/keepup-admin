"""What became of each declared plugin, as an answer rather than a log line.

A plugin that failed to initialise looks exactly like one that is switched off:
the server is up and the plugin's routes answer 404. Until that difference was
recorded it was found on the stand, by someone opening a page that used to
work. These tests hold the difference in place.

    python3 -m pytest keepup/tests/plugin_outcome_tests.py -v
"""

import json
from pathlib import Path

import pytest

from keepup.plugins import enablement
from keepup.plugins.base import (
    OUTCOME_DISABLED,
    OUTCOME_FAILED,
    OUTCOME_INITIALIZED,
    OUTCOME_NOT_FOUND,
    BasePlugin,
    PluginManager,
)
from keepup.plugins.admin import get_plugins_status

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parent

PLUGIN_TEMPLATE = '''
from keepup.plugins.base import BasePlugin


class {klass}Plugin(BasePlugin):
    def __init__(self, config=None):
        super().__init__("{plugin_id}", "{plugin_id}", config)

    async def initialize(self):
        {body}

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {{}}
'''


def write_plugin(directory: Path, plugin_id: str, body: str):
    source = PLUGIN_TEMPLATE.format(klass=plugin_id.capitalize(), plugin_id=plugin_id, body=body)
    (directory / f"{plugin_id}.py").write_text(source, encoding="utf-8")


@pytest.fixture
def plugins_dir(tmp_path):
    write_plugin(tmp_path, "good", "self.initialized = True\n        return True")
    write_plugin(tmp_path, "broken", "raise RuntimeError('no database')")
    write_plugin(tmp_path, "refusing", "return False")
    return tmp_path


async def test_a_plugin_that_comes_up_is_recorded_as_initialized(plugins_dir):
    manager = PluginManager(plugins_dir=str(plugins_dir))
    manager.load_plugin("good", {})

    assert await manager.initialize_plugins() is True
    assert manager.get_outcome("good") == (OUTCOME_INITIALIZED, None)
    assert manager.get_plugin("good") is not None


async def test_a_plugin_that_raises_does_not_stop_the_others(plugins_dir):
    """And the reason it raised is kept, not only logged."""
    manager = PluginManager(plugins_dir=str(plugins_dir))
    manager.load_plugin("broken", {})
    manager.load_plugin("good", {})

    assert await manager.initialize_plugins() is False

    outcome, reason = manager.get_outcome("broken")
    assert outcome == OUTCOME_FAILED
    assert "no database" in reason
    # The other one still came up: one broken plugin is not a broken server.
    assert manager.get_outcome("good")[0] == OUTCOME_INITIALIZED
    assert manager.get_plugin("broken") is None


async def test_a_plugin_that_answers_false_is_failed_too(plugins_dir):
    manager = PluginManager(plugins_dir=str(plugins_dir))
    manager.load_plugin("refusing", {})

    await manager.initialize_plugins()

    outcome, reason = manager.get_outcome("refusing")
    assert outcome == OUTCOME_FAILED
    assert reason


def test_a_plugin_whose_file_is_missing_is_recorded_as_not_found(tmp_path):
    manager = PluginManager(plugins_dir=str(tmp_path))

    assert manager.load_plugin("absent", {}) is False
    outcome, reason = manager.get_outcome("absent")
    assert outcome == OUTCOME_NOT_FOUND
    assert "absent.py" in reason


def test_a_class_named_wrong_is_recorded_as_not_found(tmp_path):
    """The name is derived mechanically; a mismatch loses the plugin silently."""
    (tmp_path / "typo.py").write_text("class TypoPlugin_:\n    pass\n", encoding="utf-8")

    manager = PluginManager(plugins_dir=str(tmp_path))

    assert manager.load_plugin("typo", {}) is False
    outcome, reason = manager.get_outcome("typo")
    assert outcome == OUTCOME_NOT_FOUND
    assert "TypoPlugin" in reason


async def test_the_status_report_tells_a_failure_from_a_switch(plugins_dir, monkeypatch, tmp_path):
    """The distinction the whole thing exists for."""
    config = {"plugins": [
        {"id": "good", "name": "Good", "enabled": True},
        {"id": "broken", "name": "Broken", "enabled": True},
        {"id": "refusing", "name": "Off", "enabled": False},
    ]}
    config_path = tmp_path / "modules.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    manager = PluginManager(plugins_dir=str(plugins_dir))
    manager.load_plugin("good", {})
    manager.load_plugin("broken", {})
    manager.record_outcome("refusing", OUTCOME_DISABLED)
    await manager.initialize_plugins()

    from keepup.plugins import admin

    monkeypatch.setattr(admin, "MODULES_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(admin, "read_plugin_overrides", lambda: {})
    # The decision of the last start-up belongs to the manager it was made for.
    manager.resolution = enablement.resolve(config, {})

    rows = {row["id"]: row for row in (await get_plugins_status(manager))["plugins"]}

    assert rows["good"]["outcome"] == OUTCOME_INITIALIZED
    assert rows["good"]["initialized"] is True

    assert rows["broken"]["outcome"] == OUTCOME_FAILED
    assert rows["broken"]["loaded"] is True
    assert rows["broken"]["initialized"] is False
    assert "no database" in rows["broken"]["failure_reason"]

    assert rows["refusing"]["outcome"] == OUTCOME_DISABLED
    assert rows["refusing"]["enabled"] is False
    # Both answer 404 on their routes; only the report says which is which.
    assert rows["broken"]["outcome"] != rows["refusing"]["outcome"]


def test_the_manager_looks_where_the_application_told_it_to(tmp_path):
    """Never at its own package: the framework holds no plugins."""
    write_plugin(tmp_path, "good", "return True")

    manager = PluginManager(plugins_dir=str(tmp_path))

    assert manager.load_plugin("good", {}) is True
    assert isinstance(manager.plugins["good"], BasePlugin)
