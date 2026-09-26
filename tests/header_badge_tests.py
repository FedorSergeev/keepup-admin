"""The application's badge in the panel header (keepup-37).

The shell's region that draws the top right corner is cut out of main_new.js by
its markers and executed by a real `node` against a stand-in element: what the
corner says cannot be read off the source.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SHELL = Path(__file__).resolve().parents[1] / "static" / "js" / "main_new.js"
START = "// --- The application's badge in the header"
END = "// --- end of the application's badge in the header"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node: the shell is JavaScript")

PRELUDE = r"""
const ROLE_ADMIN = 'ADMIN';
let currentUser = null;
const corner = { textContent: '', title: '', className: '' };
global.window = {};
global.document = { getElementById: (id) => id === 'userRole' ? corner : null };
const vm = require('vm');
vm.runInThisContext(process.env.REGION, { filename: 'main_new.js' });
function show() { return { text: corner.textContent, title: corner.title, className: corner.className }; }
"""


def region() -> str:
    source = SHELL.read_text(encoding="utf-8")
    return source[source.index(START):source.index(END)]


def run(script: str):
    result = subprocess.run([NODE, "-e", PRELUDE + script], capture_output=True, text=True,
                            timeout=30, env={**os.environ, "REGION": region()})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_without_a_badge_the_corner_shows_the_role():
    shown = run("""
    currentUser = {role: 'ADMIN'}; renderHeaderBadge(); const admin = show();
    currentUser = {role: 'CLIENT'}; renderHeaderBadge();
    console.log(JSON.stringify([admin, show()]));
    """)
    assert shown[0]["text"] == "Admin" and "role-admin" in shown[0]["className"]
    assert shown[1]["text"] == "Client" and "role-client" in shown[1]["className"]


def test_the_applications_badge_takes_the_roles_place_and_gives_it_back():
    shown = run("""
    currentUser = {role: 'CLIENT'};
    window.AppHeader.setBadge({text: '120 credits', title: 'Available balance', tone: 'positive'});
    const badge = show();
    window.AppHeader.clearBadge();
    console.log(JSON.stringify([badge, show()]));
    """)
    assert shown[0] == {"text": "120 credits", "title": "Available balance",
                        "className": "ml-2 role-badge header-badge header-badge-positive"}
    assert shown[1]["text"] == "Client" and shown[1]["title"] == ""


def test_a_tone_outside_the_list_is_neutral_and_an_empty_badge_is_none():
    shown = run("""
    currentUser = {role: 'CLIENT'};
    window.AppHeader.setBadge({text: 'x', tone: 'red; background: url(x)'});
    const odd = show();
    window.AppHeader.setBadge({text: ''});
    console.log(JSON.stringify([odd, show()]));
    """)
    assert shown[0]["className"].endswith("header-badge-neutral")
    assert shown[1]["text"] == "Client"


def test_the_text_is_text():
    """The value often comes from a person's data; it must never become markup."""
    source = region()
    assert "innerHTML" not in source
    assert "corner.textContent = view.text" in source


def test_signing_out_takes_the_badge_away():
    source = SHELL.read_text(encoding="utf-8")
    logout = source[source.index("async function logout()"):]
    logout = logout[:logout.index("\n}\n")]
    assert "headerBadge = null;" in logout
    # And the role is drawn through the region, not written over it elsewhere.
    assert source.count("getElementById('userRole')") == 1
