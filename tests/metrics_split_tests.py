"""Collecting metrics and handing them out live apart (keepup-22).

``keepup/metrics.py`` collects and writes; ``keepup/metrics_api.py`` serves the
Prometheus exposition and the panel's summary. The dependency points one way:
the API reads what the collector wrote, the collector knows nothing of the API.

    python3 -m pytest keepup/tests/metrics_split_tests.py -v
"""

import ast
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

import keepup_metrics as metrics
from keepup_metrics import api as metrics_api

PACKAGE = Path(__file__).resolve().parents[1]
#: The collector lives in the capability's distribution since keepup-124.
COLLECTOR = PACKAGE / "packages" / "keepup-metrics" / "keepup_metrics" / "collector.py"


def _paths():
    """The environment a spawned interpreter needs to find the distributions."""
    import os
    from pathlib import Path

    packages = [str(Path(__file__).resolve().parents[1] / "packages" / name)
                for name in ("keepup-db", "keepup-postgres", "keepup-sqlite", "keepup-auth",
                             "keepup-users", "keepup-ui", "keepup-audit", "keepup-metrics",
                             "keepup-integration-log")]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(packages + [env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    return env


REPO = PACKAGE.parent


def imports_of(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in tree.body:                     # module level: what loading it loads
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def test_the_collector_knows_nothing_of_the_web_or_the_api():
    loaded = imports_of(COLLECTOR)
    assert not {name for name in loaded
                if name.startswith(("fastapi", "pydantic", "keepup_metrics.api",
                                    "keepup_auth"))}


def test_importing_the_collector_does_not_import_the_api():
    probe = ("import sys, keepup_metrics; "
             "print('keepup_metrics.api' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", probe], cwd=REPO,
        env=_paths(), capture_output=True,
                         text=True, check=True).stdout.strip()
    assert out == "False"


@pytest.mark.parametrize("name", sorted(metrics._MOVED_TO_API))
def test_a_moved_name_still_answers_and_says_where_it_went(name):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = getattr(metrics, name)
    assert value is getattr(metrics_api, name)
    assert any(f"keepup_metrics.api.{name}" in str(w.message) for w in caught)


def test_an_unknown_name_is_still_an_attribute_error():
    with pytest.raises(AttributeError):
        metrics.no_such_name


def test_the_application_serves_the_same_routes():
    """The routes come from the capability, not from the kernel (keepup-124)."""
    from keepup_http.factory import create_app
    from keepup.settings import KeepupSettings

    app = create_app(KeepupSettings(title="Metrics", static_mounts=(), plugin_manager=None))
    paths = {route.path for route in app.routes}
    assert {"/metrics", "/api/admin/metrics/system", "/api/admin/metrics/history",
            "/api/admin/instances/{instance_id}",
            "/api/admin/instances/{instance_id}/restart"} <= paths
