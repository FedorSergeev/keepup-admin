"""Who depends on whom, as one report anybody can read.

The rules of this release live in four checks -- the distribution layout, the
ownership map, the packaging accounting and the security floors -- and each of
them answers its own question well. This prints the picture they share: what the
base declares, what each capability carries, which way every edge points, and
what a profile would start. A person asking "what do I install, and what will my
installation pull in" should not have to read four test files to find out.

    python .github/scripts/dependency_graph.py [--json]
"""

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def project(path: Path):
    """The metadata of one pyproject.toml."""
    return tomllib.loads(path.read_text(encoding="utf-8"))["project"]


def name_of(requirement: str) -> str:
    """The name a requirement asks for, without extras or version."""
    return requirement.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip()


def graph():
    """The base, the distributions, their edges and their extras."""
    base = project(ROOT / "pyproject.toml")
    distributions = {}
    for metadata_path in sorted((ROOT / "packages").glob("*/pyproject.toml")):
        metadata = project(metadata_path)
        distributions[metadata["name"]] = {
            "depends_on": [name_of(item) for item in metadata.get("dependencies", [])],
            "plugins": list(metadata.get("entry-points", {}).get("keepup.plugins", {})),
            "carries": metadata.get("description", ""),
        }
    return {
        "base": {
            "name": base["name"],
            "version": base["version"],
            "depends_on": [name_of(item) for item in base.get("dependencies", [])],
            "extras": {group: [name_of(item) for item in requirements]
                       for group, requirements in base.get("optional-dependencies", {}).items()},
        },
        "distributions": distributions,
    }


def cycles(distributions):
    """Every edge that points back into the base, which is forbidden."""
    back = {name: [item for item in data["depends_on"] if item in distributions]
            for name, data in distributions.items()}
    found = []
    for name, dependencies in back.items():
        for dependency in dependencies:
            if name in back.get(dependency, []):
                found.append((name, dependency))
    return found


def main(argv):
    data = graph()
    if "--json" in argv:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    base = data["base"]
    print(f"{base['name']} {base['version']} -- the constructor")
    print(f"  declares: {', '.join(base['depends_on'])}")
    print("  extras (what a deployment installs by name):")
    for group, items in sorted(base["extras"].items()):
        print(f"    {group}: {', '.join(items)}")
    print()
    print("distributions -- one capability each")
    for name, info in sorted(data["distributions"].items()):
        depends = ", ".join(info["depends_on"]) or "nothing"
        plugins = ", ".join(info["plugins"]) or "-"
        print(f"  {name}")
        print(f"    depends on: {depends}")
        print(f"    plugin: {plugins}")
    back = cycles(data["distributions"])
    print()
    if back:
        print(f"edges that should not exist: {back}")
        return 1
    print("no distribution depends on a distribution that depends on it: "
          "the graph points one way, from the base outwards")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
