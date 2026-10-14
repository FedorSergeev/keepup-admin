"""The local check runs what the workflows run, and says so.

Task keepup-156. `.github/scripts/check.sh` exists so that a person can make
every check this repository can make locally in one command -- and a script that
drifts from the workflows is worse than no script: it reports a green tree for
commands nobody runs. These checks read both and compare the parts that matter:
the lint selection, the suite's command, and that every workflow step a person
can run offline has a step in the script.

    python3 -m pytest tests/local_check_tests.py -v
"""

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / ".github" / "scripts" / "check.sh"
QUALITY = PACKAGE / ".github" / "workflows" / "quality.yml"
TESTS = PACKAGE / ".github" / "workflows" / "tests.yml"


def script():
    return SCRIPT.read_text(encoding="utf-8")


def test_the_script_is_there_and_runnable():
    """A check nobody can run is a check nobody runs."""
    assert SCRIPT.is_file(), "the local check is gone"
    assert SCRIPT.stat().st_mode & 0o111, "the local check is not executable"
    assert script().startswith("#!/bin/bash"), "the local check has no shebang"


def test_the_lint_selection_is_the_workflow_s():
    """`--isolated` and the selection are what quality.yml blocks on."""
    step = QUALITY.read_text(encoding="utf-8")
    selection = re.search(r"--select (\S+)", step).group(1)
    assert f"--select {selection}" in script(), (
        "the local check lints with a different selection than quality.yml")


def test_the_suite_command_is_the_workflow_s():
    """The suite job's command, term for term: coverage and the same report."""
    step = TESTS.read_text(encoding="utf-8")
    assert "--cov --cov-config=pyproject.toml" in step
    body = script()
    assert "pytest tests/ -q" in body
    assert "--cov --cov-config=pyproject.toml" in body
    assert "--cov-report=term-missing:skip-covered" in body


def test_every_offline_workflow_step_has_a_step_here():
    """The steps a person can run without the network, by name."""
    body = script()
    for expected in ("ruff", "the suite", "the release", "the security audit",
                     "the distributions"):
        assert expected in body, f"the local check has no step called {expected!r}"


def test_the_script_reports_a_summary_and_fails_loudly():
    """Silence about a skipped step is how a check goes missing."""
    body = script()
    assert "skipped:" in body, "a skipped step is not reported"
    assert "failed:" in body, "the summary does not name what failed"
    assert "exit 1" in body, "the script cannot fail"


def test_the_script_finds_the_package_where_it_is():
    """The scripts live inside the package; the working directory is the package."""
    body = script()
    assert 'PACKAGE="$(cd "$HERE/../.." && pwd)"' in body, (
        "the script looks for the package somewhere else")
    assert 'cd "$PACKAGE"' in body
