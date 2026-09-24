"""The project's front page, and what it must not start lying about.

Task keepup-33. `docs/index.html` is the address of the project: the page a
reader reaches before deciding to install anything. It therefore repeats things
the package already declares -- the distribution name in the install command,
the Python requirement, the licence, the addresses -- and a page that repeats
metadata is a page that drifts from it silently. Nothing breaks when it does:
the site keeps serving, the command on it simply stops working.

So every repeated fact here is checked against `pyproject.toml` rather than
written down twice. The rest of the file checks what makes the page publishable
at all: it renders from the repository alone, it reaches nothing outside, and no
address on it is a placeholder.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/landing_page_tests.py -v
"""

import re
import tomllib
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
PYPROJECT = PACKAGE / "pyproject.toml"
DOCS = PACKAGE / "docs"
PAGE = DOCS / "index.html"

#: Licence names that must not appear on the page while the package declares
#: another one. Not an exhaustive list of licences -- a list of the ones a
#: page like this plausibly acquires by being edited.
OTHER_LICENCES = ("MIT", "GPL", "BSD", "Proprietary", "All rights reserved")

#: What an address looks like when nobody filled it in.
PLACEHOLDERS = ('href="#"', "example.com", "example.test", "example.org",
                "TODO", "FIXME", "localhost")


def metadata():
    """The package's own metadata, parsed."""
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["project"]


def page_text():
    return PAGE.read_text(encoding="utf-8")


def declared_urls():
    """The addresses the package declares, by their label."""
    return metadata()["urls"]


def index_address():
    """Where the distribution lives in the index.

    Derived from the distribution name rather than written down: the index
    address of a package is its name, and a second copy of the name is a
    second thing to keep in step.
    """
    return f"https://pypi.org/project/{metadata()['name']}/"


def addresses_on_the_page():
    return set(re.findall(r'https?://[^"\'\s<>)]+', page_text()))


def local_references():
    """Everything the page asks for from beside itself."""
    found = set(re.findall(r'(?:href|src)="([^"]+)"', page_text()))
    return {value for value in found
            if not value.startswith(("http://", "https://", "mailto:", "#"))}


def test_the_project_has_a_page():
    assert PAGE.exists(), (
        "docs/index.html is the project's front page and what GitHub Pages "
        "serves from this branch"
    )
    assert len(page_text()) > 2000, "a front page with nothing on it is not one"


def test_the_page_is_marked_as_the_language_it_is_written_in():
    # The distribution boundary already forbids Russian anywhere in the
    # package; a reader's browser learns the language from the attribute.
    assert 'lang="en"' in page_text()


def test_the_install_command_names_the_distribution():
    name = metadata()["name"]
    assert f"pip install {name}" in page_text(), (
        f"the page must offer `pip install {name}` -- the name the index knows"
    )


def test_the_tagline_is_the_description_the_package_declares():
    assert metadata()["description"] in page_text(), (
        "the page and the index describe the package in the same words, or one "
        "of the two is out of date"
    )


def test_the_python_requirement_matches_the_metadata():
    requirement = metadata()["requires-python"]
    minimum = requirement.removeprefix(">=").strip()
    assert f"Python {minimum} or newer" in page_text(), (
        f"the metadata asks for {requirement}; the page must say the same"
    )


def test_the_licence_named_is_the_licence_declared():
    declared = metadata()["license"]
    text = page_text()
    assert declared in text, f"the page must name the licence it ships under ({declared})"
    for other in OTHER_LICENCES:
        assert other not in text, (
            f"the page names {other} while the package declares {declared}"
        )


def test_every_address_on_the_page_is_one_the_package_declares():
    allowed = tuple(declared_urls().values()) + (index_address(),)
    for address in sorted(addresses_on_the_page()):
        assert address.startswith(allowed), (
            f"{address} is on the page but in none of the places the package "
            f"declares: {sorted(allowed)}"
        )


@pytest.mark.parametrize("label", ["Repository", "Changelog", "Issues"])
def test_every_place_the_package_declares_is_reachable_from_the_page(label):
    # Homepage is exempt: it is this page, and a page does not link to itself.
    address = declared_urls()[label]
    assert address in page_text(), (
        f"{label} is declared in the metadata but the page does not lead there"
    )


def test_the_page_leads_to_the_index():
    assert index_address() in page_text(), (
        "a reader who wants the package itself must be able to reach the index "
        "from the page"
    )


def test_no_address_is_a_placeholder():
    text = page_text()
    for placeholder in PLACEHOLDERS:
        assert placeholder not in text, (
            f"{placeholder} on a published page is a dead end"
        )


def test_everything_the_page_asks_for_is_beside_it():
    for reference in sorted(local_references()):
        target = (DOCS / reference).resolve()
        assert DOCS.resolve() in target.parents or target == DOCS.resolve(), (
            f"{reference} leads out of the published directory"
        )
        assert target.exists(), f"the page asks for {reference} and it is not there"


def test_the_page_loads_nothing_from_outside():
    # A page that fetches a stylesheet or a script from elsewhere renders as
    # unstyled text without a network -- and brings the reader a third party
    # they did not choose. Every asset is local, so no external load is legal.
    for tag in re.findall(r"<(?:link|script|img|iframe)\b[^>]*>", page_text()):
        assert "http://" not in tag and "https://" not in tag, (
            f"this tag loads from outside the repository: {tag}"
        )
    for stylesheet in sorted(DOCS.rglob("*.css")):
        assert "url(http" not in stylesheet.read_text(encoding="utf-8"), (
            f"{stylesheet.name} fetches something from outside"
        )
        assert "@import" not in stylesheet.read_text(encoding="utf-8"), (
            f"{stylesheet.name} imports another stylesheet; keep it self-contained"
        )


def test_the_page_is_not_data_of_the_package():
    # The panel shell is package data and travels into every installation.
    # This page has nothing to do with a running application, and an
    # installation must not carry it.
    with PYPROJECT.open("rb") as handle:
        patterns = tomllib.load(handle)["tool"]["setuptools"]["package-data"]["keepup"]
    for pattern in patterns:
        assert not pattern.startswith("docs"), (
            f"{pattern} would ship the project page inside the package"
        )
    assert not (DOCS / "__init__.py").exists(), (
        "with an __init__.py the published directory becomes a subpackage that "
        "has to be declared, and then installed"
    )


def test_jekyll_does_not_process_the_published_directory():
    assert (DOCS / ".nojekyll").exists(), (
        "GitHub Pages runs Jekyll over a directory without this file, which "
        "drops anything whose name begins with an underscore"
    )
