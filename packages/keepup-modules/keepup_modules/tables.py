"""The catalogue's tables, declared where the capability lives.

Task keepup-113. `frontend_modules` was declared by the panel's distribution
because the panel drew the navigation from it; the model -- which section exists,
who may see it, where its files are -- is the catalogue's, so the declaration
travels with the catalogue. `keepup_db.schema`, which re-exported all three for
one
release, which is what a path written before the split still imports.

The navigation fields are columns now and not a JSON blob: the collapsed
navigation is rendered from `icon`, the order from `nav_order`, and
`declared_by` is what lets a section be filtered by the plugin that declared it
(doc/plugin_constructor.md, section 9.1).
"""

from sqlalchemy import (Boolean, Column, DateTime, Index,
                        Integer, Text, UniqueConstraint)
from sqlalchemy import text as sql_text

from keepup_db import tables

#: The names the declarations came with, kept beside them.
MODULES_TABLE = "frontend_modules"
ROLE_MODULES_TABLE = "role_modules"
PLUGIN_OVERRIDES_TABLE = "plugin_overrides"
USER_NAV_TABLE = "user_nav"

__all__ = ["FRONTEND_MODULES", "PLUGIN_OVERRIDES", "ROLE_MODULES", "USER_NAV"]

FRONTEND_MODULES = tables.table(
    MODULES_TABLE,
    tables.auto_id(),
    Column("module_id", Text, unique=True, nullable=False),
    Column("name", Text, nullable=False),
    Column("description", Text),
    Column("js_path", Text),
    Column("css_path", Text),
    Column("init_function", Text),
    # What the catalogue entry says about how the panel shows the section
    # (keepup-113): they lived only in sections.json, so a section added from
    # the panel could not carry a handler, an address or an order.
    Column("icon", Text),
    Column("handler_function", Text),
    Column("route_path", Text),
    Column("section_id", Text),
    Column("admin_only", Boolean, server_default=sql_text("FALSE")),
    Column("nav_order", Integer),
    # Which plugin -- or which file -- declared this section. What makes
    # removal and the read-time filter possible.
    Column("declared_by", Text),
    Column("version", Text, server_default="1.0.0"),
    Column("is_active", Boolean, server_default=sql_text("TRUE")),
    Column("config", Text),
    Column("created_at", DateTime, server_default=tables.NOW),
    Column("updated_at", DateTime, server_default=tables.NOW),
    Index("idx_frontend_modules_active", "is_active"),
)

ROLE_MODULES = tables.table(
    ROLE_MODULES_TABLE,
    tables.auto_id(),
    Column("role_name", Text, nullable=False),
    Column("module_id", Text, nullable=False),
    Column("is_active", Boolean, server_default=sql_text("TRUE")),
    Column("created_at", DateTime, server_default=tables.NOW),
    UniqueConstraint("role_name", "module_id"),
    Index("idx_role_modules_role", "role_name"),
    Index("idx_role_modules_active", "is_active"),
)

#: Which backend plugins an administrator switched on or off, by stand (task 65).
#: The file `config/modules.json` stays the seed: it is committed and shared
#: between deployments, so a decision about one stand belongs in that stand's
#: own database. Read at start-up by `keepup.plugins.enablement`; the
#: environment still overrules it.
PLUGIN_OVERRIDES = tables.table(
    PLUGIN_OVERRIDES_TABLE,
    Column("plugin_id", Text, primary_key=True, nullable=True),
    Column("enabled", Boolean, nullable=False),
    Column("changed_by", Integer),
    Column("changed_at", DateTime, server_default=tables.NOW),
)

#: The order one person keeps in the sidebar's own menu (task keepup-183).
#:
#: `nav_order` on `frontend_modules` is the deployment's default: what the
#: declaring file or plugin says, the same for everybody. The sequence a
#: person puts the entries into by dragging is this person's, and it stays
#: theirs: it is read only for that person, and the read leaves it alone
#: until a drag of theirs writes a new one. A user without a row is not
#: missing data: the read falls back to the default for that user.
USER_NAV = tables.table(
    USER_NAV_TABLE,
    Column("user_id", Integer, primary_key=True, nullable=False),
    tables.foreign_key("user_id", "users", ("id",), ondelete="CASCADE"),
    Column("nav_order", Text, nullable=False),
    Column("updated_at", DateTime, server_default=tables.NOW),
)
