"""The release publishes every distribution, one project per job, and the tag names its version.

The base became one distribution of several (keepup-124): a release that builds
and uploads the base alone leaves its extras pointing at projects the index does
not have. The publishing action mints one token per invocation and scopes it to
the one project whose publisher matched, so a job that uploads two projects gets
the first through and is refused for the second (keepup-4). The workflow is
checked here, and the script it runs is exercised rather than read.

    python3 -m pytest tests/release_tests.py -v
"""

import importlib.util
import re
import shutil
from pathlib import Path

import yaml

PACKAGE = Path(__file__).resolve().parents[1]
WORKFLOW = PACKAGE / ".github" / "workflows" / "release.yml"
SCRIPT = PACKAGE / ".github" / "scripts" / "release_versions.py"
CAPABILITIES = sorted(path.parent.name for path in (PACKAGE / "packages").glob("*/pyproject.toml"))
ACTION = "pypa/gh-action-pypi-publish"


def script():
    spec = importlib.util.spec_from_file_location("release_versions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def publishes(job):
    """The publishing steps of one job: one at most, or the token is misused."""
    return [step for step in job["steps"] if str(step.get("uses", "")).startswith(ACTION)]


def run_of(job, needle):
    for step in job["steps"]:
        if needle in str(step.get("run", "")):
            return str(step["run"])
    raise AssertionError(f"no step runs {needle}")


def test_every_capability_is_published_by_a_job_of_its_own():
    """A matrix leg per project: one token, one project, one name in the log."""
    capabilities = workflow()["jobs"]["capabilities"]
    assert capabilities["strategy"]["matrix"]["project"] == CAPABILITIES
    assert capabilities["strategy"]["fail-fast"] is False, "one missing publisher must not cancel the rest"


def test_one_publishing_invocation_per_job():
    """The action says so itself: two invocations in one job are unsupported."""
    jobs = workflow()["jobs"]
    publishing = {name for name, job in jobs.items() if publishes(job)}
    assert publishing == {"capabilities", "base"}
    for name in publishing:
        assert len(publishes(jobs[name])) == 1, f"{name} publishes more than once"


def test_the_capabilities_go_first_and_the_base_waits_for_them():
    jobs = workflow()["jobs"]
    needs = jobs["base"]["needs"]
    needs = {needs} if isinstance(needs, str) else set(needs)
    assert needs == {"gate", "capabilities"}
    assert "gate" in jobs["capabilities"]["needs"]
    order = list(jobs)
    assert order.index("gate") < order.index("capabilities") < order.index("base")
    assert order.index("base") < order.index("sbom"), "the SBOM follows the release"


def test_a_capability_may_be_re_run_and_the_base_may_not():
    """A project interrupted between its wheel and its sdist is finished by a re-run."""
    base = publishes(workflow()["jobs"]["base"])[0]["with"]
    capability = publishes(workflow()["jobs"]["capabilities"])[0]["with"]
    assert capability["skip-existing"] is True
    assert "skip-existing" not in base, "a second upload of the base's version is a mistake"


def test_every_distribution_is_built_and_checked_before_it_is_published():
    jobs = workflow()["jobs"]
    capability = run_of(jobs["capabilities"], "python -m build")
    assert '--outdir dist "packages/${{ matrix.project }}"' in capability
    assert "twine check --strict dist/*" in capability
    base = run_of(jobs["base"], "python -m build")
    assert "python -m build --outdir dist" in base
    assert "twine check --strict dist/*" in base


def test_an_api_token_is_the_way_out_when_a_publisher_cannot_be_registered():
    """A password turns attestations off: they need Trusted Publishing."""
    jobs = workflow()["jobs"]
    for job in jobs.values():
        if not publishes(job):
            continue
        step = publishes(job)[0]["with"]
        assert step["password"] == "${{ secrets.PYPI_API_TOKEN }}"
        assert step["attestations"] == "${{ secrets.PYPI_API_TOKEN == '' }}"


def test_the_tag_is_held_to_every_distribution_by_the_script():
    jobs = workflow()["jobs"]
    for job in jobs.values():
        if not publishes(job):
            continue
        assert "release_versions.py" in run_of(job, "release_versions.py")


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
