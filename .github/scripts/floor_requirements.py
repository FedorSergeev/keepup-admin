"""Print the lowest versions pyproject.toml admits, as pinned requirements.

The security workflow audits two sets: what a fresh install resolves today, and
these floors. The first goes green as soon as newer versions exist; only the
second says whether an installation that satisfies the declared ranges can
still be vulnerable (keepup-69).

    python .github/scripts/floor_requirements.py [pyproject.toml] > floors.txt
"""

import re
import sys
import tomllib
from pathlib import Path

#: name[extras] >= version, anything after it (an upper bound) ignored.
_FLOOR = re.compile(r"^\s*([A-Za-z0-9._-]+)(?:\[[^\]]*\])?\s*>=\s*([^,;\s]+)")


def floors(pyproject: Path):
    """(name, lowest version) of every dependency and optional dependency."""
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    declared = list(project.get("dependencies", []))
    for group, requirements in project.get("optional-dependencies", {}).items():
        if group != "test":
            declared.extend(requirements)
    pinned = []
    for requirement in declared:
        match = _FLOOR.match(requirement)
        if not match:
            raise SystemExit(f"no floor in {requirement!r}: every dependency needs '>='")
        pinned.append((match.group(1), match.group(2)))
    return pinned


def floors_everywhere(package: Path):
    """Every floor this repository declares: the package's, and each distribution's.

    A library that moved into a distribution (keepup-124) is not audited any less
    for having moved: `bcrypt` and `pyjwt` are floors of `keepup-auth` now, and an
    installation that satisfies them still has to be past every known advisory.
    """
    found = list(floors(package / "pyproject.toml"))
    for metadata in sorted((package / "packages").glob("*/pyproject.toml")):
        found.extend(floors(metadata))
    # A library declared more than once has to satisfy every declaration, so the
    # highest floor is the one to audit.
    def version(text):
        return tuple(int(part) for part in text.split(".") if part.isdigit())

    highest = {}
    for name, floor in found:
        if name not in highest or version(floor) > version(highest[name]):
            highest[name] = floor
    return sorted(highest.items())


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[2] / "pyproject.toml"
    for name, version in floors(path):
        print(f"{name}=={version}")


if __name__ == "__main__":
    main(sys.argv)
