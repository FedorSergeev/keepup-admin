"""The theme table, declared where the capability lives.

Task keepup-124. `keepup.themes` and `keepup.schema` re-export it for one
release, so the path that predates the catalogue still creates it and every
reader keeps working. The catalogue's own two tables left for `keepup-modules`
in keepup-113: the panel draws the navigation, the catalogue owns the model.
"""

from sqlalchemy import (Boolean, Column, DateTime, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy import text as sql_text

from keepup_db import tables

#: The name the declaration came with, kept beside it.
THEMES_TABLE = "visual_themes"

__all__ = ["VISUAL_THEMES"]

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
