"""Known vulnerabilities in what the framework depends on (keepup-94).

The same two questions the framework's security workflow asks
(.github/workflows/security.yml), asked of this tree:

- **what is installed here** -- the framework's declared dependencies at the
  versions this environment resolved them to. Not the whole environment: next to
  the framework sit the applications and their own dependencies, whose
  advisories are theirs to answer;
- **the lowest versions the package admits** -- its floors, from
  .github/scripts/floor_requirements.py: an installation that keeps an older
  version inside the declared range is just as much this package's installation.

pip-audit asks an advisory database over the network. Without pip-audit, or
without the network, the check is skipped with the reason -- never passed.

    python3 -m pytest keepup/tests/security_audit_tests/dependencies_tests.py -v
"""

import importlib.util
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest

pytestmark = pytest.mark.area("dependencies")

PACKAGE = Path(__file__).resolve().parents[2]
FLOORS_SCRIPT = PACKAGE / ".github" / "scripts" / "floor_requirements.py"
TIMEOUT_SECONDS = 300

#: What pip-audit says when it could not reach the advisory database.
_OFFLINE = re.compile(r"ConnectionError|Max retries|NameResolution|Failed to establish|"
                      r"Temporary failure|timed out|Network is unreachable", re.I)


def _floors():
    spec = importlib.util.spec_from_file_location("floor_requirements", FLOORS_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.floors(PACKAGE / "pyproject.toml")


def _audit(requirements, tmp_path, label):
    if importlib.util.find_spec("pip_audit") is None:
        pytest.skip("pip-audit is not installed (python -m pip install pip-audit)")
    listing = tmp_path / f"{label}.txt"
    listing.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--progress-spinner", "off",
             "--no-deps", "--disable-pip", "-r", str(listing)],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        pytest.skip(f"pip-audit did not answer within {TIMEOUT_SECONDS} s: no network?")
    output = done.stdout + done.stderr
    if done.returncode not in (0, 1) or (done.returncode and _OFFLINE.search(output)):
        if _OFFLINE.search(output):
            pytest.skip("the advisory database is unreachable: no network")
        pytest.fail(f"pip-audit failed to run ({done.returncode}):\n{output[-2000:]}")
    assert done.returncode == 0, f"{label}: known vulnerabilities\n{output[-4000:]}"


def test_the_installed_dependencies_have_no_known_vulnerability(tmp_path):
    requirements = []
    for name, _ in _floors():
        try:
            requirements.append(f"{name}=={metadata.version(name)}")
        except metadata.PackageNotFoundError:
            continue
    assert requirements, "none of the framework's dependencies is installed here"
    _audit(requirements, tmp_path, "installed")


def test_the_lowest_admitted_versions_have_no_known_vulnerability(tmp_path):
    _audit([f"{name}=={version}" for name, version in _floors()], tmp_path, "floors")
