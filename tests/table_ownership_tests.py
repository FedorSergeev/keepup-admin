"""Every table has one owner, and the map says which.

Task keepup-124. A declaration leaves the kernel's schema together with the
module that keeps it, so the move needs a map that says where each one goes --
and a map nobody checks is a map that drifts. This is that map, checked against
the declarations that actually exist.
"""

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
SCHEMA = PACKAGE / "schema.py"
OWNERSHIP = PACKAGE / "doc" / "table-ownership.md"

#: The tables owned by a capability that does not exist as a distribution yet:
#: 0.5.0 writes them (keepup-117, keepup-118, keepup-121).
LATER = {"keepup-tasks", "keepup-cluster", "keepup-modules"}


def declared_tables():
    """The table names the kernel's schema declares."""
    text = SCHEMA.read_text(encoding="utf-8")
    return set(re.findall(r"^[A-Z_]+ = tables\.table\(\s*\n\s*\"([a-z_]+)\"", text, re.M))


def ownership_map():
    """The owner of every table the document names."""
    owners = {}
    for line in OWNERSHIP.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\| `([a-z_]+)` \| `([a-z-]+)` \|", line)
        if match:
            owners[match.group(1)] = match.group(2)
    return owners


def test_every_declared_table_has_an_owner():
    """A table nobody owns is a table nobody moves."""
    owned = ownership_map()
    missing = sorted(declared_tables() - set(owned))
    assert missing == [], f"these tables have no owner in doc/table-ownership.md: {missing}"


def test_the_map_names_no_table_that_does_not_exist():
    """A row for a table that is not declared is a plan about nothing."""
    stray = sorted(set(ownership_map()) - declared_tables())
    assert stray == [], f"the map names tables that are not declared: {stray}"


def test_each_owner_is_a_distribution_or_a_named_later_one():
    """The owner is a home, not an idea."""
    distributions = {path.name for path in (PACKAGE / "packages").iterdir() if path.is_dir()}
    unknown = sorted({owner for owner in ownership_map().values()
                      if owner not in distributions and owner not in LATER})
    assert unknown == [], f"these owners are neither distributions nor named later work: {unknown}"


def test_the_tables_that_already_moved_are_not_in_the_kernel_schema():
    """Sessions, attempts, incoming requests and events are declared where they live."""
    declared = declared_tables()
    for table in ("auth_session", "login_attempts", "incoming_requests", "app_events"):
        assert table not in declared, f"{table} is declared twice: it moved already"


def test_the_document_says_why_the_later_ones_wait():
    """A reader has to know why a table stays behind."""
    text = OWNERSHIP.read_text(encoding="utf-8")
    assert "0.5.0" in text
    for owner in LATER:
        assert owner in text
