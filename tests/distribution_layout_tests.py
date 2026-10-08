"""The distributions: what a deployment installs, and which way the graph points.

Task keepup-115. The base package is the constructor and nothing else; every
capability that needs a library the base must not carry travels in a distribution
of its own, declares what it provides and names its plugin in the entry-point
group the loader reads. This check is the layout and the direction: a driver
depends on the abstraction, the abstraction depends on the constructor, and no
capability depends on another.
"""

import re
import tomllib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
PACKAGES = PACKAGE / "packages"

#: What each distribution is for: the plugin it declares, and what it provides.
EXPECTED = {
    "keepup-db": ("db", "datasource"),
    "keepup-postgres": ("postgres", "datasource_driver"),
    "keepup-sqlite": ("sqlite", "datasource_driver"),
    "keepup-auth": ("auth", "auth"),
    "keepup-users": ("users", "users"),
    "keepup-ui": ("ui", "ui"),
    "keepup-audit": ("audit", "audit"),
    "keepup-metrics": ("metrics", "metrics"),
    "keepup-integration-log": ("integration_logs", "integration_log"),
}

#: What may not be a dependency of anything but what is named.
DRIVERS = {"keepup-postgres", "keepup-sqlite"}


def project(name):
    """The metadata of one distribution."""
    return tomllib.loads((PACKAGES / name / "pyproject.toml").read_text(encoding="utf-8"))


def test_every_planned_capability_has_a_home():
    """Nine distributions, each with its package and its plugin."""
    assert sorted(entry.name for entry in PACKAGES.iterdir() if entry.is_dir()) == \
        sorted(EXPECTED)
    for name in EXPECTED:
        metadata = project(name)
        assert metadata["project"]["name"] == name
        assert metadata["project"]["version"] == "0.4.0"


def test_each_distribution_declares_its_plugin_by_name():
    """The loader names a plugin by its entry point, so the two must agree."""
    for name, (plugin_id, provided) in EXPECTED.items():
        entry_points = project(name)["project"]["entry-points"]["keepup.plugins"]
        assert list(entry_points) == [plugin_id], f"{name} declares {list(entry_points)}"
        module, _, klass = entry_points[plugin_id].partition(":")
        assert module.startswith("keepup_")
        assert klass == f"{plugin_id.capitalize()}Plugin"
        assert provided in (PACKAGES / name / module / "__init__.py").read_text(encoding="utf-8")


def test_every_distribution_is_importable_by_its_own_name():
    """The move of keepup-124 needs the packages on the path before they are installed.

    A distribution is installed in a deployment; in the repository it is a
    directory beside the package, and the suite is told where to find it. Without
    this, moving a declaration into `keepup_db` would be a change that only works
    after a release.
    """
    import importlib
    import tomllib

    for name in EXPECTED:
        package = tomllib.loads(
            (PACKAGES / name / "pyproject.toml").read_text(encoding="utf-8")
        )["tool"]["setuptools"]["packages"][0]
        module = importlib.import_module(package)
        assert module.__version__ == "0.4.0", f"{package} does not carry the release"


def test_the_graph_points_one_way():
    """A capability depends on the abstraction, never on another capability."""
    for name in EXPECTED:
        dependencies = project(name)["project"]["dependencies"]
        for dependency in dependencies:
            base = re.split(r"[<>=!]", dependency)[0]
            assert base in {"keepup-admin", "keepup-db", "keepup-users"} or not base.startswith(
                "keepup-"), f"{name} depends on {base}"
            assert base not in DRIVERS, f"{name} depends on the driver {base}"


def test_the_abstraction_does_not_choose_a_database():
    """`keepup-db` requires a driver, and depends on neither of them."""
    dependencies = project("keepup-db")["project"]["dependencies"]
    assert "sqlalchemy>=2.0,<3" in dependencies
    assert not any("postgres" in item or "sqlite" in item for item in dependencies)


def test_a_driver_depends_on_the_abstraction():
    """Which is what makes `keepup-admin` plus one driver a deployment."""
    for name in DRIVERS:
        dependencies = project(name)["project"]["dependencies"]
        assert "keepup-db>=0.4.0" in dependencies


def test_the_code_that_moves_still_lives_in_the_framework():
    """Until keepup-124: the plugin each distribution will bring exists here."""
    for name, (plugin_id, provided) in EXPECTED.items():
        plugin_file = PACKAGE / "builtin" / f"{plugin_id}.py"
        assert plugin_file.exists(), f"{name} promises {plugin_file.name}, which is not there"
        assert f'id="{plugin_id}"' in plugin_file.read_text(encoding="utf-8")


def test_the_document_says_what_the_base_stops_carrying():
    """The promise keepup-124 has to keep, written where a reader will find it."""
    text = (PACKAGE / "doc" / "distributions.md").read_text(encoding="utf-8")
    for library in ("sqlalchemy", "psycopg2-binary", "bcrypt", "pyjwt"):
        assert library in text
    assert "keepup-admin" in text and "keepup-postgres" in text
