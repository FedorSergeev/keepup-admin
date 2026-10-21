"""The panel's half of the second factor (keepup-176).

The server stopped handing out a session on a password alone in keepup-174; the
shell still called every 200 a sign-in, set its marker and then asked /me
without a cookie. These checks run the account screen's script under a real
`node`: what it sends, and what the shell does with a challenge, is read from
the code rather than assumed.

    python -m pytest tests/mfa_panel_tests.py -v
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
STATIC = PACKAGE / "packages" / "keepup-http" / "keepup_http" / "static"
MFA_JS = STATIC / "js" / "mfa_panel.js"
SHELL_JS = STATIC / "js" / "main_new.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node: the panel is JavaScript")

PRELUDE = r"""
global.window = global;
global.document = {
    readyState: 'complete',
    getElementById: () => null,
    createElement: (tag) => ({tag, className: '', textContent: '', children: [],
                              appendChild(child) { this.children.push(child); },
                              setAttribute() {}, addEventListener() {}}),
    addEventListener() {},
    querySelector: () => null,
    body: {appendChild() {}},
};
global.fetch = async () => ({ok: true, status: 200, json: async () => ({})});
const vm = require('vm');
vm.runInThisContext(require('fs').readFileSync(process.env.MFA_JS, 'utf8'));
"""


def run(script: str):
    result = subprocess.run([NODE, "-e", PRELUDE + script], capture_output=True, text=True,
                            timeout=30, env={**os.environ, "MFA_JS": str(MFA_JS)})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_six_digit_code_and_a_recovery_code_are_named_differently():
    answered = run("""
    console.log(JSON.stringify([
        MfaPanel.payloadFor('123456'),
        MfaPanel.payloadFor(' AB12-CD34 '),
        MfaPanel.payloadFor(''),
    ]));
    """)
    assert answered == [{"code": "123456"}, {"recovery_code": "AB12-CD34"}, None]


def test_the_code_is_verified_against_the_challenge_the_password_earned():
    answered = run("""
    let sent = null;
    global.fetch = async (url, options) => {
        sent = {url, body: JSON.parse(options.body)};
        return {ok: true, status: 200, json: async () => ({access_token: 'a', expires_in: 3600})};
    };
    MfaPanel.showChallenge('challenge-1');
    MfaPanel.verify('123456').then((result) => {
        console.log(JSON.stringify({sent, result}));
    });
    """)
    assert answered["sent"]["url"] == "/api/auth/mfa/verify"
    assert answered["sent"]["body"] == {"mfa_token": "challenge-1", "code": "123456"}
    assert answered["result"]["ok"] is True


def test_a_recovery_code_travels_as_one():
    answered = run("""
    let body = null;
    global.fetch = async (url, options) => {
        body = JSON.parse(options.body);
        return {ok: true, status: 200, json: async () => ({access_token: 'a'})};
    };
    MfaPanel.showChallenge('challenge-2');
    MfaPanel.verify('AB12-CD34').then(() => console.log(JSON.stringify(body)));
    """)
    assert answered == {"mfa_token": "challenge-2", "recovery_code": "AB12-CD34"}


def test_an_empty_code_is_not_sent_at_all():
    answered = run("""
    let called = false;
    global.fetch = async () => { called = true; };
    MfaPanel.showChallenge('challenge-3');
    MfaPanel.verify('').then((result) => console.log(JSON.stringify({called, status: result.status})));
    """)
    assert answered == {"called": False, "status": 400}


def test_the_shell_does_not_call_a_challenge_a_session():
    source = SHELL_JS.read_text(encoding="utf-8")
    assert "if (data.mfa_required)" in source
    assert "MfaPanel.showChallenge(data.mfa_token)" in source
    assert "await finishSignIn(data)" in source
    assert "if (!data || !data.access_token)" in source, \
        "a response without a session must not set the marker"


def test_the_shell_verifies_the_code_through_the_same_form():
    source = SHELL_JS.read_text(encoding="utf-8")
    assert "MfaPanel.challengeToken()" in source
    assert "MfaPanel.verify(code" in source
    assert "finishSignIn(result.data)" in source


def test_both_pages_load_the_account_screen():
    for name in ("index_new.html", "index_nebula.html"):
        page = (STATIC / name).read_text(encoding="utf-8")
        assert (page.index('src="/keepup-static/js/mfa_panel.js"')
                < page.index('src="/keepup-static/js/main_new.js"')), name


DOM_PRELUDE = r"""
const elements = {};
function classes(element) {
    const set = new Set();
    return {
        add: (...names) => names.forEach(name => set.add(name)),
        remove: (...names) => names.forEach(name => set.delete(name)),
        contains: (name) => set.has(name),
        toggle: (name, force) => {
            const on = force === undefined ? !set.has(name) : force;
            if (on) set.add(name); else set.delete(name);
        },
    };
}
function node(tag) {
    const element = {
        tag, children: [], className: '', classList: classes(),
        listeners: {}, _id: '', focused: false,
        set id(value) { this._id = value; elements[value] = this; },
        get id() { return this._id; },
        appendChild(child) { this.children.push(child); return child; },
        insertAdjacentElement(position, child) { this.inserted = child; return child; },
        setAttribute() {},
        addEventListener(type) { this.listeners[type] = true; },
        querySelector() { return this.submit || null; },
        closest() { return this.wrapper || null; },
        focus() { this.focused = true; },
        replaceChildren(...values) { this.children = values; },
    };
    return element;
}
const form = node('form'); form.submit = node('button');
const username = node('input'); username.wrapper = node('div');
const password = node('input'); password.wrapper = node('div');
elements.loginForm = form;
elements.loginUsername = username;
elements.loginPassword = password;
global.window = global;
global.document = {
    readyState: 'complete',
    getElementById: (id) => elements[id] || null,
    createElement: (tag) => node(tag),
    addEventListener() {},
    querySelector: () => null,
    body: {appendChild() {}},
};
global.fetch = async () => ({ok: true, status: 200, json: async () => ({})});
const vm = require('vm');
vm.runInThisContext(require('fs').readFileSync(process.env.MFA_JS, 'utf8'));
"""


def run_dom(script: str):
    result = subprocess.run([NODE, "-e", DOM_PRELUDE + script], capture_output=True, text=True,
                            timeout=30, env={**os.environ, "MFA_JS": str(MFA_JS)})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_form_grows_a_code_field_and_gives_it_back():
    answered = run_dom("""
    MfaPanel.showChallenge('challenge-4');
    const asked = {
        usernameHidden: username.wrapper.classList.contains('hidden'),
        passwordHidden: password.wrapper.classList.contains('hidden'),
        blockShown: !elements.mfaStep.classList.contains('hidden'),
        focused: elements.loginCode.focused,
        label: form.submit.textContent,
    };
    MfaPanel.clearChallenge();
    const back = {
        usernameHidden: username.wrapper.classList.contains('hidden'),
        passwordHidden: password.wrapper.classList.contains('hidden'),
        blockShown: !elements.mfaStep.classList.contains('hidden'),
        label: form.submit.textContent,
        token: MfaPanel.challengeToken(),
    };
    console.log(JSON.stringify({asked, back}));
    """)
    assert answered["asked"] == {"usernameHidden": True, "passwordHidden": True,
                                  "blockShown": True, "focused": True, "label": "Verify"}
    assert answered["back"] == {"usernameHidden": False, "passwordHidden": False,
                                "blockShown": False, "label": "Sign in", "token": None}
