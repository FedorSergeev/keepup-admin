"""The tables of the sign-in, declared where the capability lives.

Task keepup-124. `keepup.auth.panel_session` and `keepup.auth.login_throttle`
re-export them for one release, so the path that predates the catalogue still
creates them and every reader keeps working; the declarations belong to the
capability that keeps the sessions and the attempts.
"""

from sqlalchemy import (Column, DateTime, Index, Integer,
                        String, Text)

from keepup_db import tables

#: The name the session table is declared under, as the module it came from had it.
TABLE = "auth_session"

__all__ = ["AUTH_SESSION", "LOGIN_ATTEMPTS"]

AUTH_SESSION = tables.table(
    TABLE,
    Column("sid", String(64).with_variant(Text(), "sqlite"), primary_key=True, nullable=True),
    Column("user_id", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("revoked_at", DateTime),
    Column("revoked_reason", String(64).with_variant(Text(), "sqlite")),
    Index(f"idx_{TABLE}_user", "user_id"),
)

LOGIN_ATTEMPTS = tables.table(
    "login_attempts",
    Column("username", String(255).with_variant(Text(), "sqlite"), primary_key=True, nullable=True),
    Column("failures", Integer, nullable=False, server_default=tables.sql_text("0")),
    Column("last_failure_at", DateTime),
)
