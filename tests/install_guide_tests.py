"""The pages that say how to install, build and test keepup (keepup-156, 0.5.0).

The release is the one where the capabilities become distributions of their
own, and that turns two ordinary sentences into traps. `pip install keepup-admin`
now installs a constructor with no panel, no sign-in and no database, and a
reader who is not told about the extras concludes the framework is broken.
Running pytest against a clone fails with "No module named 'keepup'", because
the directory a clone lands in is called `keepup-admin`.

Neither failure is loud: the page looks right and the command fails. So
`README.md` and `INSTALL.md` are held to what the metadata declares -- the
base's real name, the extras the tree offers, the distributions under
`packages/`, the Python the package requires -- and the commands are held to the
workflow, so the page and CI cannot drift apart.

    python3 -m pytest tests/install_guide_tests.py -v
"""

import re
import tomllib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
README = PACKAGE / "README.md"
GUIDE = PACKAGE / "INSTALL.md"
PYPROJECT = PACKAGE / "pyproject.toml"
PACKAGES = PACKAGE / "packages"
TESTS_WORKFLOW = PACKAGE / ".github" / "workflows" / "tests.yml"

#: A command that names an extra: `keepup-admin[panel,postgres]`.
EXTRA = re.compile(r"keepup-admin\[([^\]]+)\]")
#: A distribution named in prose, in backticks.
DISTRIBUTION = re.compile(r"`(keepup-[a-z][a-z-]*)`")
#: What a clone installs the suite from, in the page and in the workflow.
DEVELOPMENT_INSTALL = '-e ".[test]" -e packages/keepup-*'


def metadata():
    """The base's own metadata, parsed."""
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]


def readme():
    return README.read_text(encoding="utf-8")


def guide():
    return GUIDE.read_text(encoding="utf-8")


def extras():
    """The names `pip install keepup-admin[...]` accepts."""
    return set(metadata()["optional-dependencies"])


def distributions():
    """Every capability the tree ships, by distribution name."""
    return sorted(entry.name for entry in PACKAGES.iterdir() if entry.is_dir())


# --- the guide exists, and the front page sends a reader to it ------------------

def test_the_front_page_has_the_three_sections_and_points_at_the_guide():
    """Install, build and test are what a reader arrives to find."""
    page = readme()

    for heading in ("## Install", "## Build", "## Tests"):
        assert heading in page, f"the README has no {heading!r} section"
    assert GUIDE.is_file(), "the README points at INSTALL.md, which does not exist"
    assert "INSTALL.md" in page, "the front page does not link the install guide"
    assert "pip install" in guide(), "the guide does not say how to install"
    assert "python -m build" in guide(), "the guide does not say how to build"
    assert "pytest" in guide(), "the guide does not say how to test"


# --- the names are the metadata's, not the page's -------------------------------

def test_the_install_command_names_the_distribution_the_metadata_declares():
    """A page that installs the wrong name installs nothing, or somebody else's."""
    name = metadata()["name"]

    assert f"pip install {name}" in readme(), f"the README does not install {name}"
    assert f"pip install {name}" in guide(), f"the guide does not install {name}"


def test_every_extra_a_page_offers_is_one_the_metadata_declares():
    """A reader who types an extra that does not exist gets a warning and no panel."""
    offered = set()
    for match in EXTRA.finditer(readme() + guide()):
        offered |= {part.strip() for part in re.split(r"[,\s]+", match.group(1)) if part.strip()}

    assert offered, "neither page offers a single extra"
    unknown = sorted(offered - extras())
    assert unknown == [], f"the pages offer extras the metadata does not declare: {unknown}"


def test_every_distribution_the_tree_ships_is_named_by_a_page():
    """All fourteen, because a capability nobody can find is one nobody installs."""
    text = readme() + guide()
    missing = [name for name in distributions() if name not in text]

    assert not missing, f"no page names these distributions: {missing}"


def test_a_distribution_a_page_names_is_one_that_exists():
    """The reverse: a name that stopped existing is a reader sent to a 404."""
    base = metadata()["name"]
    named = set(DISTRIBUTION.findall(guide() + readme())) - {base}
    missing = sorted(named - set(distributions()))

    assert not missing, f"the pages name distributions that do not exist: {missing}"


def test_the_python_requirement_is_the_one_the_metadata_declares():
    """`requires-python` is the one answer; a page repeating it can drift."""
    required = metadata()["requires-python"].lstrip(">=<~! ")
    both = (readme(), guide())

    for page in both:
        assert f"Python {required}" in page, f"a page does not require Python {required}"


# --- the commands are the workflow's --------------------------------------------

def test_the_development_install_is_the_one_the_workflow_runs():
    """A clone installs the capabilities from the tree, here and in CI alike."""
    workflow = TESTS_WORKFLOW.read_text(encoding="utf-8")

    assert DEVELOPMENT_INSTALL in workflow, "the workflow no longer installs this way"
    assert DEVELOPMENT_INSTALL in readme(), "the README tells a clone to install something else"
    assert DEVELOPMENT_INSTALL in guide(), "the guide tells a clone to install something else"


def test_the_test_command_is_the_one_the_workflow_runs():
    """The page and the check that runs on push must be one command."""
    command = "python -m pytest tests/"
    workflow = TESTS_WORKFLOW.read_text(encoding="utf-8")

    assert command in workflow, "the workflow no longer runs the suite this way"
    assert command in readme(), "the README shows another test command"
    assert command in guide(), "the guide shows another test command"


def test_the_build_and_the_release_are_shown_the_way_they_run():
    """Building the base alone is what leaves the capabilities unpublished."""
    workflow = (PACKAGE / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")

    assert "python -m build --outdir dist" in workflow
    for page in (readme(), guide()):
        assert "python -m build --outdir dist" in page, (
            "a page builds the base and forgets the capabilities")
        assert "twine check --strict" in page, "a page builds without checking"


# --- and say plainly why installing comes first ---------------------------------

def test_both_pages_say_the_package_is_installed_before_the_suite_runs():
    """The trap this file exists for: pytest against a clone finds no `keepup`."""
    for page in (readme(), guide()):
        assert "install first" in page.lower(), (
            "a page tells a reader to test before installing, which cannot work")
