"""What the suite can and cannot assume about what surrounds the package.

Task keepup-26. The framework's suite ships with the framework, and in a clone
of the framework there is nothing around it: no application, no deployment
scripts, no tracker region. A check that reads one of those does not merely
fail there -- it fails for a reason that says nothing about the framework,
which is the worst kind of red.

The rule this module exists to serve: a check that needs a consumer finds one
by what it does, never by its name, and says plainly when there is none. A
skip with a reason is a check that could not run; a check that quietly walks an
empty list is a check that is not there at all, and looks green either way.
"""

from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parent

#: Directories beside the package that are never an application: the package
#: itself, and the places a repository keeps things that import nothing.
NOT_AN_APPLICATION = {"tests", "doc", "config", "static", "openspec", "ci"}


def applications():
    """Whatever sits beside the package and imports it.

    Found rather than listed, and that is not a nicety: naming an application
    inside the package is exactly what the framework's boundary forbids, and
    the names would mean nothing in the framework's own repository anyway.

    Returns:
        The directories beside the package whose code imports it, sorted.
    """
    found = []
    for candidate in sorted(REPO.iterdir()):
        if not candidate.is_dir() or candidate.name.startswith("."):
            continue
        if candidate == PACKAGE or candidate.name in NOT_AN_APPLICATION:
            continue
        for path in candidate.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if "import keepup" in path.read_text(encoding="utf-8", errors="ignore"):
                found.append(candidate)
                break
    return found


def some_application():
    """The directories of the applications, or a skip naming why there are none.

    Returns:
        A non-empty list of application directories.

    Raises:
        Skipped: when the package stands alone, which is the normal state of
            its own repository.
    """
    found = applications()
    if not found:
        pytest.skip("no application stands beside the package in this repository")
    return found


def alongside(*relative):
    """A path beside the package, or a skip when this repository has no such thing.

    For what belongs to the repository the package grew in rather than to the
    package: the deployment scripts, the image, the tracker region.

    Args:
        *relative: Path segments below the repository root.

    Returns:
        An existing path.

    Raises:
        Skipped: when the path is not there.
    """
    path = REPO.joinpath(*relative)
    if not path.exists():
        pytest.skip(f"{'/'.join(relative)} belongs to the repository the package "
                    f"grew in, and is not in this one")
    return path
