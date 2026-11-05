"""The drag of the sidebar's entries (keepup-183).

The person decides their own sequence of the menu: dragging an entry over
another moves it, and the sequence the drag left is saved for that person,
not for anybody else. The shell's region is cut out of main_new.js by its
markers and executed under a real `node` against a stand-in document: what
the menu does cannot be read off the source.

    python -m pytest tests/nav_reorder_shell_tests.py -v
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
STATIC = PACKAGE / "packages" / "keepup-http" / "keepup_http" / "static"
SHELL_JS = STATIC / "js" / "main_new.js"
SHELL_CSS = STATIC / "css" / "main_new.css"
PAGES = ("index_new.html", "index_nebula.html")
NODE = shutil.which("node")

START = "// --- the order of the menu, in the hands of the person (keepup-183)"
END = "// --- end of the order of the menu, in the hands of the person (keepup-183)"

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node: the shell is JavaScript")


PRELUDE = r"""
class FakeClassList {
    constructor(element) { this.element = element; }
    names() { return this.element.className.split(/\s+/).filter(Boolean); }
    store(names) { this.element.className = names.join(' '); }
    add(...names) {
        const all = this.names();
        names.forEach(name => { if (all.indexOf(name) === -1) all.push(name); });
        this.store(all);
    }
    remove(...names) { this.store(this.names().filter(name => names.indexOf(name) === -1)); }
    contains(name) { return this.names().indexOf(name) !== -1; }
    toggle(name, force) {
        const wanted = force === undefined ? !this.contains(name) : Boolean(force);
        if (wanted) this.add(name); else this.remove(name);
        return wanted;
    }
}

