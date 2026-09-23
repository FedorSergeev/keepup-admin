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

from sqlalchemy import REAL, Boolean, Column, DateTime, Index, Integer, Text, UniqueConstraint
from sqlalchemy import text as sql_text

from keepup import tables
from keepup.audit import init_incoming_requests_table
from keepup.auth import panel_session
from keepup.auth.factory import AuthProviderFactory
from keepup.auth.login_throttle import init_login_attempts_table
from keepup.db import DatabaseManager, db_config
from keepup.modules import sync_framework_sections, sync_new_modules_from_json
from keepup.roles import ROLE_ADMIN

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "init_db",
]

logger = logging.getLogger(__name__)


DISTRIBUTED_LOCKS = tables.table(
    "distributed_locks",
    # A text key written as ``TEXT PRIMARY KEY``: SQLite leaves such a column
    # nullable, and the declaration keeps it that way.
    Column("lock_name", Text, primary_key=True, nullable=True),
    Column("acquired_at", DateTime, nullable=False),
    Column("instance_id", Text, nullable=False),
)

USERS = tables.table(
    "users",
    tables.auto_id(),
    Column("username", Text, unique=True, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("status", Text, server_default="blocked"),
    Column("role", Text, server_default="CLIENT"),
    Column("created_at", DateTime, server_default=tables.NOW),
    Column("external_id", Text),
    Column("auth_source", Text, server_default="local"),
    Column("last_external_sync", DateTime),
    Column("email", Text),
    Column("phone", Text),
    Column("full_name", Text),
    Column("updated_at", DateTime),
    Column("agree_terms", Boolean),
    # One external identity, one account. Partial: a local account has no
    # external id at all, and several of those must coexist.
    Index("users_external_identity_unique", "auth_source", "external_id", unique=True,
          postgresql_where=sql_text("external_id IS NOT NULL"),
          sqlite_where=sql_text("external_id IS NOT NULL")),
)

INTEGRATION_LOGS = tables.table(
    "integration_logs",
    tables.auto_id(),
    Column("user_id", Integer, nullable=False),
    Column("username", Text, nullable=False),
    Column("host", Text, nullable=False),
    Column("endpoint", Text, nullable=False),
    Column("method", Text, nullable=False),
    Column("request_body", Text),
    Column("response_body", Text),
    Column("status_code", Integer),
    Column("duration_ms", Integer),
    Column("created_at", DateTime, server_default=tables.NOW),
    tables.foreign_key("user_id", "users", ("id",)),
    Index("idx_integration_logs_user_id", "user_id"),
    Index("idx_integration_logs_created_at", "created_at"),
    Index("idx_integration_logs_host", "host"),
)

SYSTEM_METRICS = tables.table(
    "system_metrics",
    tables.auto_id(),
    Column("metric_name", Text, nullable=False),
    Column("metric_value", REAL, nullable=False),
    Column("timestamp", DateTime, server_default=tables.NOW),
    Column("app_instance", Text, server_default="main"),
    Column("tags", Text),
    Index("idx_metrics_timestamp", "timestamp"),
    Index("idx_metrics_name", "metric_name"),
)

FRONTEND_MODULES = tables.table(
    "frontend_modules",
    tables.auto_id(),
    Column("module_id", Text, unique=True, nullable=False),
    Column("name", Text, nullable=False),
    Column("description", Text),
    Column("js_path", Text),
    Column("css_path", Text),
    Column("init_function", Text),
    Column("version", Text, server_default="1.0.0"),
    Column("is_active", Boolean, server_default=sql_text("TRUE")),
    Column("config", Text),
    Column("created_at", DateTime, server_default=tables.NOW),
    Column("updated_at", DateTime, server_default=tables.NOW),
    Index("idx_frontend_modules_active", "is_active"),
)

# Which backend plugins this deployment runs, when the administrator has
# decided it from the panel (task 65). The file config/modules.json stays
# the seed: it is committed and shared between deployments, so a decision
# about one stand belongs in that stand's own database. Read at start-up
# by keepup.plugins.enablement; the environment still overrules it.
PLUGIN_OVERRIDES = tables.table(
    "plugin_overrides",
    Column("plugin_id", Text, primary_key=True, nullable=True),
    Column("enabled", Boolean, nullable=False),
    Column("changed_by", Integer),
    Column("changed_at", DateTime, server_default=tables.NOW),
)

# The replicas of this deployment and the commands addressed to them
# (task 1, keepup/cluster.py). One row per replica, rewritten by its own
# heartbeat; `state` is kept here rather than in memory so that a
# replica stopped by an administrator stays stopped across a restart.
CLUSTER_MEMBERS = tables.table(
    "cluster_members",
    Column("instance_id", Text, primary_key=True, nullable=True),
    Column("instance_name", Text),
    Column("host", Text),
    Column("pid", Integer),
    Column("build", Text),
    Column("started_at", DateTime),
    Column("last_seen", DateTime),
    Column("state", Text, nullable=False, server_default="serving"),
    Column("can_restart", Boolean),
    Column("restart_blocker", Text),
    Column("plugins_running", Text),
    Column("plugins_pending", Text),
)

CLUSTER_COMMANDS = tables.table(
    "cluster_commands",
    tables.auto_id(),
    Column("instance_id", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("forced", Boolean),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("requested_by", Integer),
    Column("requested_by_name", Text),
    Column("requested_at", DateTime),
    Column("claimed_at", DateTime),
    Column("finished_at", DateTime),
    Column("result", Text),
    Index("idx_cluster_commands_target", "instance_id", "status"),
)

ROLE_MODULES = tables.table(
    "role_modules",
    tables.auto_id(),
    Column("role_name", Text, nullable=False),
    Column("module_id", Text, nullable=False),
    Column("is_active", Boolean, server_default=sql_text("TRUE")),
    Column("created_at", DateTime, server_default=tables.NOW),
    UniqueConstraint("role_name", "module_id"),
    Index("idx_role_modules_role", "role_name"),
    Index("idx_role_modules_active", "is_active"),
)

# The pair is unique on both dialects, but not by the same means: PostgreSQL
# holds a named constraint, SQLite a unique index of the same name. Databases
# already running carry one or the other, so both stay as they were.
USER_PERMISSIONS = tables.table(
    "user_permissions",
    tables.auto_id(),
    Column("user_id", Integer, nullable=False),
    Column("permission_name", Text, nullable=False),
    Column("granted", Boolean, server_default=sql_text("FALSE")),
    Column("created_at", DateTime, server_default=tables.NOW),
    Column("updated_at", DateTime, server_default=tables.NOW),
    tables.foreign_key("user_id", "users", ("id",), ondelete="CASCADE"),
    UniqueConstraint("user_id", "permission_name",
                     name="user_permissions_user_permission_unique").ddl_if(dialect="postgresql"),
    Index("idx_user_permissions_user_id", "user_id"),
    Index("idx_user_permissions_permission", "permission_name"),
    Index("user_permissions_user_permission_unique", "user_id", "permission_name",
          unique=True).ddl_if(dialect="sqlite"),
)

EXTERNAL_ROLE_MAPPINGS = tables.table(
    "external_role_mappings",
    tables.auto_id(),
    Column("external_role_name", Text, nullable=False),
    Column("internal_permission_name", Text, nullable=False),
    Column("auth_source", Text, nullable=False),
    Column("created_at", DateTime, server_default=tables.NOW),
    UniqueConstraint("external_role_name", "auth_source", "internal_permission_name",
                     name="external_role_mappings_unique").ddl_if(dialect="postgresql"),
    Index("idx_ext_role_mappings_source", "auth_source"),
    Index("idx_ext_role_mappings_role", "external_role_name"),
    Index("external_role_mappings_unique", "external_role_name", "auth_source",
          "internal_permission_name", unique=True).ddl_if(dialect="sqlite"),
)

#: In the order their foreign keys need: ``users`` before what references it.
CORE_TABLES = (
    DISTRIBUTED_LOCKS, USERS, INTEGRATION_LOGS, SYSTEM_METRICS, FRONTEND_MODULES,
    PLUGIN_OVERRIDES, CLUSTER_MEMBERS, CLUSTER_COMMANDS, ROLE_MODULES,
    USER_PERMISSIONS, EXTERNAL_ROLE_MAPPINGS,
)


def _hook_types():
    """The dialect's type names, as ``app_tables`` hooks written before the declarations expect."""
    if db_config.is_postgres():
        return {"auto_increment": "SERIAL PRIMARY KEY", "datetime_type": "TIMESTAMP",
                "boolean_type": "BOOLEAN", "text_type": "TEXT", "real_type": "REAL"}
    return {"auto_increment": "INTEGER PRIMARY KEY AUTOINCREMENT", "datetime_type": "DATETIME",
            "boolean_type": "BOOLEAN", "text_type": "TEXT", "real_type": "REAL"}


def init_db(app_tables=None, extra_setup=None, plugins_dir=None):
    """Create the core tables, on both SQLite and PostgreSQL.

    ``app_tables(cursor, types, db_config)`` is the application's own schema
    step, handed the connection this function opens and run after the core
    tables exist. ``extra_setup()`` is for the application's table modules that
    open their own connection, run before this one is opened.
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
    tables.ensure_tables(*CORE_TABLES)

    conn = DatabaseManager.get_connection()
    cursor = conn.cursor()

    if app_tables is not None:
        # The application's own tables reference users, which exists by now.
        app_tables(cursor, _hook_types(), db_config)

    conn.commit()

    init_incoming_requests_table()

    # One place creates the first-start accounts: three implementations in a
    # row had drifted apart in password, role and status -- see
    # keepup/auth/seed_accounts.py.
    from keepup.auth.factory import AuthProviderFactory
    from keepup.auth import seed_accounts

    auth_source = AuthProviderFactory.get_provider().type
    seed_accounts.ensure_admin(cursor, role=ROLE_ADMIN, auth_source=auth_source,
                               is_postgres=db_config.is_postgres())
    seed_accounts.ensure_system_user(cursor, role=ROLE_ADMIN, auth_source=auth_source,
                                     is_postgres=db_config.is_postgres())

    conn.commit()
    cursor.close()
    conn.close()

    # Sections added to config/modules.json since the last start reach the panel
    # without a manual import; what an administrator changed is left alone.
    # The framework's own sections first: they belong to the package, and their
    # file paths are corrected on a deployment that has been running since
    # before they moved into it (keepup-4).
    sync_framework_sections()
    sync_new_modules_from_json()

    if plugins_dir:
        os.makedirs(plugins_dir, exist_ok=True)

        init_file = os.path.join(plugins_dir, "__init__.py")
        if not os.path.exists(init_file):
            with open(init_file, 'w') as f:
                f.write("# Plugins package\n")

    logger.info(f"Database initialized successfully. Type: {db_config.db_type}")
