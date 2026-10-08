"""Importing the framework must not load a database.

Task keepup-127. The release exists so that the base package carries no database
library it does not use; an import at the top of a module defeats that quietly.
Two reasons this is checked rather than assumed: a module that takes the manager
at module level loads SQLAlchemy -- and, once the manager lives in `keepup-db`,
loads the distribution -- for every deployment that merely mentions it, and
several modules are careful about exactly this (`events_split_tests.py`,
`log_shipping_split_tests.py`, `metrics_split_tests.py`, `themes_tests.py`).

The manager is reached inside the function that needs it. A module that really
has to hold it at import time belongs on the list below, with its reason.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]

#: Modules that declare tables, so they need SQLAlchemy to be imported at all.
#: They cannot be made lazy: their resolution is that they *move* into the
#: distribution of the capability they belong to, with their declarations
#: (keepup-124). Named here so the reason is on the record rather than in
#: somebody's head.
MOVES_WITH_ITS_CAPABILITY = {
    "audit.py": "keepup-audit",
    "builtin/db.py": "keepup-db",
    "builtin/postgres.py": "keepup-postgres",
    "builtin/sqlite.py": "keepup-sqlite",
    "schema.py": "the declarations themselves, until each owner takes its own",
    "themes.py": "keepup-ui",
}

#: Modules whose import can be made lazy here and now: they reach the database
#: only inside functions, so the import belongs there. This is keepup-127's own
#: work, and it is finite -- nine of them.
CAN_BE_MADE_LAZY = {
}

#: What "takes the database" means.
TAKES = ("keepup.db", "sqlalchemy")


def framework_files():
    """Every Python file of the framework, checks and artifacts aside."""
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = str(path.relative_to(PACKAGE))
        parts = relative.split("/")
        if any(part.startswith(".") or part in
               ("tests", "ci", "openspec", "doc", "packages", "build", "__pycache__")
               for part in parts):
            continue
        yield path, relative


def top_level_database_imports(path):
    """The database imports a module makes while being imported."""
    found = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names
                         if any(alias.name == take or alias.name.startswith(take + ".")
                                for take in TAKES))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if any(node.module == take or node.module.startswith(take + ".")
                   for take in TAKES):
                found.add(node.module)
        elif isinstance(node, ast.Try):
            # A guarded import is still an import that happens.
            for inner in node.body:
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    name = getattr(inner, "module", None) or ""
                    if not name and isinstance(inner, ast.Import):
                        name = inner.names[0].name
                    if any(name == take or name.startswith(take + ".")
                           for take in TAKES):
                        found.add(name)
    return found


def owed():
    """What keepup-127 still owes: the modules that can be made lazy."""
    return CAN_BE_MADE_LAZY


def offenders():
    """Every module that takes the database while being imported, by name."""
    found = {}
    for path, relative in framework_files():
        taking = top_level_database_imports(path)
        if taking:
            found[relative] = sorted(taking)
    return found


def test_only_the_named_modules_take_the_database_while_being_imported():
    """A new one is a deployment that loads SQLAlchemy to import a helper."""
    known = offenders()
    known_names = set(MOVES_WITH_ITS_CAPABILITY) | set(CAN_BE_MADE_LAZY)
    unknown = sorted(name for name in known if name not in known_names)
    assert unknown == [], (
        f"these load the database while being imported: {unknown}. Reach the "
        "manager inside the function that needs it, or say why here -- a stand "
        "whose capabilities do not include a database must be able to import the "
        "framework (keepup-127)."
    )


def test_the_debt_of_keepup_127_is_nine_modules_and_only_shrinks():
    """The work this task owns, told apart from the work another task owns.

    `keepup-127` is "make the import lazy"; a module that declares tables cannot
    have a lazy import, and its answer is that it moves into a distribution
    (keepup-124). Keeping the two in one list made this task look unclosable when
    it is simply two jobs.
    """
    assert len(CAN_BE_MADE_LAZY) <= 9, "the debt of 127 grows by decision, not by accident"
    listed = set(MOVES_WITH_ITS_CAPABILITY) | set(CAN_BE_MADE_LAZY)
    stale = sorted(name for name in listed if name not in offenders())
    assert stale == [], (
        f"these no longer take the database while being imported: {stale}. Strike "
        "them off in the same change: shrinking this list is the work (keepup-127)."
    )


def test_the_kernel_takes_no_database_at_all():
    """The constructor every deployment loads is not on the list, and cannot be."""
    taking = {name for name in offenders() if name.startswith("kernel/")}
    assert taking == set(), f"the kernel loads a database: {sorted(taking)}"
