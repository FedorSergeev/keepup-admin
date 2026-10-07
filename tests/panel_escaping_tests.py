"""Panel sections escape what came from data (keepup-62).

A user name with markup in it ran as a script in the session of the administrator
who opened the Users section: the list spliced it into innerHTML and into inline
``onclick`` handlers. The users list is drawn here by a real ``node`` against a
stand-in document; the other sections are held to one rule read off their
source -- no data interpolated into an inline handler as a quoted string -- and
the name itself is held to a plain shape when an account is created.

    python3 -m pytest keepup/tests/panel_escaping_tests.py -v
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from keepup.auth.oidc_routes import propose_username
from keepup.auth.usernames import is_valid_username, safe_username

PACKAGE = Path(__file__).resolve().parents[1]
SECTIONS = PACKAGE / "static" / "modules" / "js"
SHELL = PACKAGE / "static" / "js" / "main_new.js"
NODE = shutil.which("node")
HOSTILE = "<img src=x onerror=alert(1)>');alert('x"
ACTIONS_START = "// --- what a section asks the shell to do (keepup-93)"
ACTIONS_END = "// --- end of what a section asks the shell to do"

needs_node = pytest.mark.skipif(NODE is None, reason="needs node: the panel is JavaScript")

STAND_IN = r"""
const elements = {};
function element(id) {
  if (!elements[id]) elements[id] = {
    id, innerHTML: '', textContent: '', dataset: {}, listeners: {},
    addEventListener(type, fn) { this.listeners[type] = fn; },
    querySelector() { return element(id + ':child'); },
    appendChild() {}, remove() {}, className: '',
    querySelectorAll: () => [], insertAdjacentHTML() {},
    parentElement: { querySelectorAll: () => [], insertAdjacentHTML() {}, appendChild() {} },
  };
  return elements[id];
}
global.window = global;
global.document = {
  getElementById: element,
  createElement: (tag) => element('created:' + tag + ':' + Object.keys(elements).length),
  addEventListener(type, fn) { this.listeners = this.listeners || {}; this.listeners[type] = fn; },
  body: { appendChild() {} },
};
global.feather = { replace() {} };
global.ROLE_ADMIN = 'ADMIN';
global.currentUser = null;
const vm = require('vm');
"""


def shell_actions() -> str:
    """The shell's action dispatcher, which a section's markup now goes through."""
    source = SHELL.read_text(encoding="utf-8")
    return source[source.index(ACTIONS_START):source.index(ACTIONS_END)]


def run_node(script: str, env=None) -> str:
    return subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True,
                          env={**os.environ, **(env or {})}).stdout


@needs_node
def test_a_hostile_name_is_shown_as_text_and_never_becomes_a_handler():
    script = STAND_IN + r"""
    vm.runInThisContext(process.env.ACTIONS_JS, {filename: 'main_new.js'});
    vm.runInThisContext(require('fs').readFileSync(process.env.USERS_JS, 'utf8'));
    displayUsersList([{ id: 7, username: process.env.HOSTILE, status: 'active',
                        roles: ['CLIENT'], created_at: '2026-09-29T00:00:00Z' }]);
    const body = element('usersListBody');
    let opened = null;
    global.showUserRolesModal = (id, name) => { opened = [id, name]; };
    const button = {
        tagName: 'BUTTON', dataset: {userId: '7'},
        getAttribute: (name) => (name === 'data-action' ? 'users.roles' : null),
        closest: () => button,
        preventDefault() {},
    };
    document.listeners.click({type: 'click', target: button});
    console.log(JSON.stringify({ html: body.innerHTML, opened }));
    """
    out = json.loads(run_node(script, {"USERS_JS": str(SECTIONS / "users.js"),
                                       "ACTIONS_JS": shell_actions(),
                                       "HOSTILE": HOSTILE}))
    assert "<img" not in out["html"]
    assert "&lt;img src=x onerror=alert(1)&gt;" in out["html"]
    assert "onclick" not in out["html"]
    # The action still reaches the account, with its name as data.
    assert out["opened"] == [7, HOSTILE]


@needs_node
def test_a_notification_shows_its_message_as_text():
    shell = SHELL.read_text(encoding="utf-8")
    start = shell.index("function showNotification(")
    end = shell.index("\n}\n", start) + 3
    script = STAND_IN + r"""
vm.runInThisContext(process.env.REGION);
global.setTimeout = () => {};
let made = null;
document.createElement = () => (made = element('note'));
showNotification(process.env.HOSTILE, 'error');
console.log(JSON.stringify({ html: made.innerHTML, text: element('note:child').textContent }));
"""
    out = json.loads(run_node(script, {"REGION": shell[start:end], "HOSTILE": HOSTILE}))
    assert "<img" not in out["html"]
    assert out["text"] == HOSTILE


QUOTED_IN_HANDLER = re.compile(r"""on\w+="[^"]*'\$\{""")


@pytest.mark.parametrize("path", sorted(p for p in SECTIONS.glob("*.js")
                                        if "chart" not in p.name and "bundle" not in p.name)
                         + [SHELL], ids=lambda p: p.name)
def test_no_section_splices_data_into_an_inline_handler_as_a_string(path):
    """``onclick="f('${value}')"`` is a script built from data; use keepupJsArg or
    a data attribute."""
    offenders = [f"{path.name}:{number}" for number, line
                 in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                 if QUOTED_IN_HANDLER.search(line)]
    assert offenders == []


# --- the name at the door ------------------------------------------------------------

@pytest.mark.parametrize("name, fits", [
    ("alice", True), ("zoë.müller", True), ("a.b+c@d-e_f", True),
    (HOSTILE, False), ("with space", False), ("", False), ("x" * 65, False),
    ('quote"d', False),
])
def test_what_a_name_may_be(name, fits):
    assert is_valid_username(name) is fits


def test_a_provider_s_name_is_made_to_fit():
    assert propose_username({"preferred_username": HOSTILE}) == safe_username(HOSTILE.lower())
    assert is_valid_username(propose_username({"preferred_username": HOSTILE}))
    assert propose_username({"email": "Someone@Example.org"}) == "someone@example.org"


def test_registration_refuses_a_hostile_name():
    from pydantic import ValidationError
    from keepup.auth.routes import UserCreate
    with pytest.raises(ValidationError, match="Username may contain"):
        UserCreate(username=HOSTILE, password="a-long-password-62", agree_terms=True)
    assert UserCreate(username="renée", password="a-long-password-62", agree_terms=True)


def test_every_path_that_creates_an_account_refuses_it():
    """Whatever route or plugin creates the account, the provider's door holds."""
    from fastapi import HTTPException
    from keepup.auth.dependencies import auth_provider, save_user_to_db
    with pytest.raises(HTTPException) as refused:
        auth_provider.create_user_in_db(HOSTILE, "a-long-password-62", agree_terms=True)
    assert refused.value.status_code == 400
    with pytest.raises(HTTPException):
        save_user_to_db(HOSTILE, "a-long-password-62")
