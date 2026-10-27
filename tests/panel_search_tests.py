"""The search field above the panel's menu (keepup-179).

The menu of a full panel is longer than a screen, and finding one section means
reading it top to bottom. The field narrows the menu to entries whose text
contains what was typed, without asking the server anything; a submodule that
matches has to stay recognisable as one, even when its parent does not match.

The shell's region is cut out of main_new.js by its markers and executed under a
real `node` against a stand-in document: what the menu shows cannot be read off
the source.

    python -m pytest tests/panel_search_tests.py -v
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
STATIC = PACKAGE / "packages" / "keepup-http" / "keepup_http" / "static"
SHELL_JS = STATIC / "js" / "main_new.js"
SHELL_CSS = STATIC / "css" / "main_new.css"
NEBULA_CSS = STATIC / "css" / "main_nebula.css"
PAGES = ("index_new.html", "index_nebula.html")
NODE = shutil.which("node")

START = "// --- the search field of the sidebar"
END = "// --- end of the search field of the sidebar"

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
        this.clicked = 0;
        this.focused = false;
        this.attributes = {};
        this.text = '';
        this.styleProps = {};
        this.style = {setProperty: (name, value) => { this.styleProps[name] = value; }};
    }
    get classList() { return new FakeClassList(this); }
    set textContent(value) { this.text = String(value); }
    get textContent() {
        if (this.text) return this.text;
        return this.children.map(child => child.textContent).join(' ');
    }
    appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
    hasAttribute(name) { return name in this.attributes; }
    matches(selector) {
        return String(selector).split(',').map(part => part.trim()).some(part => {
            if (part === '.nav-item') return this.classList.contains('nav-item');
            if (part === '.nav-text') return this.classList.contains('nav-text');
            if (part === '.nav-search-match') return this.classList.contains('nav-search-match');
            if (part === '[data-nav-children]') return this.hasAttribute('data-nav-children');
            return false;
        });
    }
    querySelector(selector) {
        const found = this.querySelectorAll(selector);
        return found.length ? found[0] : null;
    }
    querySelectorAll(selector) {
        const found = [];
        const walk = node => node.children.forEach(child => {
            if (child.matches(selector)) found.push(child);
            walk(child);
        });
        walk(this);
        return found;
    }
    addEventListener(type, handler) { (this.listeners[type] = this.listeners[type] || []).push(handler); }
    dispatch(type, event) { (this.listeners[type] || []).forEach(handler => handler(event || {})); }
    click() { this.clicked += 1; this.dispatch('click'); }
    focus() { this.focused = true; }
}

const elements = {};
global.window = {};
global.document = {getElementById: id => elements[id] || null};
function element(id, tag, className) {
    const made = new FakeElement(tag, className);
    if (id) elements[id] = made;
    return made;
}
function navItem(label) {
    const item = new FakeElement('a', 'nav-item');
    const text = new FakeElement('span', 'nav-text');
    text.textContent = label;
    item.appendChild(text);
    return item;
}
function children() {
    const container = new FakeElement('div', '');
    container.setAttribute('data-nav-children', '');
    return container;
}
function build() {
    const root = element('modules-nav-container', 'div');
    const users = navItem('Users');
    const metrics = navItem('System metrics');
    const wrapper = new FakeElement('div', 'nav-group');
    const gateway = navItem('Gateway');
    const gatewayChildren = children();
    const routes = navItem('Routes');
    const routeChildren = children();
    const upstreams = navItem('Upstreams');
    element('nav-search-input', 'input');
    element('nav-search-clear', 'button').hidden = true;
    element('nav-search-empty', 'p').hidden = true;
    root.appendChild(users);
    root.appendChild(metrics);
    root.appendChild(wrapper);
    wrapper.appendChild(gateway);
    wrapper.appendChild(gatewayChildren);
    gatewayChildren.appendChild(routes);
    routes.appendChild(routeChildren);
    routeChildren.appendChild(upstreams);
    return {root, users, metrics, gateway, routes, upstreams};
}
function state(fixture) {
    return [fixture.users, fixture.metrics, fixture.gateway,
            fixture.routes, fixture.upstreams].map(item => ({
        label: item.querySelector('.nav-text').textContent.trim(),
        hidden: item.classList.contains('nav-search-hidden'),
        match: item.classList.contains('nav-search-match'),
        nested: item.classList.contains('nav-search-nested'),
        depth: Number(item.styleProps['--nav-search-depth']),
    }));
}
const vm = require('vm');
vm.runInThisContext(process.env.REGION, {filename: 'main_new.js'});
"""