class FakeElement {
    constructor(tag, className) {
        this.tagName = String(tag || 'div').toUpperCase();
        this.className = className || '';
        this.children = [];
        this.parentElement = null;
        this.hidden = false;
        this.value = '';
        this.listeners = {};
        this.attributes = {};
        this.styleProps = {};
        this.style = {setProperty: (name, value) => { this.styleProps[name] = value; }};
        this.navReorderArmed = false;
    }
    get childNodes() { return this.children; }
    get classList() { return new FakeClassList(this); }
    appendChild(child) {
        // The real DOM moves the node: a child that is already here is
        // removed from its old place, not copied.
        if (this.children.includes(child)) this.children = this.children.filter(c => c !== child);
        child.parentElement = this;
        this.children.push(child);
        return child;
    }
    insertBefore(child, ref) {
        // The real DOM moves the node: a child that is already here is
        // removed from its old place, not copied.
        if (this.children.includes(child)) this.children = this.children.filter(c => c !== child);
        child.parentElement = this;
        if (!ref) { this.children.push(child); return child; }
        const idx = this.children.indexOf(ref);
        this.children.splice(idx === -1 ? this.children.length : idx, 0, child);
        return child;
    }
    removeChild(child) {
        this.children = this.children.filter(c => c !== child);
        child.parentElement = null;
        return child;
    }
    remove() {
        if (!this.parentElement) return;
        this.parentElement.removeChild(this);
    }
    get firstChild() { return this.children.length ? this.children[0] : null; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
    hasAttribute(name) { return name in this.attributes; }
    getBoundingClientRect() { return {top: 0, height: 40, left: 0, width: 100}; }
    addEventListener(type, handler) { (this.listeners[type] = this.listeners[type] || []).push(handler); }
    dispatch(type, event) { (this.listeners[type] || []).forEach(handler => handler(event || {})); }
}

const elements = {};
global.window = {};
global.document = {
    getElementById: id => elements[id] || null,
    createElement: tag => new FakeElement(tag, ''),
};
const storage = {};
global.localStorage = {
    getItem: key => key in storage ? storage[key] : null,
    setItem: (key, value) => { storage[key] = String(value); },
    removeItem: key => { delete storage[key]; },
};
const AppEventTarget = new EventTarget();
global.AppEventTarget = AppEventTarget;

function navItem(id, label) {
    const item = new FakeElement('a', 'nav-item');
    // A section names the page its entry opens; that is what a saved
    // sequence calls the entry.
    item.setAttribute('data-target', id);
    const text = new FakeElement('span', 'nav-text');
    text.textContent = label || id;
    item.appendChild(text);
    return item;
}

function build() {
    const root = new FakeElement('div', '');
    elements['modules-nav-container'] = root;
    const a = navItem('a', 'Alpha');
    const b = navItem('b', 'Beta');
    const c = navItem('c', 'Gamma');
    root.appendChild(a);
    root.appendChild(b);
    root.appendChild(c);
    return {root, a, b, c};
}

function orderOf(fixture) {
    // The order the entries stand in, read from the container's own children
    // in the order they stand there. An entry names the page it opens in
    // `data-target`; a menu the shell drew names the module instead.
    return fixture.root.children.map(c => c.getAttribute
        ? (c.getAttribute('data-target') || c.getAttribute('data-module-id'))
        : 'unknown');
}

//: A stand-in for the browser's own: it records the callbacks and fires them
//: when the test says a mutation happened.
class FakeMutationObserver {
    constructor(callback) { this.callback = callback; }
    observe(target) { FakeMutationObserver.observers.push({observer: this, target}); }
    disconnect() {
        FakeMutationObserver.observers =
            FakeMutationObserver.observers.filter(entry => entry.observer !== this);
    }
    static fire() {
        FakeMutationObserver.observers.slice()
            .forEach(entry => entry.observer.callback([]));
    }
}
FakeMutationObserver.observers = [];
global.MutationObserver = FakeMutationObserver;

function dragFromTo(fixture, fromId, toId, clientY) {
    const from = fixture[fromId];
    const to = fixture[toId];
    const data = {};
    from.dispatch('dragstart', {dataTransfer: {effectAllowed: '', setData: (t, v) => { data[t] = v; }}});
    to.dispatch('dragover', {preventDefault() {}, clientY: clientY, relatedTarget: null});
    to.dispatch('drop', {preventDefault() {}, clientY: clientY});
    from.dispatch('dragend', {});
    return orderOf(fixture);
}

const vm = require('vm');
vm.runInThisContext(process.env.REGION, {filename: 'main_new.js'});
"""


def region() -> str:
    source = SHELL_JS.read_text(encoding="utf-8")
    assert START in source and END in source, "the reorder region lost its markers"
    return source[source.index(START):source.index(END)]


def run(script: str):
    result = subprocess.run([NODE, "-e", PRELUDE + script], capture_output=True, text=True,
                            timeout=30, check=False,
                            env={**os.environ, "REGION": region()})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


# --- the drag moves the entry ---------------------------------------------------

def test_a_drag_onto_the_top_half_lands_before_the_target():
    shown = run("""
    const fixture = build();
    enableNavDrag();
    const order = dragFromTo(fixture, 'c', 'a', 10);
    console.log(JSON.stringify(order));
    """)
    assert shown == ["c", "a", "b"]


def test_a_drag_onto_the_bottom_half_lands_after_the_target():
    shown = run("""
    const fixture = build();
    enableNavDrag();
    const order = dragFromTo(fixture, 'a', 'c', 30);
    console.log(JSON.stringify(order));
    """)
    assert shown == ["b", "c", "a"]


def test_a_drag_to_the_same_place_changes_nothing():
    shown = run("""
    const fixture = build();
    enableNavDrag();
    const before = orderOf(fixture);
    dragFromTo(fixture, 'b', 'b', 10);
    console.log(JSON.stringify({before, after: orderOf(fixture)}));
    """)
    assert shown["before"] == ["a", "b", "c"]
    assert shown["after"] == ["a", "b", "c"]


def test_the_drag_state_is_shown_on_the_entries():
    shown = run("""
    const fixture = build();
    enableNavDrag();
    fixture.a.dispatch('dragstart', {dataTransfer: {effectAllowed: '', setData: () => {}}});
    fixture.b.dispatch('dragover', {preventDefault() {}, clientY: 10, relatedTarget: null});
    console.log(JSON.stringify({
        dragging: fixture.a.classList.contains('nav-reorder-dragging'),
        over: fixture.b.classList.contains('nav-reorder-over'),
    }));
    fixture.a.dispatch('dragend', {});
    console.log(JSON.stringify({
        after: fixture.a.classList.contains('nav-reorder-dragging'),
    }));
    """)
    # The last console.log is what run() returns.
    assert shown["after"] is False


def test_a_second_enable_does_not_arm_the_entries_twice():
    shown = run("""
    const fixture = build();
    enableNavDrag();
    enableNavDrag();
    console.log(JSON.stringify({
        armed: fixture.a.navReorderArmed,
        listeners: fixture.a.listeners['dragstart'].length,
    }));
    """)
    assert shown == {"armed": True, "listeners": 1}


# --- the person's own sequence is applied ---------------------------------------

def test_the_cached_sequence_is_applied_on_draw():
    shown = run("""
    const fixture = build();
    window.AppNavReorder.cache(['c', 'a']);
    applyUserNavOrder();
    console.log(JSON.stringify(orderOf(fixture)));
    """)
    assert shown == ["c", "a", "b"]


def test_an_id_the_catalogue_no_longer_knows_is_left_out():
    shown = run("""
    const fixture = build();
    fixture.c.remove();
    window.AppNavReorder.cache(['c', 'a']);
    applyUserNavOrder();
    console.log(JSON.stringify(orderOf(fixture)));
    """)
    assert shown == ["a", "b"]


def test_an_entry_a_section_drew_is_moved_by_its_target():
    """A section draws its own entry and names the page in `data-target`, not
    a module id: that is the shape the menu really has, and a drag has to move
    such an entry and save it under that name."""
    shown = run("""
    const root = new FakeElement('div', '');
    elements['modules-nav-container'] = root;
    const one = new FakeElement('a', 'nav-item');
    one.setAttribute('data-target', 'users-section');
    const two = new FakeElement('a', 'nav-item');
    two.setAttribute('data-target', 'metrics-section');
    const three = new FakeElement('a', 'nav-item');
    three.setAttribute('data-target', 'cluster-section');
    root.appendChild(one);
    root.appendChild(two);
    root.appendChild(three);
    enableNavDrag();
    const from = three;
    from.dispatch('dragstart', {dataTransfer: {effectAllowed: '', setData: () => {}}});
    one.dispatch('dragover', {preventDefault() {}, clientY: 10, relatedTarget: null});
    one.dispatch('drop', {preventDefault() {}, clientY: 10});
    from.dispatch('dragend', {});
    console.log(JSON.stringify({
        drawn: one.getAttribute('draggable'),
        order: orderOf({root}),
    }));
    """)
    assert shown["drawn"] == "true"
    assert shown["order"] == ["cluster-section", "users-section", "metrics-section"]


def test_an_entry_is_named_by_the_page_it_opens():
    """A section draws its entry with `data-target` and nothing else -- even
    when it fetches its data first and draws much later. That name is the one
    the sequence saves, because it is the one the entry always carries."""
    shown = run("""
    const root = new FakeElement('div', '');
    elements['modules-nav-container'] = root;
    const entry = new FakeElement('a', 'nav-item');
    entry.setAttribute('data-target', 'users-section');
    root.appendChild(entry);
    enableNavDrag();
    console.log(JSON.stringify({
        named: navCurrentOrder(root),
        name: navEntryId(entry),
    }));
    """)
    assert shown["named"] == ["users-section"]
    assert shown["name"] == "users-section"


def test_a_late_entry_is_armed_and_put_where_the_person_left_it():
    """Sections draw their entries after the shell runs; an entry that arrives
    late is armed and placed, not left out of the drag."""
    shown = run("""
    const fixture = build();
    window.AppNavReorder.cache(['c']);
    enableNavDrag();
    const late = navItem('d', 'Delta');
    fixture.root.appendChild(late);
    MutationObserver.fire();
    console.log(JSON.stringify({
        armed: late.navReorderArmed === true,
        order: orderOf(fixture),
    }));
    """)
    assert shown["armed"] is True
    assert shown["order"][0] == "c"
    assert "d" in shown["order"]


# --- the person's sequence is read from the server, once -------------------------

def test_the_servers_sequence_is_remembered_for_the_menu():
    shown = run("""
    global.fetch = () => Promise.resolve({
        ok: true,
        json: () => Promise.resolve({order: ['users-section', 'themes-section']}),
    });
    rememberStoredNavOrder().then(() => {
        console.log(JSON.stringify(readCachedNavOrder()));
    });
    """)
    assert shown == ["users-section", "themes-section"]


def test_a_sequence_the_server_no_longer_has_does_not_come_back_from_the_cache():
    shown = run("""
    cacheNavOrder(['users-section', 'themes-section']);
    global.fetch = () => Promise.resolve({
        ok: true,
        json: () => Promise.resolve({order: []}),
    });
    rememberStoredNavOrder().then(() => {
        console.log(JSON.stringify(readCachedNavOrder()));
    });
    """)
    assert shown == []


def test_the_sequence_read_from_the_server_is_what_the_menu_stands_in():
    shown = run("""
    const fixture = build();
    global.fetch = () => Promise.resolve({
        ok: true,
        json: () => Promise.resolve({order: ['c', 'a']}),
    });
    enableNavDrag();
    rememberStoredNavOrder().then(() => {
        applyUserNavOrder();
        console.log(JSON.stringify(orderOf(fixture)));
    });
    """)
    assert shown == ["c", "a", "b"]


# --- the pages and the styles the drag is made of --------------------------------

@pytest.mark.parametrize("page", PAGES)
def test_every_theme_page_keeps_the_container_a_list(page):
    markup = (STATIC / page).read_text(encoding="utf-8")
    assert 'id="modules-nav-container"' in markup
    assert 'role="list"' in markup


def test_the_theme_styles_the_drag_states():
    css = SHELL_CSS.read_text(encoding="utf-8")
    assert ".nav-item.nav-reorder-dragging" in css
    assert ".nav-item.nav-reorder-over" in css
    assert "--nav-reorder-dragging-opacity" in css
    assert "--nav-reorder-over-bg" in css
    assert ".nav-item[draggable=\"true\"]" in css


def test_the_shell_wires_the_drag_when_the_menu_is_drawn():
    source = SHELL_JS.read_text(encoding="utf-8")
    assert "enableNavDrag();" in source
    assert "rememberStoredNavOrder" in source
