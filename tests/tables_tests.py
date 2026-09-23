"""Tables declared once: what ensure_tables() does to a real database.

SQLite is executed; PostgreSQL is compiled, because a test run has no server --
the equivalence of the whole stand's schema on PostgreSQL is checked by
tests/schema_declaration_tests.py against the recorded hand-written DDL.

    python3 -m pytest keepup/tests/tables_tests.py -v
"""

import pytest
from sqlalchemy import (BigInteger, Boolean, Column, DateTime, ForeignKeyConstraint, Index,
                        Integer, MetaData, String, Text, UniqueConstraint)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from keepup import tables
from keepup.db import DatabaseManagerV2, db_config


@pytest.fixture
def fresh_database(tmp_path, monkeypatch):
    DatabaseManagerV2.dispose()
    monkeypatch.setattr(db_config, "db_path", str(tmp_path / "tables.db"), raising=False)
    monkeypatch.setattr(db_config, "db_type", "sqlite", raising=False)
    yield
    DatabaseManagerV2.dispose()


@pytest.fixture
def catalogue(monkeypatch):
    """A catalogue of its own, so the test tables do not join the application's."""
    monkeypatch.setattr(tables, "metadata", MetaData())
    return tables.metadata


def orders(*extra):
    tables.table("users", tables.auto_id(), Column("username", Text, nullable=False, unique=True))
    return tables.table(
        "demo_orders",
        tables.auto_id(),
        Column("user_id", BigInteger().with_variant(Integer(), "sqlite"), nullable=False),
        Column("paid", Boolean().with_variant(Integer(), "sqlite"), nullable=False,
               server_default=tables.sql_text("FALSE")),
        Column("title", String(255).with_variant(Text(), "sqlite")),
        Column("created_at", DateTime, nullable=False, server_default=tables.NOW),
        *extra,
        UniqueConstraint("user_id", "title"),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE").ddl_if(dialect="postgresql"),
        Index("idx_demo_orders_user", "user_id"),
    )


def columns(name):
    return {row["name"] for row in DatabaseManagerV2.execute(f'PRAGMA table_info("{name}")')}


def test_a_declared_table_is_created_with_its_index(fresh_database, catalogue):
    declared = orders()
    tables.ensure_tables(catalogue.tables["users"], declared)

    assert columns("demo_orders") == {"id", "user_id", "paid", "title", "created_at"}
    indexes = {row["name"] for row in DatabaseManagerV2.execute('PRAGMA index_list("demo_orders")')}
    assert "idx_demo_orders_user" in indexes


def test_ensure_is_idempotent(fresh_database, catalogue):
    declared = orders()
    tables.ensure_tables(catalogue.tables["users"], declared)
    DatabaseManagerV2.execute_commit(
        "INSERT INTO demo_orders (user_id, title) VALUES (1, 'kept')")

    added = tables.ensure_tables(catalogue.tables["users"], declared)

    assert added == {"users": [], "demo_orders": []}
    assert DatabaseManagerV2.execute_one("SELECT title FROM demo_orders")["title"] == "kept"


def test_a_column_added_to_the_declaration_is_added_to_an_existing_table(fresh_database, catalogue):
    tables.ensure_tables(*[orders().metadata.tables[n] for n in ("users", "demo_orders")])
    DatabaseManagerV2.execute_commit("INSERT INTO demo_orders (user_id, title) VALUES (1, 'old row')")

    grown = orders(Column("note", Text, nullable=False, server_default="none"))
    added = tables.ensure_tables(grown)

    assert added == {"demo_orders": ["note"]}
    row = DatabaseManagerV2.execute_one("SELECT title, note FROM demo_orders")
    assert (row["title"], row["note"]) == ("old row", "none")


def test_sqlite_keeps_autoincrement_and_drops_the_postgres_only_key(fresh_database, catalogue):
    declared = orders()
    tables.ensure_tables(catalogue.tables["users"], declared)

    sql = DatabaseManagerV2.execute_one(
        "SELECT sql FROM sqlite_master WHERE name = 'demo_orders'")["sql"]
    assert "AUTOINCREMENT" in sql
    assert "REFERENCES" not in sql