def region() -> str:
    source = SHELL_JS.read_text(encoding="utf-8")
    assert START in source and END in source, "the search region lost its markers"
    return source[source.index(START):source.index(END)]


def run(script: str):
    result = subprocess.run([NODE, "-e", PRELUDE + script], capture_output=True, text=True,
                            timeout=30, check=False,
                            env={**os.environ, "REGION": region()})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


# --- what the search matches ----------------------------------------------------

def test_a_substring_narrows_the_menu_whatever_the_case():
    shown = run("""
    const fixture = build();
    const answer = applyModuleSearch('MET');
    console.log(JSON.stringify({answer, state: state(fixture)}));
    """)
    assert shown["answer"] == {"query": "met", "matches": 1}
    assert shown["state"][1] == {"label": "System metrics", "hidden": False, "match": True,
                                 "nested": False, "depth": 0}
    assert [item["hidden"] for item in shown["state"]] == [True, False, True, True, True]


def test_the_query_is_trimmed_before_it_is_compared():
    shown = run("""
    const fixture = build();
    console.log(JSON.stringify(applyModuleSearch('  system  ')));
    """)
    assert shown == {"query": "system", "matches": 1}


def test_the_search_reads_the_entry_and_not_its_icon():
    shown = run("""
    const fixture = build();
    const answer = applyModuleSearch('feather');
    console.log(JSON.stringify({answer, state: state(fixture)}));
    """)
    assert shown["answer"] == {"query": "feather", "matches": 0}


def test_a_search_that_matches_nothing_says_so():
    shown = run("""
    const fixture = build();
    const answer = applyModuleSearch('zzz');
    console.log(JSON.stringify({
        answer, state: state(fixture),
        empty: {hidden: document.getElementById('nav-search-empty').hidden,
                text: document.getElementById('nav-search-empty').textContent},
        clear: document.getElementById('nav-search-clear').hidden,
    }));
    """)
    assert shown["answer"]["matches"] == 0
    assert shown["empty"] == {"hidden": False, "text": 'No modules match "zzz"'}
    assert shown["clear"] is False
    assert all(item["hidden"] for item in shown["state"])


def test_an_empty_search_puts_the_whole_menu_back():
    shown = run("""
    const fixture = build();
    applyModuleSearch('zzz');
    console.log(JSON.stringify({
        answer: applyModuleSearch('   '),
        state: state(fixture),
        empty: document.getElementById('nav-search-empty').hidden,
        clear: document.getElementById('nav-search-clear').hidden,
    }));
    """)
    assert shown["answer"] == {"query": "", "matches": 0}
    assert shown["empty"] is True and shown["clear"] is True
    assert all(not item["hidden"] and not item["match"] and not item["nested"]
               for item in shown["state"])


# --- a submodule keeps its shape --------------------------------------------------

def test_a_matching_submodule_stands_alone_indented():
    """The parent's text does not match, so it goes; the child stays, indented.

    The two live in a wrapper whose children container is a sibling of the
    parent's link -- the shape a menu uses for a submenu.
    """
    shown = run("""
    const fixture = build();
    const answer = applyModuleSearch('routes');
    console.log(JSON.stringify({answer, state: state(fixture)}));
    """)
    assert shown["answer"]["matches"] == 1
    gateway, routes, upstreams = shown["state"][2], shown["state"][3], shown["state"][4]
    assert gateway["hidden"] is True, "the parent's own text did not match"
    assert routes == {"label": "Routes", "hidden": False, "match": True,
                      "nested": True, "depth": 1}
    assert upstreams["hidden"] is True


