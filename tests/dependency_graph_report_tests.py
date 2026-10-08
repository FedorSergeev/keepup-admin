"""The graph, readable in one command (keepup-124).

The rules of the release live in four checks, each answering its own question.
This runs the report that puts the picture together -- what the base declares, what
each capability carries, which way the edges point, what a profile installs -- so
that a person asking "what do I install and what does it pull in" does not have to
read four test files.
"""

import json
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / ".github" / "scripts" / "dependency_graph.py"


def report(*arguments):
    """The report, as text and as data."""
    text = subprocess.run([sys.executable, str(SCRIPT), *arguments],
                          capture_output=True, text=True, check=True).stdout
    return text


def test_the_report_names_the_base_its_extras_and_the_distributions():
    """What a deployment installs, and what each part carries."""
    text = report()
    assert "keepup-admin 0.4.0" in text, "the release the graph describes"
    for group in ("panel", "postgres", "sqlite", "test"):
        assert f"{group}: " in text, f"no extra {group} in the report"
    data = json.loads(report("--json"))
    assert data["base"]["name"] == "keepup-admin"
    assert "keepup-db" in data["distributions"]
    assert data["distributions"]["keepup-auth"]["plugins"] == ["auth"]


def test_the_report_says_the_graph_points_one_way():
    """The property the release rests on, in one line of output."""
    assert "the graph points one way" in report()


def test_the_report_shows_a_capability_its_own_libraries():
    """A distribution declares the libraries it needs, not the base."""
    data = json.loads(report("--json"))
    assert {"bcrypt", "pyjwt"} <= set(data["distributions"]["keepup-auth"]["depends_on"])
    assert "bcrypt" not in data["base"]["depends_on"]


def test_the_guide_names_the_command():
    """A maintainer who does not know the command will not run it."""
    guide = (PACKAGE / "AGENTS.md").read_text(encoding="utf-8")
    assert "dependency_graph.py" in guide
