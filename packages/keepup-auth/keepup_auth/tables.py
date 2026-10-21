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

__all__ = ["AUTH_RATE_LIMIT", "AUTH_SESSION", "AUTH_TOTP", "LOGIN_ATTEMPTS"]

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
    # Where the last attempt came from: the row already counted failures, and a
    # count without an address cannot be read after an incident (keepup-172).
    Column("last_address", String(64).with_variant(Text(), "sqlite")),
)

#: One row per bucket and wall-clock minute: how much of a sign-in budget was
#: spent, shared by every replica because it is in the database (keepup-171).
AUTH_RATE_LIMIT = tables.table(
    "auth_rate_limit",
    Column("bucket", String(255).with_variant(Text(), "sqlite"), primary_key=True,
           nullable=False),
    Column("window_start", DateTime, primary_key=True, nullable=False),
    Column("hits", Integer, nullable=False, server_default=tables.sql_text("0")),
    Index("idx_auth_rate_limit_window", "window_start"),
)

#: One row per bucket and wall-clock minute: how much of a sign-in budget was
#: spent, shared by every replica because it is in the database (keepup-171).
AUTH_RATE_LIMIT = tables.table(
    "auth_rate_limit",
    Column("bucket", String(255).with_variant(Text(), "sqlite"), primary_key=True,
           nullable=False),
    Column("window_start", DateTime, primary_key=True, nullable=False),
    Column("hits", Integer, nullable=False, server_default=tables.sql_text("0")),
    Index("idx_auth_rate_limit_window", "window_start"),
)

#: One row per bucket and wall-clock minute: how much of a sign-in budget was
#: spent, shared by every replica because it is in the database (keepup-171).
AUTH_RATE_LIMIT = tables.table(
    "auth_rate_limit",
    Column("bucket", String(255).with_variant(Text(), "sqlite"), primary_key=True,
           nullable=False),
    Column("window_start", DateTime, primary_key=True, nullable=False),
    Column("hits", Integer, nullable=False, server_default=tables.sql_text("0")),
    Index("idx_auth_rate_limit_window", "window_start"),
)

#: The second factor of one account: a TOTP secret, whether it was confirmed,
#: the last step used (a code is one-use) and the recovery codes, hashed.
AUTH_TOTP = tables.table(
    "auth_totp",
    Column("user_id", Integer, primary_key=True, nullable=False),
    Column("secret", String(64).with_variant(Text(), "sqlite"), nullable=False),
    Column("confirmed_at", DateTime),
    Column("last_step", Integer),
    Column("recovery", Text),
    Column("created_at", DateTime, nullable=False, server_default=tables.NOW),
)
