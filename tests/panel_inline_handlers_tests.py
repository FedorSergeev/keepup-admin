"""The framework's own panel carries no inline handler and no inline script (keepup-93).

The Content-Security-Policy the panel is served with refuses both: an attribute
that runs a function is a script the page did not load, and so is a <script>
without a src. The section that still has one is a section that stops working
the moment the policy is enforced -- which is a fault the policy only reports,
not one it causes. The rule is checked over the source so that a new handler
cannot be added quietly.

The files the package ships from somebody else (Tailwind, Feather, AOS,
Chart.js and its adapter) are not ours to change and are left out.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/panel_inline_handlers_tests.py -v
"""

import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
STATIC = PACKAGE / "static"

#: The panel's pages and the framework's own scripts. The vendored libraries
#: are named rather than matched, so a new one has to be added on purpose.
VENDORED = {"tailwind.js", "feather-icons.js", "aos.js", "chart.js",
            "chartjs-adapter-date-fns.bundle.min.js"}
PAGES = sorted(STATIC.glob("*.html"))
SCRIPTS = ([STATIC / "js" / "main_new.js", STATIC / "js" / "layout_bootstrap.js"]
           + sorted(path for path in (STATIC / "modules" / "js").glob("*.js")
                    if path.name not in VENDORED))
SOURCES = PAGES + SCRIPTS

INLINE_HANDLER = re.compile(
    r"""\son(?:click|change|submit|input|load|error|keyup|keydown|keypress"""
    r"""|blur|focus|mouseover|mouseout|dblclick|contextmenu)\s*=\s*["']""",
    re.IGNORECASE)
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)
DATA_ACTION = re.compile(r"""data-action=["']([^"'$]+)["']""")
REGISTERED_ACTION = re.compile(r"""["']([A-Za-z][\w.-]*)["']\s*:""")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_inline_event_handler_is_left(path):
    remaining = [f"{path.name}:{number}" for number, line
                 in enumerate(read(path).splitlines(), 1) if INLINE_HANDLER.search(line)]
    assert remaining == [], f"inline handlers, which the policy refuses: {remaining}"


@pytest.mark.parametrize("path", PAGES, ids=lambda path: path.name)
def test_no_inline_script_is_left_in_the_pages(path):
    remaining = [f"{path.name}:{number}" for number, line
                 in enumerate(read(path).splitlines(), 1) if INLINE_SCRIPT.search(line)]
    assert remaining == [], f"inline scripts, which the policy refuses: {remaining}"


def test_every_action_in_the_markup_is_one_that_is_registered():
    """A renamed action would leave a dead button, and only in a browser."""
    registered = set()
    for path in SCRIPTS:
        registered |= set(REGISTERED_ACTION.findall(read(path)))

    used = set()
    for path in SOURCES:
        used |= set(DATA_ACTION.findall(read(path)))

    assert used, "no section names an action at all"
    assert not (used - registered), f"actions nothing registers: {sorted(used - registered)}"
