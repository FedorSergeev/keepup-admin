"""The framework's schema, for a check that does not go through the kernel.

Task keepup-158. `keepup_db.schema.init_db` is gone: a deployment declares its
tables and the kernel creates them as the runtime comes up, and 42 files used to
call `init_db()` for the same thing in a test.

A test does not run the kernel's lifecycle, so it asks the plugins the same
question: every table the installed capabilities declare, discovered through the
entry points they publish rather than listed anywhere.

A module of its own, not a fixture, because a check that hands itself a database
of its own has to ask for the schema *after* it points the manager at it --
inside its own fixture, and before the first query.
"""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points


def declared_tables():
    """Every table the installed capabilities declare, discovered not listed."""
    from sqlalchemy import Table

    found = []
    for entry in entry_points(group="keepup.plugins"):
        module_name = getattr(entry, "value", "").split(":")[0]
        if not module_name or not module_name.startswith("keepup_"):
            continue
        package = module_name.split(".")[0]
        try:
            module = importlib.import_module(f"{package}.tables")
        except ModuleNotFoundError:
            continue
        for value in vars(module).values():
            if isinstance(value, Table) and value not in found:
                found.append(value)
    return found


def create_framework_tables():
    """Create every declared table in the database the manager is pointed at."""
    from keepup_db import tables
    from keepup_auth import seed_accounts

    tables.ensure_tables(*declared_tables())
    # The first accounts too: a check that signs in as `admin` needs it, and the
    # plugin that creates it at start-up never runs in a test.
    seed_accounts.create_first_accounts()
