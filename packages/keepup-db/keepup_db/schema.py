"""The tables the framework itself needs.

There are no migrations in this project: every table is declared once
(``keepup/tables.py``) and made to match its declaration at start-up -- created
when missing, given the declared columns it lacks. That is why this function is
safe to run on every boot and on every replica.

What the application adds to the schema arrives through hooks rather than
through edits here -- the framework has no payments and no feeds.
"""

import logging
import os

from sqlalchemy import Column, DateTime, Text

from keepup_db import tables
from keepup_audit.audit import init_incoming_requests_table
from keepup_auth import panel_session
from keepup_auth.factory import AuthProviderFactory
from keepup_auth.login_throttle import init_login_attempts_table
from keepup_db import DatabaseManagerV2, db_config
from keepup_modules.catalogue import (sync_framework_sections,
                                       sync_new_modules_from_json)
from keepup.roles import ROLE_ADMIN

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "init_db",
]

logger = logging.getLogger(__name__)


#: The tables the capabilities declare, answered on access and never on import:
#: this module is the abstraction, and a capability is built on it -- importing
#: one here would close the circle (keepup-122). The list is what the 0.3.0 path
#: created; the kernel's table step creates what a plugin declares.
_FROM_CAPABILITIES = {
    "USERS": "keepup_users.tables", "USER_ROLES": "keepup_users.tables",
    "EXTERNAL_ROLE_MAPPINGS": "keepup_users.tables",
    "USER_PERMISSIONS": "keepup_users.tables",
    "FRONTEND_MODULES": "keepup_modules.tables", "ROLE_MODULES": "keepup_modules.tables",
    "PLUGIN_OVERRIDES": "keepup_modules.tables",
    "CLUSTER_MEMBERS": "keepup_cluster.tables", "CLUSTER_COMMANDS": "keepup_cluster.tables",
}


DISTRIBUTED_LOCKS = tables.table(
    "distributed_locks",
    # A text key written as ``TEXT PRIMARY KEY``: SQLite leaves such a column
    # nullable, and the declaration keeps it that way.
    Column("lock_name", Text, primary_key=True, nullable=True),
    Column("acquired_at", DateTime, nullable=False),
    Column("instance_id", Text, nullable=False),
)




# The metrics capability declares its own table (keepup-124); it is re-exported
# here so the path that predates the catalogue still creates it.
from keepup_metrics.tables import SYSTEM_METRICS  # noqa: E402


# Which backend plugins this deployment runs, when the administrator has
# decided it from the panel (task 65). The file config/modules.json stays
# the seed: it is committed and shared between deployments, so a decision
# about one stand belongs in that stand's own database. Read at start-up
# by keepup.plugins.enablement; the environment still overrules it.
# The replicas of this deployment and the commands addressed to them
# (task 1, keepup/cluster.py). One row per replica, rewritten by its own
# heartbeat; `state` is kept here rather than in memory so that a
# replica stopped by an administrator stays stopped across a restart.
# The roles one account holds. The set is the truth about who this is;
# ``users.role`` is a mirror of it for one release (keepup/auth/user_roles.py).


# The pair is unique on both dialects, but not by the same means: PostgreSQL
# holds a named constraint, SQLite a unique index of the same name. Databases
# already running carry one or the other, so both stay as they were.


#: In the order their foreign keys need: ``users`` before what references it.
def core_tables():
    """The tables the 0.3.0 path created, asked from their capabilities now.

    Imported here and not at the top of the module: this is the abstraction the
    capabilities are built on, and importing them at import time would close the
    circle (keepup-122). A deployment that runs the kernel's table step creates
    what each plugin declares and needs none of this.
    """
    from keepup_cluster.tables import CLUSTER_COMMANDS, CLUSTER_MEMBERS
    from keepup_modules.tables import FRONTEND_MODULES, PLUGIN_OVERRIDES, ROLE_MODULES
    from keepup_users.tables import (EXTERNAL_ROLE_MAPPINGS, USERS, USER_PERMISSIONS,
                                     USER_ROLES)
    return (DISTRIBUTED_LOCKS, USERS, SYSTEM_METRICS, FRONTEND_MODULES,
            PLUGIN_OVERRIDES, CLUSTER_MEMBERS, CLUSTER_COMMANDS, ROLE_MODULES, USER_ROLES,
            USER_PERMISSIONS, EXTERNAL_ROLE_MAPPINGS)


