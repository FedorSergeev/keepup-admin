"""The release's own paperwork, checked (keepup-122, 0.5.0).

The version is one number in fifteen metadata files; the changelog is the only
place a person reads what a release is. A release whose changelog names the
previous version is a release nobody can upgrade to on purpose, and a release
that hides what is left is one that promises more than it did.

    python3 -m pytest tests/release_paperwork_tests.py -v
"""

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
CHANGELOG = PACKAGE / "CHANGELOG.md"


def declared_version():
    text = (PACKAGE / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"', text, re.M).group(1)


def test_the_changelog_opens_with_the_release_the_tree_declares():
    """One tag names one release, and the file a person reads names it too."""
    version = declared_version()
    text = CHANGELOG.read_text(encoding="utf-8")
    assert f"## {version}" in text, f"the changelog has no section for {version}"

    # The newest section is the first one after the title: a file whose top
    # section is an older release is a file nobody updated for this one.
    sections = re.findall(r"^## (.+)$", text, re.M)
    assert sections and sections[0].startswith(version), (
        f"the changelog's newest section is {sections[0]!r}, not {version}")


def release_section():
    """The newest release's part of the changelog, up to the next one."""
    text = CHANGELOG.read_text(encoding="utf-8")
    headings = list(re.finditer(r"^## ", text, re.M))
    assert headings, "the changelog has no release section"
    end = headings[1].start() if len(headings) > 1 else len(text)
    return text[headings[0].start():end]


def test_the_changelog_says_what_the_release_did_and_what_is_left():
    """A release that hides its remainder is one that promises too much."""
    section = release_section()
    for part in ("### Added", "### Changed", "### Fixed"):
        assert part in section, f"the release section has no {part}"
    assert "0.6.0" in section, "the release section does not say what is left for 0.6.0"


def test_the_capabilities_the_release_names_are_the_distributions_that_exist():
    """The changelog's promises are names a reader can install."""
    named = set(re.findall(r"`(keepup-[a-z-]+)`", release_section()))
    named.discard("keepup-admin")  # the base, which lives in the package itself
    assert named, "the release section names no distribution"
    for distribution in sorted(named):
        assert (PACKAGE / "packages" / distribution).is_dir(), (
            f"the changelog names {distribution}, which is not a distribution here")