def test_postgres_gets_serial_bigint_boolean_and_the_key(catalogue):
    declared = orders()
    ddl = str(CreateTable(declared, if_not_exists=True).compile(dialect=postgresql.dialect()))

    assert "CREATE TABLE IF NOT EXISTS demo_orders" in ddl
    assert "id SERIAL" in ddl and "PRIMARY KEY (id)" in ddl
    assert "user_id BIGINT NOT NULL" in ddl
    assert "paid BOOLEAN DEFAULT FALSE NOT NULL" in ddl
    assert "VARCHAR(255)" in ddl
    assert "REFERENCES users (id) ON DELETE CASCADE" in ddl


def test_sequences_exist_only_on_postgres(fresh_database, catalogue, monkeypatch):
    seq = tables.sequence("demo_seq", start=1, increment=100)
    sent = []
    monkeypatch.setattr(DatabaseManagerV2, "execute_commit", classmethod(lambda cls, q, p=None: sent.append(q)))

    tables.ensure_sequences(seq)
    assert sent == []

    monkeypatch.setattr(db_config, "db_type", "postgres", raising=False)
    tables.ensure_sequences(seq)
    assert sent == ["CREATE SEQUENCE IF NOT EXISTS demo_seq INCREMENT BY 100 START WITH 1"]


def test_a_reimported_declaration_replaces_rather_than_fails(catalogue):
    first = tables.table("demo_twice", tables.auto_id())
    second = tables.table("demo_twice", tables.auto_id(), Column("extra", Text))
    assert first is second and "extra" in second.c


def test_a_default_spelled_differently_per_dialect(catalogue):
    from sqlalchemy.dialects import sqlite
    declared = tables.table("demo_defaults", tables.auto_id(),
                            Column("at", DateTime, server_default=tables.per_dialect(
                                postgres="NOW()", sqlite="CURRENT_TIMESTAMP")))
    assert "DEFAULT NOW()" in str(CreateTable(declared).compile(dialect=postgresql.dialect()))
    assert "DEFAULT (CURRENT_TIMESTAMP)" in str(CreateTable(declared).compile(dialect=sqlite.dialect()))


def test_an_index_of_one_dialect_is_made_only_there(fresh_database, catalogue):
    declared = tables.table("demo_idx", tables.auto_id(), Column("v", Integer),
                            Index("idx_demo_idx_pg", "v").ddl_if(dialect="postgresql"),
                            Index("idx_demo_idx_both", "v"))
    tables.ensure_tables(declared)
    indexes = {row["name"] for row in DatabaseManagerV2.execute('PRAGMA index_list("demo_idx")')}
    assert indexes == {"idx_demo_idx_both"}


def test_a_key_to_a_table_declared_later_compiles_and_the_real_one_wins(catalogue):
    child = tables.table("demo_child", tables.auto_id(), Column("parent_id", BigInteger),
                         tables.foreign_key("parent_id", "demo_parent", ondelete="CASCADE"))
    ddl = str(CreateTable(child).compile(dialect=postgresql.dialect()))
    assert "REFERENCES demo_parent (id) ON DELETE CASCADE" in ddl

    parent = tables.table("demo_parent", tables.big_auto_id(), Column("name", Text))
    assert set(parent.c.keys()) == {"id", "name"}
    assert "BIGSERIAL" in str(CreateTable(parent).compile(dialect=postgresql.dialect()))


def test_a_key_column_carries_no_explicit_not_null_on_sqlite(fresh_database, catalogue):
    """As in the hand-written ``INTEGER PRIMARY KEY AUTOINCREMENT``; PostgreSQL's key implies it."""
    declared = tables.table("demo_key", tables.auto_id())
    tables.ensure_tables(declared)
    sql = DatabaseManagerV2.execute_one("SELECT sql FROM sqlite_master WHERE name = 'demo_key'")["sql"]
    assert "NOT NULL" not in sql


def test_columns_are_added_to_a_table_another_module_owns(fresh_database, catalogue):
    DatabaseManagerV2.execute_commit("CREATE TABLE owned_elsewhere (id INTEGER PRIMARY KEY, name TEXT)")

    assert tables.ensure_columns("owned_elsewhere", Column("persona", String(20))) == ["persona"]
    assert tables.ensure_columns("owned_elsewhere", Column("persona", String(20))) == []
    assert columns("owned_elsewhere") == {"id", "name", "persona"}
    assert "owned_elsewhere" not in catalogue.tables