#: The old name of the list above, resolved on access so that the import stays
#: lazy. `CORE_TABLES` was a tuple of declarations at import time.
def __getattr__(name):
    if name == "CORE_TABLES":
        return core_tables()
    module = _FROM_CAPABILITIES.get(name)
    if module is not None:
        import importlib

        return getattr(importlib.import_module(module), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _hook_types():
    """The dialect's type names, as ``app_tables`` hooks written before the declarations expect."""
    if db_config.is_postgres():
        return {"auto_increment": "SERIAL PRIMARY KEY", "datetime_type": "TIMESTAMP",
                "boolean_type": "BOOLEAN", "text_type": "TEXT", "real_type": "REAL"}
    return {"auto_increment": "INTEGER PRIMARY KEY AUTOINCREMENT", "datetime_type": "DATETIME",
            "boolean_type": "BOOLEAN", "text_type": "TEXT", "real_type": "REAL"}


def init_db(app_tables=None, extra_setup=None, plugins_dir=None, plugins_config_path=None):
    """Create the core tables, on both SQLite and PostgreSQL.

    ``app_tables(cursor, types, db_config)`` is the application's own schema
    step, handed the connection this function opens and run after the core
    tables exist. ``extra_setup()`` is for the application's table modules that
    open their own connection, run before this one is opened.
    ``plugins_config_path`` is the catalogue of panel sections and plugins this
    deployment declares; without it the path the application named in
    KeepupSettings is used, and failing that the default (keepup-98).
    """
    try:
        init_login_attempts_table()
    except Exception as error:
        logger.warning(f"Could not create the login attempts table: {error}")
    try:
        panel_session.init_table()
    except Exception as error:
        logger.warning(f"Could not create the session table: {error}")
    if extra_setup is not None:
        extra_setup()

    # Created and committed before the connection below is opened: a SQLite
    # connection holding a write would lock out the one the declarations use.
    tables.ensure_tables(*core_tables())

    conn = DatabaseManagerV2.raw_connection()
    cursor = conn.cursor()
    try:
        if app_tables is not None:
            # The application's own tables reference users, which exists by now.
            app_tables(cursor, _hook_types(), db_config)

        conn.commit()

        init_incoming_requests_table()

        # One place creates the first-start accounts: three implementations in a
        # row had drifted apart in password, role and status -- see
        # keepup/auth/seed_accounts.py.
        from keepup_auth import seed_accounts, user_roles

        auth_source = AuthProviderFactory.get_provider().type
        seed_accounts.ensure_admin(cursor, role=ROLE_ADMIN, auth_source=auth_source,
                                   is_postgres=db_config.is_postgres())
        seed_accounts.ensure_system_user(cursor, role=ROLE_ADMIN, auth_source=auth_source,
                                         is_postgres=db_config.is_postgres())

        # An account whose role set has never been filled in gets the one its
        # ``role`` column names -- including the two just created. Idempotent,
        # so every later start writes nothing (keepup-51).
        user_roles.fill_from_mirror(cursor, is_postgres=db_config.is_postgres())

        conn.commit()
    finally:
        cursor.close()
        conn.close()

    # Sections added to config/modules.json since the last start reach the panel
    # without a manual import; what an administrator changed is left alone.
    # The framework's own sections first: they belong to the package, and their
    # file paths are corrected on a deployment that has been running since
    # before they moved into it (keepup-4).
    sync_framework_sections()
    sync_new_modules_from_json(plugins_config_path)

    if plugins_dir:
        os.makedirs(plugins_dir, exist_ok=True)

        init_file = os.path.join(plugins_dir, "__init__.py")
        if not os.path.exists(init_file):
            with open(init_file, 'w') as f:
                f.write("# Plugins package\n")

    logger.info(f"Database initialized successfully. Type: {db_config.db_type}")
