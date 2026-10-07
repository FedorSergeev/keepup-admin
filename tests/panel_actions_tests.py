"""Markup names an action; the shell runs only what was registered (keepup-93).

A section used to build JavaScript into its markup, which the panel's
Content-Security-Policy refuses. It now calls KeepupActions.register() and puts
the action's name in a data attribute. What the panel decides cannot be read off
the source, so one region of the shell is executed here by a real `node`.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SHELL = Path(__file__).resolve().parents[1] / "static" / "js" / "main_new.js"
ACTIONS_START = "// --- what a section asks the shell to do (keepup-93)"
ACTIONS_END = "// --- end of what a section asks the shell to do"
NODE = shutil.which("node")

PRELUDE = r"""
global.window = global;
const listeners = {};
global.document = {
    listeners: listeners,
    addEventListener(type, fn) { listeners[type] = fn; },
};
const vm = require('vm');
vm.runInThisContext(process.env.REGION, { filename: 'main_new.js' });

// A stand-in for the element the browser would hand the listener: closest()
// finds the element that carries the action, getAttribute reads it, dataset
// carries the values.
function named(action, dataset, tagName) {
    const element = {
        tagName: tagName || 'BUTTON',
        dataset: dataset || {},
        getAttribute: (name) => (name === 'data-action' ? action : null),
        closest: () => element,
    };
    return element;
}
"""


def actions_region() -> str:
    source = SHELL.read_text(encoding="utf-8")
    return source[source.index(ACTIONS_START):source.index(ACTIONS_END)]


def run_in_node(script: str):
    result = subprocess.run([NODE, "-e", PRELUDE + script], capture_output=True, text=True,
                            timeout=30, env={**os.environ, "REGION": actions_region()})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


needs_node = pytest.mark.skipif(NODE is None, reason="needs node: the shell is JavaScript")


@needs_node
def test_a_registered_action_runs_with_its_element_and_the_event():
    answered = run_in_node("""
    const calls = [];
    KeepupActions.register({'demo.run': (element, event) => calls.push([element.dataset.id, event.type])});
    const button = named('demo.run', {id: '7'});
    document.listeners.click({type: 'click', target: button});
    console.log(JSON.stringify(calls));
    """)
    assert answered == [["7", "click"]]


@needs_node
def test_an_action_nothing_registered_runs_nothing():
    """Markup can name an action, never a function."""
    answered = run_in_node("""
    let ran = false;
    global.alert = () => { ran = true; };
    document.listeners.click({type: 'click', target: named('alert', {})});
    document.listeners.click({type: 'click', target: named('demo.missing', {})});
    console.log(JSON.stringify(ran));
    """)
    assert answered is False


@needs_node
def test_a_click_on_an_element_without_an_action_is_left_alone():
    answered = run_in_node("""
    let ran = false;
    KeepupActions.register({'demo.run': () => { ran = true; }});
    document.listeners.click({type: 'click', target: {closest: () => null}});
    document.listeners.click({type: 'click', target: null});
    console.log(JSON.stringify(ran));
    """)
    assert answered is False


@needs_node
def test_a_form_is_answered_rather_than_sent():
    answered = run_in_node("""
    let ran = false;
    KeepupActions.register({'demo.save': () => { ran = true; }});
    const event = {type: 'submit', target: named('demo.save', {}, 'FORM'),
                   prevented: false, preventDefault() { this.prevented = true; }};
    document.listeners.submit(event);
    console.log(JSON.stringify({ran: ran, prevented: event.prevented}));
    """)
    assert answered == {"ran": True, "prevented": True}


@needs_node
def test_a_link_that_names_an_action_is_a_button():
    answered = run_in_node("""
    let ran = false;
    KeepupActions.register({'demo.open': () => { ran = true; }});
    const event = {type: 'click', target: named('demo.open', {}, 'A'),
                   prevented: false, preventDefault() { this.prevented = true; }};
    document.listeners.click(event);
    console.log(JSON.stringify({ran: ran, prevented: event.prevented}));
    """)
    assert answered == {"ran": True, "prevented": True}


@needs_node
def test_a_click_on_a_form_control_waits_for_its_own_event():
    """An action on a form or a field belongs to the submit or the change.

    The click that a submit button also produces would otherwise run the same
    handler twice, and a select would be read before it was answered.
    """
    answered = run_in_node("""
    const calls = [];
    KeepupActions.register({'demo.save': () => calls.push('save'),
                            'demo.pick': () => calls.push('pick')});
    const form = named('demo.save', {}, 'FORM');
    const button = {tagName: 'BUTTON', dataset: {}, closest: () => form};
    document.listeners.click({type: 'click', target: button});
    const select = named('demo.pick', {}, 'SELECT');
    document.listeners.click({type: 'click', target: select});
    document.listeners.change({type: 'change', target: select});
    document.listeners.submit({type: 'submit', target: form, preventDefault() {}});
    console.log(JSON.stringify(calls));
    """)
    assert answered == ["pick", "save"]


@needs_node
def test_a_change_names_the_action_like_a_click():
    answered = run_in_node("""
    const seen = [];
    KeepupActions.register({'demo.changed': (element) => seen.push(element.dataset.field)});
    document.listeners.change({type: 'change', target: named('demo.changed', {field: 'cpu'}, 'SELECT')});
    console.log(JSON.stringify(seen));
    """)
    assert answered == ["cpu"]
