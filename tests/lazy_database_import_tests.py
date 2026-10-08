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

#: Who still takes the database while being imported, and why. The list may only
#: shrink: a module that stops needing it is struck off in the same change, and a
#: module that starts is a change to the specification first. It was thirty
#: modules when it was written, which is the real size of what keepup-127 and
#: keepup-124 have left to do -- the kernel itself (keepup/kernel) is not on it.
AT_IMPORT_TIME = {
    "audit.py": "its capability keeps its own table",
    "auth/dependencies.py": "the sign-in owns its sessions, attempts and roles",
    "auth/external_accounts.py": "the sign-in owns its sessions, attempts and roles",
    "auth/identity/access.py": "the sign-in owns its sessions, attempts and roles",
    "auth/login_throttle.py": "the sign-in owns its sessions, attempts and roles",
    "auth/oidc_routes.py": "the sign-in owns its sessions, attempts and roles",
    "auth/panel_session.py": "the sign-in owns its sessions, attempts and roles",
    "auth/providers/base.py": "the sign-in owns its sessions, attempts and roles",
    "auth/providers/local.py": "the sign-in owns its sessions, attempts and roles",
    "auth/routes.py": "the sign-in owns its sessions, attempts and roles",
    "auth/socket_sessions.py": "the sign-in owns its sessions, attempts and roles",
    "auth/user_roles.py": "the sign-in owns its sessions, attempts and roles",
    "auth/user_routes.py": "the sign-in owns its sessions, attempts and roles",
    "builtin/db.py": "it is the capability itself, which a database deployment installs",
    "builtin/postgres.py": "it is the capability itself, which a database deployment installs",
    "builtin/sqlite.py": "it is the capability itself, which a database deployment installs",
    "cluster.py": "its capability keeps its own table",
    "db.py": "it is the database, or the root that wires it in (keepup-124 moves these)",
    "events.py": "its capability keeps its own table",
    "integrations.py": "its capability keeps its own table",
    "locks.py": "its capability keeps its own table",
    "metrics.py": "its capability keeps its own table",
    "metrics_api.py": "its capability keeps its own table",
    "metrics_retention.py": "its capability keeps its own table",
    "modules.py": "its capability keeps its own table",
    "notification_bus.py": "its capability keeps its own table",
    "plugins/admin.py": "its capability keeps its own table",
    "positional_sql.py": "it is the database, or the root that wires it in (keepup-124 moves these)",
    "retention.py": "its capability keeps its own table",
    "schema.py": "it is the database, or the root that wires it in (keepup-124 moves these)",
    "tables.py": "it is the database, or the root that wires it in (keepup-124 moves these)",
    "themes.py": "its capability keeps its own table",
    "web.py": "its capability keeps its own table",
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
    unknown = sorted(name for name in known if name not in AT_IMPORT_TIME)
    assert unknown == [], (
        f"these load the database while being imported: {unknown}. Reach the "
        "manager inside the function that needs it, or say why here -- a stand "
        "whose capabilities do not include a database must be able to import the "
        "framework (keepup-127)."
    )


def test_the_list_of_modules_that_still_do_only_shrinks():
    """An entry nobody needs is a claim about the boundary that is no longer true."""
    stale = sorted(name for name in AT_IMPORT_TIME if name not in offenders())
    assert stale == [], (
        f"these no longer take the database while being imported: {stale}. Strike "
        "them off in the same change: shrinking this list is the work (keepup-127)."
    )


def test_the_kernel_takes_no_database_at_all():
    """The constructor every deployment loads is not on the list, and cannot be."""
    taking = {name for name in offenders() if name.startswith("kernel/")}
    assert taking == set(), f"the kernel loads a database: {sorted(taking)}"
