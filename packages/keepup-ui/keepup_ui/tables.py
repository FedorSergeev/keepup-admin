"""The tables of the panel, declared where the capability lives.

Task keepup-124. `keepup.themes` and `keepup.schema` re-export them for one
release, so the path that predates the catalogue still creates them and every
reader keeps working; the declarations belong to the capability that keeps the
themes and the section catalogue.
"""

from sqlalchemy import (Boolean, Column, DateTime, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy import text as sql_text

from keepup_db import tables

#: The names the declarations came with, kept beside them.
THEMES_TABLE = "visual_themes"
MODULES_TABLE = "frontend_modules"
ROLE_MODULES_TABLE = "role_modules"

__all__ = ["FRONTEND_MODULES", "ROLE_MODULES", "VISUAL_THEMES"]

VISUAL_THEMES = tables.table(
    THEMES_TABLE,
    tables.auto_id(),
    Column("theme_name", String(100).with_variant(Text(), "sqlite"), nullable=False, unique=True),
    Column("main_page_file", String(255).with_variant(Text(), "sqlite"), nullable=False),
    # Branding came after the table; older databases get these two from
    # ensure_tables rather than from the CREATE.
    Column("brand_name", String(100).with_variant(Text(), "sqlite")),
    Column("logo_url", String(255).with_variant(Text(), "sqlite")),
    # SQLite had an integer flag here and the queries write 0/1 into it.
    Column("is_active", Boolean().with_variant(Integer(), "sqlite"),
           server_default=tables.per_dialect(postgres="FALSE", sqlite="0")),
    Column("created_at", DateTime, server_default=tables.NOW),
    Column("updated_at", DateTime, server_default=tables.NOW),
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
