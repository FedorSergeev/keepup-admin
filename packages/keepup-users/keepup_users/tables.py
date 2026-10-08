"""The tables of the accounts, declared where the capability lives.

Task keepup-124. `keepup.schema` re-exports them for one release, so every
importer keeps working while the accounts capability, its module and its plugin
travel into this distribution; nothing that reads a user row imports the kernel
for it.
"""

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, Text, UniqueConstraint
from sqlalchemy import text as sql_text

from keepup_db import tables

__all__ = ["EXTERNAL_ROLE_MAPPINGS", "USERS", "USER_PERMISSIONS", "USER_ROLES"]

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

USER_ROLES = tables.table(
    "user_roles",
    tables.auto_id(),
    Column("user_id", Integer, nullable=False),
    Column("role_name", Text, nullable=False),
    Column("granted_at", DateTime, server_default=tables.NOW),
    tables.foreign_key("user_id", "users", ("id",), ondelete="CASCADE"),
    UniqueConstraint("user_id", "role_name", name="user_roles_user_role_unique")
    .ddl_if(dialect="postgresql"),
    Index("idx_user_roles_user", "user_id"),
    Index("user_roles_user_role_unique", "user_id", "role_name",
          unique=True).ddl_if(dialect="sqlite"),
)

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
