"""The framework as something you can install.

Task keepup-5. Until now `keepup` was a directory somebody happened to have
next to their code: it worked because the process was started in this
repository, with this repository's requirements.txt installed. A package is the
opposite claim -- that it works somewhere else, with nothing of ours around it.

The heavy check here builds a wheel and installs it into a throwaway virtual
environment. That costs about a minute and needs the network, so it is marked
`slow` and skipped unless KEEPUP_PACKAGING_TESTS=1. Everything above it is
static and always runs.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/packaging_tests.py -v
    KEEPUP_PACKAGING_TESTS=1 python3 -m pytest keepup/tests/packaging_tests.py -v
"""

import ast
import os
import re
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from keepup.tests.repository import alongside

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "keepup"
PYPROJECT = PACKAGE / "pyproject.toml"

#: Third-party distributions the framework may depend on, by import name. A
#: package that installs itself must ask for everything it imports and nothing
#: else -- an application's dependency that happens to be installed here is
#: exactly what makes a package work in this repository and nowhere else.
DEPENDENCY_IMPORTS = {
    "apscheduler", "bcrypt", "fastapi", "httpx", "jose", "prometheus_client",
    "psutil", "psycopg2", "pydantic", "requests", "sqlalchemy", "starlette",
    "yaml", "multipart",
    # Declared as an optional extra: the bus between replicas needs it on
    # PostgreSQL, a single-process deployment on SQLite does not.
    "asyncpg",
    # Read for a version string in the cluster registry and the log setup;
    # whoever serves the application brings it.
    "uvicorn",
}

STANDARD_LIBRARY = set(sys.stdlib_module_names)


def pyproject_text():
    return PYPROJECT.read_text(encoding="utf-8")


def framework_imports():
    """Every top-level module the framework imports, at any level."""
    found = set()
    for path in PACKAGE.rglob("*.py"):
        # The package's dependencies are what the package imports. Its tests
        # import pytest and whatever they need to pretend with; that is the
        # test environment's business, not the distribution's (keepup-6).
        if (PACKAGE / "tests") in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


# --- the metadata -------------------------------------------------------------

def test_the_package_has_its_own_metadata():
    """Inside keepup/, not at the repository root.

    The root belongs to the applications, and this file has to travel with the
    directory when it becomes a repository of its own (keepup-8).
    """
    assert PYPROJECT.is_file()


def test_the_distribution_is_named_for_a_free_name():
    """`keepup` on PyPI is an unrelated package from 2015.

    It installs a script and no module, so the import name can stay `keepup`
    while the distribution is called something else.
    """
    text = pyproject_text()
    assert re.search(r'^name\s*=\s*"keepup-admin"', text, re.M)


def test_the_version_is_the_one_the_region_builds():
    """A cross-check against the tracker, not the version itself.

    The version the package declares is checked above; this says the region
    that builds it agrees. In a clone of the framework there is no region, and
    then there is nothing to disagree with.
    """
    project = alongside("ci", "keepup", "project.json").read_text(encoding="utf-8")
    declared = re.search(r'"version":\s*"([^"]+)"', project).group(1)
    assert re.search(rf'^version\s*=\s*"{re.escape(declared)}"', pyproject_text(), re.M)


def test_the_package_is_mapped_to_this_directory():
    """Discovery would install `auth` and `plugins` as top-level packages.

    From inside keepup/ there is no `keepup` directory to find, only its
    contents -- and `import keepup` would then fail on an installation that
    looked healthy.
    """
    text = pyproject_text()
    assert re.search(r'^\s*keepup\s*=\s*"\."', text, re.M)
    for subpackage in ("keepup.auth", "keepup.plugins", "keepup.auth.providers"):
        assert f'"{subpackage}"' in text, f"{subpackage} is not in the package list"


def test_every_subpackage_is_declared():
    """A subpackage added later and not listed here is missing from the wheel."""
    text = pyproject_text()
    on_disk = {
        "keepup." + str(path.parent.relative_to(PACKAGE)).replace("/", ".")
        for path in PACKAGE.rglob("__init__.py")
        # `python -m build` leaves a copy of the package under build/ beside
        # pyproject.toml -- that is output, not a subpackage.
        if path.parent != PACKAGE
        and not {"build", "dist", "__pycache__"} & set(path.parts)
    }
    missing = sorted(name for name in on_disk if f'"{name}"' not in text)
    assert missing == [], f"not declared in pyproject.toml: {missing}"


def test_the_panel_shell_travels_with_the_package():
    """Without it an installed keepup serves no panel at all (keepup-3)."""
    text = pyproject_text()
    for pattern in ("static/*.html", "static/js/*.js", "static/css/*.css"):
        assert pattern in text


# --- the licence ----------------------------------------------------------------

