"""The badges on the package's front page point at things that exist.

Task keepup-36. A badge is the one piece of this repository that nobody looks
at twice, and the one that breaks silently: rename a workflow and the image
turns into "no status" while the README still looks fine. Nothing fails, nobody
notices, and the page keeps claiming something it no longer checks.

So the addresses are checked rather than trusted: every workflow a badge names
is in the repository, every link belongs to this project, and every status badge
names a branch -- without one it shows the newest run from anywhere, including a
contributor's branch, which is not what a reader takes it to mean.

What a badge does not do is stop anything. A red check blocks no merge by
itself; that is a branch rule, and it lives in the repository's settings rather
than in any file here. This is said in the README and in doc/keepup.md because
a picture of a green tick is easy to mistake for a gate.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/badges_tests.py -v
"""

import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
README = PACKAGE / "README.md"
WORKFLOWS = PACKAGE / ".github" / "workflows"

#: The project the badges must belong to. A badge pointing at somebody else's
#: repository would look right and report their state.
PROJECT = "FedorSergeev/keepup-admin"
DISTRIBUTION = "keepup-admin"

#: Checks deliberately not shown. They answer nothing a reader asks before
#: installing, and six badges in a row stop being read -- see the proposal of
#: keepup-36. Named here so that adding one is a decision, not a drift.
NOT_SHOWN = ("quality", "build", "codeql")

#: ![label](image)(link) as Markdown writes it.
BADGE = re.compile(r"\[!\[(?P<label>[^\]]+)\]\((?P<image>[^)]+)\)\]\((?P<link>[^)]+)\)")

#: A GitHub Actions status image: .../actions/workflows/<file>/badge.svg
ACTIONS_IMAGE = re.compile(
    r"https://github\.com/(?P<project>[^/]+/[^/]+)/actions/workflows/(?P<workflow>[^/]+)/badge\.svg(?P<query>\?[^)]*)?$")


def badges():
    """Every badge of the README, in the order it is written.

    Returns:
        A list of match objects with `label`, `image` and `link` groups.
    """
    return list(BADGE.finditer(README.read_text(encoding="utf-8")))


def action_badges():
    """The badges that report a workflow, as (label, project, workflow, query)."""
    found = []
    for badge in badges():
        match = ACTIONS_IMAGE.match(badge.group("image"))
        if match:
            found.append((badge.group("label"), match.group("project"),
                          match.group("workflow"), match.group("query") or ""))
    return found


# --- there are badges at all ---------------------------------------------------

def test_the_front_page_shows_the_state_of_the_checks():
    """Without these the checks exist and nobody outside the repository knows."""
    labels = {badge.group("label") for badge in badges()}

    assert {"tests", "security"} <= labels, (
        f"the README does not show the state of the run and of the audit: {sorted(labels)}")


def test_the_front_page_says_what_will_be_installed_and_on_what_terms():
    images = " ".join(badge.group("image") for badge in badges())

    assert f"pypi/v/{DISTRIBUTION}" in images, "no badge says which version is current"
    assert f"pypi/l/{DISTRIBUTION}" in images, "no badge says the licence"


def test_housekeeping_checks_are_not_shown():
    """A decision, not an oversight: adding one should take editing this list."""
    shown = {workflow.removesuffix(".yml") for _, _, workflow, _ in action_badges()}
    intruders = sorted(shown & set(NOT_SHOWN))

    assert intruders == [], (
        f"these are housekeeping and belong on the Actions page, not the front page: {intruders}")


# --- the addresses lead somewhere ----------------------------------------------

@pytest.mark.parametrize("label, project, workflow, query", action_badges())
def test_the_workflow_a_badge_names_exists(label, project, workflow, query):
    """A renamed workflow leaves the badge showing "no status" and nothing else."""
    assert (WORKFLOWS / workflow).is_file(), (
        f"the {label!r} badge names {workflow}, which is not in .github/workflows")


@pytest.mark.parametrize("label, project, workflow, query", action_badges())
def test_a_status_badge_names_the_branch(label, project, workflow, query):
    """Without a branch the badge shows the newest run from anywhere.

    Including a branch somebody pushed five minutes ago, which a reader will
    take for the state of the released code.
    """
    assert "branch=" in query, (
        f"the {label!r} badge does not name a branch: it would report whatever ran last")


def test_every_badge_belongs_to_this_project():
    """A badge pointing elsewhere looks right and reports somebody else's state."""
    offenders = []
    for badge in badges():
        for address in (badge.group("image"), badge.group("link")):
            if "github.com" in address and PROJECT not in address:
                offenders.append(f"{badge.group('label')}: {address}")
            if "pypi.org" in address and DISTRIBUTION not in address:
                offenders.append(f"{badge.group('label')}: {address}")

    assert offenders == [], "badges pointing outside this project:\n  " + "\n  ".join(offenders)


def test_a_badge_is_clickable_to_where_it_came_from():
    """A picture with no link makes a reader trust it without being able to check."""
    for badge in badges():
        link = badge.group("link")
        assert link.startswith(("http", "LICENSE", "CHANGELOG")), (
            f"the {badge.group('label')!r} badge leads to {link!r}")


# --- and say plainly what they are not -----------------------------------------

def test_the_page_says_a_badge_does_not_block_a_merge():
    """The easiest thing to misread here is a green tick as a gate."""
    readme = README.read_text(encoding="utf-8").lower()

    assert "does not require" in readme or "shows; it does not" in readme, (
        "the README lets a badge be mistaken for a required check")
