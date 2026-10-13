"""The base is standard library: a bare interpreter imports the kernel (keepup-121).

The point of the split is not tidiness. An installation of `keepup-admin` alone
must import, and its constructor must build a runtime, without a web framework,
an ORM, a scheduler or a metrics collector; what needs those names the
distribution that carries it, in a sentence a person can act on.

The check runs in a child interpreter with every third-party library blocked at
the import machinery, so nothing that happens to be installed in this
environment can make it pass.

    python3 -m pytest tests/bare_interpreter_tests.py -v
"""

import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]

#: Every library the capabilities declare, and the base does not.
BLOCKED = (
    "fastapi", "starlette", "pydantic", "sqlalchemy", "psycopg2", "asyncpg",
    "apscheduler", "bcrypt", "jwt", "yaml", "psutil", "prometheus_client",
    "requests", "httpx", "multipart", "cryptography", "urllib3", "uvicorn",
)

SCRIPT = '''
import importlib.abc
import sys

BLOCKED = set(%(blocked)r)


class Blocked(importlib.abc.MetaPathFinder):
    """A library that is not installed, as far as this interpreter is concerned."""

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


sys.meta_path.insert(0, Blocked())

import keepup
from keepup.kernel import create_runtime
from keepup.settings import KeepupSettings

print("imported:", keepup.__version__)

settings = KeepupSettings(title="bare", project_name="bare", static_mounts=(),
                          plugin_manager=None)
runtime = create_runtime(settings)
print("runtime:", runtime is not None)

def says_what_to_install(name, use):
    """Whether using a moved name answers with the distribution to install."""
    try:
        use()
    except ImportError as error:
        said = str(error)
        print(f"{name}:", "keepup-" in said and "install" in said)
    else:
        print(f"{name}: False")


says_what_to_install("create_app", lambda: keepup.create_app)


def use_tables():
    from keepup.tables import table  # noqa: F401


says_what_to_install("tables", use_tables)
'''


def run():
    """The child's output, with the environment the child needs."""
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT % {"blocked": BLOCKED}],
        cwd=PACKAGE, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "SECRET_KEY": "bare-interpreter-key-not-for-deployments"},
    )
    return result


def test_a_bare_interpreter_imports_the_kernel_and_builds_a_runtime():
    result = run()
    assert result.returncode == 0, result.stderr
    assert "imported:" in result.stdout, result.stdout
    assert "runtime: True" in result.stdout, (
        "the constructor does not build a runtime without the capabilities:\n"
        + result.stdout + result.stderr)


def test_the_names_that_left_name_the_distribution_to_install():
    """An ImportError from the depth of a dependency is a riddle, not an answer."""
    result = run()
    assert "create_app: True" in result.stdout, result.stdout + result.stderr
    assert "tables: True" in result.stdout, result.stdout + result.stderr
