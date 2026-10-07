"""The backend-plugin table in the module management section.

A plugin that is enabled and did not come up used to be visible only as a
line in the log. The rows are rendered by a real `node` from what the
status endpoint returns, and what the row says about such a plugin is what
is checked.

This suite arrived here from an application repository, where it was written
while the framework was a submodule of that tree. The section it renders --
``static/modules/js/modules.js`` -- is the framework's own, so the suite moved
with it; the plugin ids were renamed to neutral ones, because they are the
framework's fixtures and no longer an application's plugins.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]

MODULES_JS = PACKAGE / "static" / "modules" / "js" / "modules.js"
#: The shell's escaping helpers, which sections call (keepup-62), and the action
#: registry a section registers into (keepup-93): both cut out of main_new.js by
#: their markers and loaded before the section, as the page does.
SHELL_JS = PACKAGE / "static" / "js" / "main_new.js"
ACTIONS_START = "// --- what a section asks the shell to do (keepup-93)"
ACTIONS_END = "// --- end of what a section asks the shell to do"
NODE = shutil.which("node")

needs_node = pytest.mark.skipif(NODE is None, reason="the panel is JavaScript: node is needed")

PRELUDE = r"""
const noop = () => {};
global.window = global;
global.document = { addEventListener: noop, getElementById: () => null, querySelector: () => null,
                    querySelectorAll: () => [], createElement: () => ({ style: {}, classList: { add: noop, remove: noop } }) };
global.localStorage = { getItem: () => 'token-123' };
global.feather = { replace: noop };
global.location = { pathname: '/', origin: 'http://localhost' };
global.history = { pushState: noop };
global.showNotification = noop;
global.setTimeout = () => 0;
const vm = require('vm');
const fs = require('fs');
vm.runInThisContext(process.env.SHELL_ACTIONS || '', { filename: 'main_new.js' });
vm.runInThisContext(process.env.SHELL_ESCAPING || '', { filename: 'main_new.js' });
vm.runInThisContext(fs.readFileSync(process.env.MODULES_JS, 'utf8'), { filename: 'modules.js' });
"""


def shell_region(start: str, end: str) -> str:
    text = SHELL_JS.read_text(encoding="utf-8")
    return text[text.index(start):text.index(end)]


def shell_escaping() -> str:
    return shell_region("// --- Escaping data for section markup",
                        "// --- end of escaping data for section markup ---")


def shell_actions() -> str:
    return shell_region(ACTIONS_START, ACTIONS_END)


def run_node(script: str):
    result = subprocess.run(
        [NODE, "-e", PRELUDE + script], capture_output=True, text=True, timeout=30,
        env={**os.environ, "MODULES_JS": str(MODULES_JS),
             "SHELL_ESCAPING": shell_escaping(), "SHELL_ACTIONS": shell_actions()},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


ROWS = [
    {"id": "alpha", "name": "Alpha", "priority": 100, "enabled": True,
     "source": "config", "loaded": True, "initialized": True},
    {"id": "beta", "name": "Beta", "priority": 100, "enabled": False,
     "source": "default", "loaded": False, "initialized": False},
    {"id": "gamma", "name": "Gamma", "priority": 100, "enabled": True,
     "source": "env", "loaded": True, "initialized": False},
]


@needs_node
class TestBackendPluginRows:

    def rows(self):
        return run_node("console.log(JSON.stringify(renderBackendPlugins(%s)));" % json.dumps(ROWS))

    def test_one_row_per_plugin(self):
        html = self.rows()
        assert html.count("<tr") == 3
        for pid in ("alpha", "beta", "gamma"):
            assert pid in html

    def test_a_running_plugin_reads_as_running(self):
        html = self.rows()
        row = html.split("<tr")[1]
        assert "alpha" in row and "Running" in row

    def test_a_plugin_that_is_not_enabled_says_so_and_names_no_failure(self):
        row = self.rows().split("<tr")[2]
        assert "beta" in row and "Not enabled" in row
        assert "Failed to start" not in row

    def test_an_enabled_plugin_that_did_not_come_up_is_called_out(self):
        row = self.rows().split("<tr")[3]
        assert "gamma" in row and "Failed to start" in row
        assert "environment" in row.lower()

    def test_no_rows_reads_as_empty(self):
        html = run_node("console.log(JSON.stringify(renderBackendPlugins([])));")
        assert "<tr" in html and "no plugins" in html.lower()
