"""The event log apart from its HTTP routes (keepup-23).

``keepup_audit/events.py`` is the journal -- the table, writing, reading, retention;
``keepup_http/events_api.py`` holds its routes. A module that only emits events does
not load FastAPI, and the routes read the journal like any other reader.

    python3 -m pytest keepup/tests/events_split_tests.py -v
"""

import ast
import subprocess
import sys
from pathlib import Path


from keepup_audit import events
from keepup_http import events_api

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

    journal = (PACKAGE / "packages" / "keepup-audit" / "keepup_audit" / "events.py")
    loaded = module_imports(journal)
    assert not {name for name in loaded
                if name.startswith(("fastapi", "pydantic", "keepup_http.events_api",
                                    "keepup_auth"))}


def test_emitting_an_event_does_not_load_the_routes():
    probe = "import sys, keepup_audit.events; print('keepup_http.events_api' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", probe], cwd=REPO, capture_output=True,
        env=_paths(),
                         text=True, check=True).stdout.strip()
    assert out == "False"


def test_the_routes_read_the_journal_the_application_writes():
    assert events_api.events is events


def test_the_kernel_keeps_no_copy_of_the_journal():
    """The journal is the audit capability's; the base has no events module."""
    assert not (PACKAGE / "events.py").exists(), "the journal is back in the base"
    assert events.__name__ == "keepup_audit.events"
