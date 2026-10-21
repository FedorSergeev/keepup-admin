"""The panel's plugin report reads the snapshot the start took (keepup-167).

The report compares two resolutions: what the deployment decided when it started,
which is what is running, and what the panel's latest switches say, which is what
a restart would bring. The first one is the runtime's, and until keepup-167 the
runtime did not keep it -- the report asked for an attribute that was never set,
so `/api/admin/plugins` answered 500 on a deployment built by the kernel, and
only a test that built the manager by hand ever saw it work.

    python3 -m pytest tests/plugin_report_tests.py -v
"""

import asyncio
import json

import pytest

from keepup.kernel import create_runtime
from keepup_modules import decisions as plugins_admin


@pytest.fixture(autouse=True)
def catalogue(tmp_path, monkeypatch):
    """A catalogue file with nothing declared, which is where the report reads."""
    path = tmp_path / "modules.json"
    path.write_text(json.dumps({"plugins": [], "roles": []}), encoding="utf-8")
    monkeypatch.setattr(plugins_admin, "MODULES_CONFIG_PATH", str(path))
    return path


def test_the_runtime_has_the_snapshot_before_any_start():
    """The crash was an attribute that was never set, not a start that failed."""
    runtime = create_runtime(entry_points=[])
    assert runtime.resolution is not None
    assert runtime.resolution.decisions == {}


def test_the_report_answers_for_a_runtime_and_not_only_for_a_manager():
    """The call `/api/admin/plugins` makes, against the object the kernel gives."""
    async def status():
        runtime = create_runtime(entry_points=[])
        return await plugins_admin.get_plugins_status(runtime)

    assert asyncio.run(status()) == {"plugins": []}