def test_a_match_inside_a_parent_keeps_the_parent_on_screen():
    """Hiding the parent would take the child with it: they share a subtree."""
    shown = run("""
    const fixture = build();
    applyModuleSearch('upstreams');
    console.log(JSON.stringify(state(fixture)));
    """)
    routes, upstreams = shown[3], shown[4]
    assert routes["hidden"] is False, "the match lives inside this entry"
    assert routes["match"] is False, "it is a path, not a result"
    assert upstreams == {"label": "Upstreams", "hidden": False, "match": True,
                         "nested": True, "depth": 2}


def test_a_top_level_match_is_not_marked_as_a_submodule():
    shown = run("""
    const fixture = build();
    applyModuleSearch('users');
    console.log(JSON.stringify(state(fixture)[0]));
    """)
    assert shown == {"label": "Users", "hidden": False, "match": True,
                     "nested": False, "depth": 0}


def test_an_entry_added_after_the_search_is_searched_too():
    """Sections add their entries as they initialise, which can be later."""
    shown = run("""
    const fixture = build();
    applyModuleSearch('billing');
    const billing = navItem('Billing');
    fixture.root.appendChild(billing);
    const answer = applyModuleSearch('billing');
    console.log(JSON.stringify({answer, hidden: billing.classList.contains('nav-search-hidden')}));
    """)
    assert shown == {"answer": {"query": "billing", "matches": 1}, "hidden": False}


def test_labels_outside_ascii_match_in_any_case():
    """A deployment names its sections in its own language, and the search folds
    that case too. The letters are escapes because the framework is checked for
    text a reader may not have (tests/distribution_tests.py)."""
    shown = run("""
    const fixture = build();
    fixture.users.querySelector('.nav-text').textContent =
        '\\u041f\\u043e\\u043b\\u044c\\u0437\\u043e\\u0432\\u0430\\u0442\\u0435\\u043b\\u0438';
    fixture.metrics.querySelector('.nav-text').textContent =
        '\\u0416\\u0443\\u0440\\u043d\\u0430\\u043b \\u0438\\u043d\\u0442\\u0435\\u0433\\u0440\\u0430\\u0446\\u0438\\u0439';
    const answer = applyModuleSearch(' \\u0418\\u041d\\u0422\\u0415\\u0413\\u0420\\u0410\\u0426 ');
    console.log(JSON.stringify({answer, state: state(fixture)}));
    """)
    assert shown["answer"]["matches"] == 1
    assert shown["state"][0]["hidden"] is True
    assert shown["state"][1]["hidden"] is False


# --- the field's own controls -----------------------------------------------------

def test_escape_clears_and_enter_opens_the_first_match():
    shown = run("""
    const fixture = build();
    setupModuleSearch();
    const input = document.getElementById('nav-search-input');
    const prevented = [];
    input.value = 'routes';
    input.dispatch('input');
    const filtered = state(fixture)[3];
    input.dispatch('keydown', {key: 'Enter', preventDefault: () => prevented.push('enter')});
    const clicked = fixture.routes.clicked;
    input.dispatch('keydown', {key: 'Escape', preventDefault: () => prevented.push('escape')});
    console.log(JSON.stringify({
        filtered, clicked, prevented, value: input.value,
        after: state(fixture).map(item => item.hidden),
    }));
    """)
    assert shown["filtered"]["hidden"] is False
    assert shown["clicked"] == 1, "Enter opens the first match"
    assert shown["prevented"] == ["enter", "escape"]
    assert shown["value"] == ""
    assert shown["after"] == [False] * 5


def test_escape_does_nothing_on_an_empty_search():
    shown = run("""
    const fixture = build();
    setupModuleSearch();
    const input = document.getElementById('nav-search-input');
    const prevented = [];
    input.dispatch('keydown', {key: 'Escape', preventDefault: () => prevented.push('escape')});
    console.log(JSON.stringify(prevented));
    """)
    assert shown == []


