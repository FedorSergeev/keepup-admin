"""The release publishes every distribution, and the tag names its version.

The base became one distribution of several (keepup-124): a release that builds
and uploads the base alone leaves its extras pointing at projects the index does
not have -- before 0.4.0 every capability name answered 404 -- and a version,
once uploaded, cannot be replaced. The workflow is checked here, and the script
it runs is exercised rather than read (keepup-134).

    python3 -m pytest tests/release_tests.py -v
"""

import importlib.util
import re
import shutil
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
WORKFLOW = PACKAGE / ".github" / "workflows" / "release.yml"
SCRIPT = PACKAGE / ".github" / "scripts" / "release_versions.py"
CAPABILITIES = sorted((PACKAGE / "packages").glob("*/pyproject.toml"))


def script():
    spec = importlib.util.spec_from_file_location("release_versions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_distribution_declares_the_same_version():
    """One tag names one release; the metadata of all ten must agree."""
    versions = {str(path.relative_to(PACKAGE)): script().declared_version(path)
                for path in script().metadata_files()}
    assert len(versions) == len(CAPABILITIES) + 1, versions
    assert len(set(versions.values())) == 1, versions


def test_the_check_accepts_the_version_the_tree_declares():
    declared = script().declared_version(PACKAGE / "pyproject.toml")
    assert script().main([declared]) == 0
    # Without a tag the base is what every capability is held to.
    assert script().main([]) == 0


def test_the_check_refuses_a_distribution_that_drifted(tmp_path):
    """The copy is the tree with one capability's version moved."""
    shutil.copy2(PACKAGE / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copytree(PACKAGE / "packages", tmp_path / "packages",
                    ignore=shutil.ignore_patterns("build", "*.egg-info", "__pycache__"))
    drifted = sorted((tmp_path / "packages").glob("*/pyproject.toml"))[0]
    moved = re.sub(r'^version = "[^"]+"', 'version = "9.9.9"',
                   drifted.read_text(encoding="utf-8"), count=1, flags=re.M)
    drifted.write_text(moved, encoding="utf-8")

    assert script().main(["1.2.3"], root=tmp_path) == 1
    # ...and the base's own version is the one it names, so a release that only
    # moved a capability cannot pass as the base's.
    assert script().main([], root=tmp_path) == 1


def test_the_release_builds_every_distribution():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "--outdir dist/base" in text, "the base is built into its own directory"
    assert re.search(r"for package in packages/\*", text), "every capability is built"
    assert "--outdir dist/capabilities" in text, "and into its own directory"
    assert "release_versions.py" in text, "the versions are checked by the script"


def test_the_capabilities_are_published_before_the_base():
    """The extras of the base name the capabilities: they must be there first."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("pypa/gh-action-pypi-publish") == 2
    capabilities = text.index("packages-dir: dist/capabilities")
    base = text.index("packages-dir: dist/base")
    assert capabilities < base, "the base is published last"
