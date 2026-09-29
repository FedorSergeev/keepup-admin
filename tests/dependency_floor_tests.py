"""The lowest versions the package admits are free of known advisories (keepup-69).

The declared ranges let an installation keep a PyJWT, python-multipart or
requests with published advisories, and left cryptography and urllib3 with no
floor at all; the security workflow audited only what a fresh install resolved
that day. The floors are raised, and the workflow audits them as well.

    python3 -m pytest keepup/tests/dependency_floor_tests.py -v
"""

import importlib.util
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / ".github" / "scripts" / "floor_requirements.py"
WORKFLOW = PACKAGE / ".github" / "workflows" / "security.yml"

#: The first version clear of every advisory published when the floor was set
#: (pip-audit, 29.09.2026). A floor below it readmits a known vulnerability.
FIRST_CLEAN = {
    "pyjwt": "2.13",
    "python-multipart": "0.0.31",
    "requests": "2.33",
    "cryptography": "50.0",
    "urllib3": "2.7",
}


def script():
    spec = importlib.util.spec_from_file_location("floor_requirements", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def as_tuple(version):
    return tuple(int(part) for part in version.split("."))


def test_every_runtime_dependency_has_a_floor_and_the_test_extra_does_not_count():
    names = [name for name, _ in script().floors(PACKAGE / "pyproject.toml")]
    assert "fastapi" in names and "asyncpg" in names
    assert "pytest" not in names
    assert len(names) == len(set(names))


@pytest.mark.parametrize("name, clean", sorted(FIRST_CLEAN.items()))
def test_the_floor_is_past_every_known_advisory(name, clean):
    floors = dict(script().floors(PACKAGE / "pyproject.toml"))
    assert name in floors, f"{name} has no floor of its own"
    assert as_tuple(floors[name]) >= as_tuple(clean)


def test_a_dependency_without_a_floor_is_refused(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\ndependencies = ["requests"]\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="no floor"):
        script().floors(pyproject)


def test_the_security_workflow_audits_the_floors():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert ".github/scripts/floor_requirements.py > floors.txt" in text
    assert "-r floors.txt" in text
