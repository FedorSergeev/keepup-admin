"""A tag is not evidence, and a release is not a package dropped on PyPI (keepup-175).

The release workflow publishes with an OIDC identity. These checks hold the
things that make that identity worth trusting: the suite runs first, the tools
and actions are pinned, the SBOM is kept, and the disclosure policy exists.

    python -m pytest tests/release_policy_tests.py -v
"""

import re
from pathlib import Path

import yaml

PACKAGE = Path(__file__).resolve().parents[1]
RELEASE = (PACKAGE / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
SECURITY = (PACKAGE / "SECURITY.md").read_text(encoding="utf-8")

def test_nothing_is_published_before_the_suite():
    assert "  gate:" in RELEASE
    assert "needs: gate" in RELEASE
    assert "needs: [gate, capabilities]" in RELEASE
    assert "pytest tests/" in RELEASE


def test_two_releases_do_not_race():
    assert "concurrency:" in RELEASE
    assert "group: release" in RELEASE


def test_the_build_tools_are_pinned():
    assert "build==1.6.1" in RELEASE
    assert "twine==7.0.0" in RELEASE
    assert "cyclonedx-bom==7.5.0" in RELEASE


def test_every_action_is_pinned_to_a_sha():
    used = re.findall(r"uses:\s+([^\s#]+)", RELEASE)
    assert used, "the workflow uses no actions at all"
    for reference in used:
        assert re.search(r"@[0-9a-f]{40}$", reference), f"not pinned by SHA: {reference}"


def test_the_sbom_is_kept():
    assert "  sbom:" in RELEASE
    assert "cyclonedx-py environment" in RELEASE
    assert "sbom.json" in RELEASE


def test_the_disclosure_policy_names_a_private_channel_and_time():
    assert "security/advisories/new" in SECURITY
    assert "2 business days" in SECURITY
    assert "7 days" in SECURITY and "30 days" in SECURITY
    assert "Do not open a public issue" in SECURITY


def test_no_job_declares_a_key_twice():
    """YAML keeps the last duplicate silently; Actions may refuse the file."""
    node = yaml.compose(RELEASE)

    def walk(value, path):
        if isinstance(value, yaml.MappingNode):
            keys = [key.value for key, _ in value.value]
            assert len(keys) == len(set(keys)), f"duplicate key at {path}: {keys}"
            for key, child in value.value:
                walk(child, f"{path}/{key.value}")
        elif isinstance(value, yaml.SequenceNode):
            for index, child in enumerate(value.value):
                walk(child, f"{path}[{index}]")

    walk(node, "release.yml")
