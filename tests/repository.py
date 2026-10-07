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

import subprocess
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parent

#: Directories beside the package that are never an application: the package
#: itself, and the places a repository keeps things that import nothing.
NOT_AN_APPLICATION = {"tests", "doc", "config", "static", "openspec", "ci"}

#: Directories a working copy carries that are not the package at all: a local
#: environment, an editor's, the build tracker kept beside the framework, and
#: what a build leaves behind. A check that walks the package has to step over
#: them, or it reports findings about code nobody ships -- a `.venv` inside the
#: checkout was enough to fail the distribution and packaging checks with the
#: names of pip's vendored modules.
NOT_THE_PACKAGE = {
    ".git", ".venv", "venv", ".idea", ".vscode", "ci", "openspec",
    "build", "dist", "static.min", "__pycache__", ".pytest_cache",
}


def is_not_the_package(path: Path) -> bool:
    """Whether a path below the package is outside what the package is.

    Args:
        path: a path below ``PACKAGE``.

    Returns:
        True for a local environment, an editor's directory, the tracker, and
        build output.
    """
    return any(part in NOT_THE_PACKAGE or part.endswith(".egg-info")
               for part in path.relative_to(PACKAGE).parts)


def repository_root() -> Path:
    """The checkout the package sits in, as git sees it.

    ``REPO`` -- the directory above the package -- is the repository root only
    in the repository the package grew in, where an application stands beside
    it. In a clone of the package that directory is wherever somebody put it,
    and walking it finds other people's projects that merely import keepup: the
    public-interface check then judges the framework by a neighbour's code, and
    takes minutes doing it. Git says where the checkout ends; a package with no
    git around it keeps the old answer.
    """
    try:
        answer = subprocess.run(
            ["git", "-C", str(PACKAGE), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return REPO
    if answer.returncode == 0 and answer.stdout.strip():
        return Path(answer.stdout.strip())
    return REPO


def applications():
    """Whatever sits beside the package and imports it.

    Found rather than listed, and that is not a nicety: naming an application
    inside the package is exactly what the framework's boundary forbids, and
    the names would mean nothing in the framework's own repository anyway.

    Returns:
        The directories beside the package whose code imports it, sorted.
    """
    found = []
    for candidate in sorted(repository_root().iterdir()):
        if not candidate.is_dir() or candidate.name.startswith("."):
            continue
        if candidate == PACKAGE or PACKAGE in candidate.parents:
            continue
        if candidate.name in NOT_AN_APPLICATION:
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
    path = repository_root().joinpath(*relative)
    if not path.exists():
        pytest.skip(f"{'/'.join(relative)} belongs to the repository the package "
                    f"grew in, and is not in this one")
    return path
