"""The names that moved are gone, and the distributions answer for themselves.

Task keepup-122 and keepup-153. 0.4.0 moved ten capabilities out of the base and
kept their old import paths answering for one release, with a warning that named
the distribution to install. That release is over: 0.5.0 removes the layer, so an
old name is an ImportError and the module it moved to is the import.

    python3 -m pytest tests/compatibility_tests.py -v
"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]

#: Where each name that moved lives now. The list is the map the layer carried,
#: kept here so that a name that stops working is reported by name.
MOVED = {
    "keepup.db": "keepup_db",
    "keepup.tables": "keepup_db.tables",
    "keepup.schema": "keepup_db.schema",
    "keepup.positional_sql": "keepup_db.positional_sql",
    "keepup.retention": "keepup_db.retention",
    "keepup.audit": "keepup_audit.audit",
    "keepup.events": "keepup_audit.events",
    "keepup.admin_trail": "keepup_audit.trail",
    "keepup.scheduler": "keepup_tasks.scheduler",
    "keepup.metrics": "keepup_metrics",
    "keepup.metrics_api": "keepup_metrics.api",
    "keepup.metrics_retention": "keepup_metrics.retention",
    "keepup.cluster": "keepup_cluster.cluster",
    "keepup.log_shipping": "keepup_log_shipping.log_shipping",
    "keepup.modules": "keepup_modules.catalogue",
    "keepup.integrations": "keepup_integration_log.integrations",
    "keepup.plugins.admin": "keepup_modules.decisions",
    "keepup.plugins.routes": "keepup_http.routes",
    "keepup.auth": "keepup_auth",
    "keepup.auth.user_roles": "keepup_auth.user_roles",
    "keepup.themes": "keepup_ui.themes",
    "keepup.api_docs": "keepup_http.api_docs",
    "keepup.events_api": "keepup_http.events_api",
    "keepup.body_limit": "keepup_http.body_limit",
    "keepup.factory": "keepup_http.factory",
    "keepup.web": "keepup_http.web",
}


def test_the_layer_is_gone():
    """A module that answers the old names is a module that keeps them alive."""
    assert not (PACKAGE / "compat.py").exists(), "the compatibility layer is back"
    source = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    assert "compat" not in source, "the kernel installs the compatibility layer again"


@pytest.mark.parametrize("old_name", sorted(MOVED))
def test_an_old_name_is_an_import_error(old_name):
    """The name a 0.4.0 application wrote does not resolve to a stand-in."""
    assert old_name not in sys.modules
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(old_name)


@pytest.mark.parametrize("old_name,destination", sorted(MOVED.items()))
def test_the_module_it_moved_to_is_there(old_name, destination):
    """And it is one module, not a copy under the old name as well."""
    module = importlib.import_module(destination)
    assert module is sys.modules[destination]
    assert not old_name.startswith(destination)


def test_an_old_name_is_not_resolvable_in_a_fresh_interpreter():
    """Nothing of the layer survives a process that imports the kernel first."""
    probe = ("import keepup, importlib\n"
             "try:\n"
             "    importlib.import_module('keepup.auth')\n"
             "except ModuleNotFoundError:\n"
             "    print('gone')\n"
             "else:\n"
             "    print('answered')\n")
    result = subprocess.run([sys.executable, "-c", probe], cwd=PACKAGE,
                            capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin"})
    assert result.stdout.strip() == "gone", result.stdout + result.stderr
