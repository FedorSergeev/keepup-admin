# The search above the panel's menu

The menu of a full panel is longer than a screen. Every section that a role is
granted adds its own entry, and the entries arrive from different places: some
from the catalogue the panel was started with, some from the script a section
runs when it initialises. A person looking for one of them reads the menu top to
bottom, so the shell gives the menu a field instead.

The field is a plain substring filter over what the menu shows. It asks the
server nothing, stores nothing and keeps no state of its own: the query lives in
the input, and every keystroke re-reads the menu.

## What it looks like

The field sits above `#modules-nav-container`, inside `#main-nav`, so the
entries scroll under it and the field stays where it was put. It carries the
theme's chrome: a magnifier, a placeholder, a clear button that appears only
while there is something to clear, and a status line under the field for the
case where nothing matches.

Both themes ship it — `main_new.css` declares the shared rules and the tokens,
`main_nebula.css` overrides only the values, the way it does for the rest of the
chrome. Collapsed, the wide menu hides the field: there is no room for it and
nothing to type into, and the shell clears the filter as it goes down so no
section stays hidden behind a field nobody can see.

## What a search keeps

An entry whose text contains the query stays. Its text is the `.nav-text` label,
not the whole line: the icon, the marker and the paddings are not searchable
text, and neither is a section's `description` — the menu never showed it.

Matching is case-insensitive and trims the query at both ends, so ` MET `, `met`
and `Met` are one search. The comparison is `indexOf` on the lowercased label,
not a regular expression: a bracket or a dot in the query is a character, not
syntax.

An entry either matches, or is kept as the path to a match that lives inside it.
Hiding the parent of a match would hide the match with it, because a submenu is
part of the parent's subtree. A parent kept this way is not marked as a result,
so the eye goes to the match.

## Where the match is

A match is marked where it matched: the substring is wrapped in a `<mark>`
inside the entry's own label, in the case the label was written in, and the rest
of the label — an icon, a badge — is put back as it was. The label of a parent
kept only as the path to a match is not marked; the mark says "this is why the
row is here", and a row that is here for its child has no such reason.

Marking rewrites the label, so the search keeps the label's original nodes in a
`WeakMap` and puts them back before every search. That is what stops a mark from
a previous query surviving into the next one, and what leaves a cleared field
with exactly the labels the sections drew.

## The arrows

The field is a filter with a keyboard. `ArrowDown` and `ArrowUp` walk the matches
on screen, wrapping at both ends, and the row they are on gets
`nav-search-current` — a ring rather than a fill, because hover already claims
the fill — and is scrolled into view. `Enter` opens that row; with no arrow
pressed it opens the first result, which is what it did before the arrows
existed.

A new query drops the arrow's place: the row was chosen among results that no
longer exist, and opening it because the letters changed would be a surprise.
The same happens when the current match is filtered out by a narrowing query.

## A submodule in the results

A submenu is drawn from the same `.nav-item` elements as the top level, and a
search that shows a submodule without its parent has to keep it readable as a
submodule. An entry's depth is how deeply it sits in the menu:

- an entry inside another entry — a `.nav-item` inside a `.nav-item`;
- an entry inside a container the parent keeps for its children — an element
  with `data-nav-children`;
- both counts taken as the deeper of the two, so a submenu whose container sits
  inside the parent's own link is one level, not two.

A matched submodule is indented by its depth
(`--nav-item-search-base` plus `--nav-item-search-step` per level) and carries a
small dot before its icon. A match at the top level is not indented and has no
dot. The indentation is what makes a result shown on its own — parent hidden,
because its own text does not match — read as a sub-item rather than as a new
section.

An application that adds a submenu sets the container's `data-nav-children`
attribute and the search finds the level without being told anything else.

## The keys

`Esc` clears the field and shows the whole menu; the clear button does the same
and puts the focus back in the field. `Enter` opens the row the arrows are on,
or the first result when they were not used: the shell clicks that entry, so
whichever handler the section bound runs exactly as if it had been clicked.

The field binds its own listeners in `main_new.js`; the markup carries no inline
handler, because the panel is served with a Content-Security-Policy that refuses
one. The status line is filled with `textContent`, so a query full of markup is
shown as typed.

## Where the code lives

- `keepup_http/static/index_new.html`, `static/index_nebula.html` — the field
  and its status line;
- `static/js/main_new.js`, the region between `the search field of the sidebar`
  markers — filtering, depth, the keys, and `window.AppNavSearch` for what a
  section or a test may ask of the field;
- `static/css/main_new.css` — the rules and the tokens: the field, the indent of
  a submodule, the mark of a match (`--nav-search-hit-*`) and the row the arrows
  are on (`--nav-search-current-*`);
- `static/css/main_nebula.css` — the second theme's values for those tokens;
- `tests/panel_search_tests.py` — the region run under `node` against a
  stand-in document, and the pages and stylesheets read as data.
