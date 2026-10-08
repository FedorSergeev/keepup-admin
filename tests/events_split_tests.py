"""The event log apart from its HTTP routes (keepup-23).

``keepup/events.py`` is the journal -- the table, writing, reading, retention;
``keepup/events_api.py`` holds its routes. A module that only emits events does
not load FastAPI, and the routes read the journal like any other reader.

    python3 -m pytest keepup/tests/events_split_tests.py -v
"""

import ast
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from keepup import events, events_api

PACKAGE = Path(__file__).resolve().parents[1]
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


def module_imports(path: Path):
    found = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found |= {node.module or ""} | {f"{node.module}.{a.name}" for a in node.names}
    return found


def test_the_journal_knows_nothing_of_the_web_or_its_routes():
    # By name, not by path: the journal may live in its distribution (keepup-124).
    from keepup.tests.repository import source_of

    loaded = module_imports(source_of("keepup.events"))
    assert not {name for name in loaded
                if name.startswith(("fastapi", "pydantic", "keepup.events_api",
                                    "keepup.auth"))}


def test_emitting_an_event_does_not_load_the_routes():
    probe = "import sys, keepup.events; print('keepup.events_api' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", probe], cwd=REPO, capture_output=True,
        env=_paths(),
                         text=True, check=True).stdout.strip()
    assert out == "False"


def test_the_routes_read_the_journal_the_application_writes():
    assert events_api.events is events


@pytest.mark.parametrize("name", sorted(events._MOVED_TO_API))
def test_a_moved_name_still_answers_and_says_where_it_went(name):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = getattr(events, name)
    assert value is getattr(events_api, name)
    assert any(f"keepup.events_api.{name}" in str(w.message) for w in caught)


def test_the_application_serves_the_same_routes():
    from keepup.factory import create_app
    from keepup.settings import KeepupSettings
    from fastapi.testclient import TestClient

    app = create_app(KeepupSettings(title="Events", static_mounts=(), plugin_manager=None))
    with TestClient(app):
        paths = {route.path for route in app.routes}
    assert {"/api/events", "/api/events/types", "/api/events/instances",
            "/api/events/cleanup", "/api/events/stats"} <= paths
