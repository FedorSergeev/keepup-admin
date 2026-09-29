"""The panel shell hears only its own origin (keepup-77).

The shell's `message` handler acted on whatever any window posted: any site the
user had open could make the panel announce a payment and reload a section.
It now takes messages from the page's own origin only and hands them on as a
`keepup:message` event. The handler is run here by a real `node`, cut out of
the shell.

    python3 -m pytest keepup/tests/window_message_tests.py -v
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SHELL = Path(__file__).resolve().parents[1] / "static" / "js" / "main_new.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node: the panel is JavaScript")

HARNESS = r"""
const heard = [], shown = [], reloaded = [];
global.window = global;
window.location = { origin: 'https://panel.example' };
window.dispatchEvent = (event) => heard.push(event.detail);
global.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init.detail; } };
global.showNotification = (text) => shown.push(text);
global.setTimeout = (fn) => fn();
window.addEventListener = () => {};
if (process.env.WITH_SECTION) window.loadTariffsData = () => reloaded.push(true);
require('vm').runInThisContext(process.env.REGION);
for (const event of JSON.parse(process.env.EVENTS)) keepupHandleWindowMessage(event);
console.log(JSON.stringify({ heard, shown, reloaded: reloaded.length }));
"""


def run(events, with_section=True):
    shell = SHELL.read_text(encoding="utf-8")
    start = shell.index("function keepupHandleWindowMessage(")
    end = shell.index("window.addEventListener('message', keepupHandleWindowMessage);")
    env = {"REGION": shell[start:end], "EVENTS": json.dumps(events),
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    if with_section:
        env["WITH_SECTION"] = "1"
    out = subprocess.run([NODE, "-e", HARNESS], capture_output=True, text=True,
                         check=True, env=env).stdout
    return json.loads(out)


PAYMENT = {"type": "payment_success"}


def test_another_site_is_not_heard():
    result = run([{"origin": "https://evil.example", "data": PAYMENT}])
    assert result == {"heard": [], "shown": [], "reloaded": 0}


def test_the_own_origin_is_heard_and_handed_on():
    result = run([{"origin": "https://panel.example", "data": {"type": "section_ready", "id": 7}}])
    assert result["heard"] == [{"type": "section_ready", "id": 7}]


def test_a_payment_from_the_own_origin_still_reloads_the_section():
    result = run([{"origin": "https://panel.example", "data": PAYMENT}])
    assert result["shown"] and result["reloaded"] == 1


def test_without_the_section_a_payment_message_does_not_throw():
    result = run([{"origin": "https://panel.example", "data": PAYMENT}], with_section=False)
    assert result["reloaded"] == 0


@pytest.mark.parametrize("data", [None, "payment_success", 42, {"no": "type"}])
def test_what_is_not_a_message_is_ignored(data):
    assert run([{"origin": "https://panel.example", "data": data}])["heard"] == []
