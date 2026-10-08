"""Shipping logs to a collector apart from local logging (keepup-24).

``keepup/logging_setup.py`` is the console and rotated files;
``keepup/log_shipping.py`` is the queue, the thread, the retries, the back-off
and registration with the collector. An application that names no collector
never loads the second, and ``logging_setup.configure()`` stays the one place
the application supplies its values.

    python3 -m pytest keepup/tests/log_shipping_split_tests.py -v
"""

import ast
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from keepup import log_shipping, logging_setup

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


def run(code: str) -> str:
    return subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True,
        env=_paths(),
                          text=True, check=True).stdout.strip().splitlines()[-1]


def test_local_logging_loads_none_of_the_shipping_machinery():
    loaded = set()
    for node in ast.parse((PACKAGE / "logging_setup.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            loaded |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            loaded.add(node.module or "")
    assert not loaded & {"requests", "threading", "queue", "asyncio", "keepup.log_shipping"}


def test_a_file_log_does_not_load_the_shipping(tmp_path):
    code = ("import sys; from keepup import logging_setup as ls; "
            f"ls.configure(project_name='p', log_dir={str(tmp_path)!r}); ls.setup_logging(); "
            "print('keepup.log_shipping' in sys.modules)")
    assert run(code) == "False"


def test_an_application_without_a_collector_does_not_load_it():
    code = ("import sys; from fastapi.testclient import TestClient; "
            "from keepup.factory import create_app; from keepup.settings import KeepupSettings; "
            "app = create_app(KeepupSettings(title='t', static_mounts=(), plugin_manager=None)); "
            "c = TestClient(app); c.__enter__(); "
            "print('keepup.log_shipping' in sys.modules)")
    assert run(code) == "False"


def test_values_given_before_the_shipping_loads_reach_it():
    code = ("from keepup import logging_setup as ls; "
            "ls.configure(project_name='early', remote_url='http://collector.invalid/logs', "
            "remote_token='t'); from keepup import log_shipping as sh; "
            "print(sh.PROJECT_NAME, sh.REMOTE_LOG_URL, sh.REMOTE_LOG_TOKEN)")
    assert run(code) == "early http://collector.invalid/logs t"


def test_values_given_after_it_loaded_are_handed_on():
    previous = (log_shipping.PROJECT_NAME, log_shipping.REMOTE_LOG_URL)
    try:
        logging_setup.configure(project_name="later", remote_url="http://other.invalid/logs")
        assert (log_shipping.PROJECT_NAME, log_shipping.REMOTE_LOG_URL) == \
            ("later", "http://other.invalid/logs")
    finally:
        log_shipping.configure(project_name=previous[0], remote_url=previous[1])


@pytest.mark.parametrize("name", sorted(logging_setup._MOVED_TO_SHIPPING))
def test_a_moved_name_still_answers_and_says_where_it_went(name):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = getattr(logging_setup, name)
    assert value is getattr(log_shipping, name)
    assert any(f"keepup.log_shipping.{name}" in str(w.message) for w in caught)