def test_the_package_says_it_is_apache_two():
    """Declared as an SPDX expression, which is what a public index reads.

    Publishing puts the sources where anyone can read them, so "all rights
    reserved" beside them would have said the opposite of the intent: readable
    by everyone, usable by nobody. The classifier that used to carry this is
    deliberately absent -- with an expression present the build refuses it
    (PEP 639).
    """
    text = pyproject_text()

    assert re.search(r'^license\s*=\s*"Apache-2\.0"', text, re.M)
    assert "License :: OSI Approved" not in text


def test_the_licence_text_is_the_licence_itself():
    """A licence file that is not the licence grants nothing."""
    licence = (PACKAGE / "LICENSE").read_text(encoding="utf-8")

    assert "Apache License" in licence and "Version 2.0, January 2004" in licence
    assert "END OF TERMS AND CONDITIONS" in licence, "the text is truncated"
    assert "All rights reserved" not in licence


def test_the_attribution_travels_with_the_package():
    """Clause 4(d): a NOTICE, where there is one, goes with every derivative."""
    assert (PACKAGE / "NOTICE").is_file()
    assert "Apache License" in (PACKAGE / "NOTICE").read_text(encoding="utf-8")
    assert re.search(r'^license-files\s*=', pyproject_text(), re.M)


def test_the_third_party_notices_ship_with_what_they_describe():
    """Five vendored bundles are MIT, and minification took their headers.

    MIT asks that its notice travel with the copy. Without this file the
    package would be published in breach of somebody else's licence -- a small
    breach, and a real one.
    """
    notices = (PACKAGE / "THIRD-PARTY.md").read_text(encoding="utf-8")
    for bundle in ("tailwind.js", "feather-icons.js", "aos.js",
                   "chart.js", "chartjs-adapter-date-fns"):
        assert bundle in notices, f"{bundle} ships with no notice"
    assert "Permission is hereby granted, free of charge" in notices, \
        "the MIT text itself is not there"


# --- the dependencies ---------------------------------------------------------

def test_the_package_asks_for_everything_it_imports():
    """An application's dependency that happens to be installed here is what
    makes a package work in this repository and nowhere else."""
    outside = framework_imports() - STANDARD_LIBRARY - {"keepup"}
    undeclared = sorted(outside - DEPENDENCY_IMPORTS)
    assert undeclared == [], (
        "the framework imports these and the package does not account for them: "
        + ", ".join(undeclared))


@pytest.mark.parametrize("name", sorted(DEPENDENCY_IMPORTS - {"uvicorn"}))
def test_each_dependency_is_written_down(name):
    """Including the optional one, which is written down as an extra."""
    distribution = {
        "jose": "python-jose", "yaml": "PyYAML", "psycopg2": "psycopg2-binary",
        "prometheus_client": "prometheus-client", "multipart": "python-multipart",
    }.get(name, name)
    assert distribution.lower() in pyproject_text().lower(), (
        f"{name} is imported but {distribution} is not in the dependencies")


def test_the_application_s_own_dependencies_stay_out():
    """The repository's requirements.txt carries these; the framework must not."""
    text = pyproject_text().lower()
    for name in ("boto3", "pillow", "tokenizers", "xmltodict", "aiohttp",
                 "ldap3", "jinja2", "starlette-authlib"):
        assert name not in text, f"{name} belongs to an application, not to the framework"


# --- the heavy check ------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.skipif(os.getenv("KEEPUP_PACKAGING_TESTS") != "1",
                    reason="builds a wheel and installs it; set KEEPUP_PACKAGING_TESTS=1")
def test_an_installed_package_builds_an_application_with_nothing_of_ours_around_it(tmp_path):
    """The claim a package makes, checked the only way it can be.

    A wheel, a fresh interpreter, an empty working directory somewhere else --
    and a panel comes back. Everything this repository provides by accident is
    absent, which is the point.
    """
    dist = tmp_path / "dist"
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(dist),
                    str(PACKAGE)], check=True, capture_output=True)
    [wheel] = list(dist.glob("keepup_admin-*.whl"))

    environment = tmp_path / "env"
    venv.create(environment, with_pip=True)
    python = environment / "bin" / "python"
    subprocess.run([str(python), "-m", "pip", "install", "-q", str(wheel)],
                   check=True, capture_output=True)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    probe = """
from fastapi.testclient import TestClient
from keepup.factory import create_app
from keepup.settings import KeepupSettings

app = create_app(KeepupSettings(title="Somebody else", project_name="other",
                                plugin_manager=None, plugins_dir=None,
                                static_mounts=()))
client = TestClient(app)
print(client.get("/selfcare").status_code,
      client.get("/keepup-static/js/main_new.js").status_code,
      client.get("/api/health").status_code)
"""
    finished = subprocess.run([str(python), "-c", probe], cwd=elsewhere,
                              capture_output=True, text=True)
    assert finished.returncode == 0, finished.stderr[-2000:]
    assert finished.stdout.strip().endswith("200 200 200"), finished.stdout