def test_the_clear_button_empties_the_field_and_returns_focus():
    shown = run("""
    const fixture = build();
    setupModuleSearch();
    const input = document.getElementById('nav-search-input');
    const clear = document.getElementById('nav-search-clear');
    input.value = 'metrics';
    input.dispatch('input');
    clear.dispatch('click');
    console.log(JSON.stringify({
        value: input.value, focused: input.focused,
        clearHidden: clear.hidden,
        hidden: state(fixture).map(item => item.hidden),
    }));
    """)
    assert shown == {"value": "", "focused": True, "clearHidden": True,
                     "hidden": [False] * 5}


def test_the_public_hook_answers_the_same_way_the_field_does():
    shown = run("""
    const fixture = build();
    console.log(JSON.stringify({
        answer: window.AppNavSearch.apply('metrics'),
        cleared: window.AppNavSearch.clear().query,
        label: window.AppNavSearch.label(fixture.routes),
        depth: window.AppNavSearch.depth(fixture.upstreams, fixture.root),
    }));
    """)
    assert shown == {"answer": {"query": "metrics", "matches": 1}, "cleared": "",
                     "label": "Routes", "depth": 2}


# --- the pages and the styles the search is made of --------------------------------

@pytest.mark.parametrize("page", PAGES)
def test_every_theme_page_carries_the_field_above_the_menu(page):
    markup = (STATIC / page).read_text(encoding="utf-8")
    for name in ("nav-search", "nav-search-input", "nav-search-clear", "nav-search-empty"):
        assert f'id="{name}"' in markup, f"{page} has no {name}"
    assert 'aria-label="Search modules"' in markup
    assert 'aria-controls="modules-nav-container"' in markup
    assert 'role="search"' in markup
    # The field is chrome above the menu, not one of the entries the menu lists.
    assert markup.index('id="nav-search"') < markup.index('id="modules-nav-container"')
    assert markup.index('id="main-nav"') < markup.index('id="nav-search"')
    # A Content-Security-Policy serves the panel; an inline handler is refused.
    block = markup[markup.index('id="nav-search"'):markup.index('id="modules-nav-container"')]
    assert not re.search(r"\son(click|input|change|keydown|keyup|search)=", block)


@pytest.mark.parametrize("page", PAGES)
def test_the_field_is_not_part_of_the_scrolling_list(page):
    """Entries scroll under the field; the field stays where it was put."""
    markup = (STATIC / page).read_text(encoding="utf-8")
    container = markup.index('id="modules-nav-container"')
    nav = markup.index('id="main-nav"')
    field = markup.index('id="nav-search"')
    assert nav < field < container
    assert markup.count('id="nav-search-input"') == 1


def test_the_shell_wires_the_field_when_the_panel_starts():
    source = SHELL_JS.read_text(encoding="utf-8")
    assert "setupModuleSearch();" in source
    assert "KeepupActions" not in region(), "the field binds its own events; no action is needed"
    # Collapsing the menu takes the field away, so the filter goes with it.
    assert "if (isCollapsed) clearModuleSearch();" in source


def test_the_theme_styles_the_search_and_the_indent():
    css = SHELL_CSS.read_text(encoding="utf-8")
    for selector in (".nav-search {", ".nav-search-input", ".nav-search-clear",
                     ".nav-search-empty", ".nav-item.nav-search-hidden",
                     ".nav-item.nav-search-match", ".nav-item.nav-search-nested::before"):
        assert selector in css, f"{selector} is not in the shared stylesheet"
    assert "--nav-search-depth" in css
    assert "var(--nav-item-search-step" in css
    # The collapsed menu has no room for the field and nothing to type into.
    assert ":root:not(.is-narrow) .sidebar-collapsed .nav-search" in css


def test_the_second_theme_overrides_the_tokens_rather_than_the_rules():
    css = NEBULA_CSS.read_text(encoding="utf-8")
    assert "--nav-search-border:" in css
    assert "--nav-search-border-focus:" in css
    assert "--nav-item-search-base:" in css
    # The drawer rules and the field's layout stay in the shared stylesheet.
    assert ".nav-search {" not in css
