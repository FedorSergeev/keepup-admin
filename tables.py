"""Tables declared once and created on either dialect.

Until this module every table was written out twice -- once for PostgreSQL and
once for SQLite -- and every column added later was a third and fourth copy in
an ``ALTER``. The copies drifted: a foreign key on one side only, ``VARCHAR``
on one and ``TEXT`` on the other, a column added to the PostgreSQL branch and
forgotten in the SQLite one.

A table is now a SQLAlchemy ``Table`` in the owner's data-access module, and
``ensure_tables()`` makes the database match it, idempotently and on every
start -- there are still no migrations:

- the table is created with ``CREATE TABLE IF NOT EXISTS``, which, unlike
  inspecting first and creating second, is safe when two replicas start at
  once;
- a declared column the table lacks is added -- one catalogue read per table,
  not an ``ALTER`` per column per start, which on PostgreSQL would take a lock
  each time;
- declared indexes are created ``IF NOT EXISTS``.

What a column cannot say for both dialects at once is said with SQLAlchemy's
own means: ``with_variant()`` for a type, ``ddl_if(dialect=...)`` for a
constraint that only one dialect had. The DDL is compiled for the dialect in
force and run through ``DatabaseManagerV2.execute_commit`` -- the same path as
any other statement, so a test harness that rebinds the manager sees it too.

See doc/database_tables.md.
"""

import logging
import re
from typing import Dict, Iterable, List, Set

from sqlalchemy import BigInteger, Column, ForeignKeyConstraint, Index, Integer, MetaData, Sequence, Table
from sqlalchemy import text as sql_text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ColumnElement
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateColumn, CreateIndex, CreateSequence, CreateTable

from keepup import db as _db

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "ensure_columns",
    "ensure_tables",
    "foreign_key",
    "identifier",
    "per_dialect",
    "table",
]

logger = logging.getLogger(__name__)

#: One catalogue for every declared table of the process: a foreign key names
#: its target table, and SQLAlchemy resolves it here.
metadata = MetaData()

#: The default "now" of the hand-written DDL, kept as the database's own.
NOW = sql_text("CURRENT_TIMESTAMP")

_DIALECTS = {"postgres": postgresql.dialect(), "sqlite": sqlite.dialect()}


def dialect():
    """The SQLAlchemy dialect of the database in force."""
    return _DIALECTS["postgres"] if _db.db_config.is_postgres() else _DIALECTS["sqlite"]


def table(name: str, *columns, **kwargs) -> Table:
    """Declare a table in the shared catalogue.

    ``sqlite_autoincrement`` is on, as it was in every hand-written
    ``INTEGER PRIMARY KEY AUTOINCREMENT``: without it SQLite reuses the ids of
    deleted rows, and an id that once meant another order is a bug waiting.
    """
    kwargs.setdefault("sqlite_autoincrement", True)
    # Test harnesses re-import data-access modules; a second import replaces
    # the declaration instead of failing. Two different modules declaring one
    # name is caught by tests/schema_declaration_tests.py, not here.
    kwargs.setdefault("extend_existing", True)
    declared = Table(name, metadata, *columns, **kwargs)
    # SQLAlchemy writes NOT NULL on every key column; the hand-written SQLite
    # DDL never did on a single-column key (``id INTEGER PRIMARY KEY
    # AUTOINCREMENT``). On PostgreSQL the primary key makes the column NOT NULL
    # by itself, so dropping the explicit word changes nothing there and keeps
    # SQLite as it was. Composite keys were written with NOT NULL on each part
    # and keep whatever the declaration says.
    key = list(declared.primary_key.columns)
    if len(key) == 1:
        key[0].nullable = True
    return declared


def sequence(name: str, start: int = 1, increment: int = 1) -> Sequence:
    """A PostgreSQL sequence read by hand (``nextval``); SQLite has none."""
    return Sequence(name, start=start, increment=increment, metadata=metadata)


def ensure_sequences(*sequences: Sequence) -> None:
    """Create these sequences on PostgreSQL; nothing to do on SQLite."""
    if not _db.db_config.is_postgres():
        return
    for declared in sequences:
        _db.DatabaseManagerV2.execute_commit(
            str(CreateSequence(declared, if_not_exists=True).compile(dialect=dialect())).strip())


class per_dialect(ColumnElement):
    """A server default (or any SQL fragment) that the two dialects spell differently.

    ``server_default=per_dialect(postgres="NOW()", sqlite="CURRENT_TIMESTAMP")``.
    SQLAlchemy has ``with_variant`` for types but nothing for defaults, and a
    default that differed between the hand-written branches must keep differing.
    """

    inherit_cache = False

    def __init__(self, postgres: str, sqlite: str):
        self.postgres = postgres
        self.sqlite = sqlite


@compiles(per_dialect)
def _compile_per_dialect(element, compiler, **kw):
    return element.postgres if compiler.dialect.name == "postgresql" else element.sqlite


def _wanted_here(item, current) -> bool:
    """Whether an index or constraint marked ``ddl_if(dialect=...)`` belongs to this dialect."""
    condition = getattr(item, "_ddl_if", None)
    if condition is None or not condition.dialect:
        return True
    wanted = condition.dialect if isinstance(condition.dialect, (list, tuple, set)) else [condition.dialect]
    return current.name in wanted


def foreign_key(columns, target: str, target_columns=("id",), **kwargs) -> ForeignKeyConstraint:
    """A foreign key to a table that may be declared by another module, later.

    SQLAlchemy resolves the target in the catalogue when compiling. Modules are
    imported in whatever order start-up and tests happen to use, so the target
    may not be declared yet; a placeholder holding just the referenced columns
    stands in (``keep_existing``: it never overrides a real declaration), and
    the real declaration replaces it when it comes (``extend_existing``).
    The placeholder is never created: ``ensure_tables`` makes only the tables
    it is given.
    """
    if target not in metadata.tables:
        Table(target, metadata, *[Column(name, Integer, primary_key=True) for name in target_columns],
              keep_existing=True)
    columns = [columns] if isinstance(columns, str) else list(columns)
    return ForeignKeyConstraint(columns, [f"{target}.{c}" for c in target_columns], **kwargs)


def auto_id(name: str = "id") -> Column:
    """``SERIAL PRIMARY KEY`` / ``INTEGER PRIMARY KEY AUTOINCREMENT``."""
    return Column(name, Integer, primary_key=True, autoincrement=True)


def big_auto_id(name: str = "id") -> Column:
    """``BIGSERIAL PRIMARY KEY`` on PostgreSQL, the same integer key on SQLite."""
    return Column(name, BigInteger().with_variant(Integer(), "sqlite"),
                  primary_key=True, autoincrement=True)


def create_statements(tables: Iterable[Table]) -> List[str]:
    """The DDL ``ensure_tables`` runs for these tables, compiled for the dialect in force."""
    current = dialect()
    statements = []
    for declared in tables:
        statements.append(str(CreateTable(declared, if_not_exists=True).compile(dialect=current)).strip())
        for index in sorted(declared.indexes, key=lambda i: i.name or ""):
            if not _wanted_here(index, current):
                continue
            statements.append(str(CreateIndex(index, if_not_exists=True).compile(dialect=current)).strip())
    return statements


def existing_columns(name: str) -> Set[str]:
    """The columns the table has now, asked of the database in one read."""
    manager = _db.DatabaseManagerV2
    if _db.db_config.is_postgres():
        # current_schema(): a table of the same name in another schema must not
        # make a column look present when it is not.
        rows = manager.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :table AND table_schema = current_schema()",
            {"table": name})
        return {row["column_name"] for row in rows or []}
    # PRAGMA takes no parameters; the name comes from a declaration, never a request.
    rows = manager.execute(f'PRAGMA table_info("{identifier(name)}")')
    return {row["name"] for row in rows or []}


#: What a table or column may be called. Checked rather than escaped: an
#: identifier of a schema is written by the code that owns it, so anything
#: outside this shape is a mistake to stop, not a string to quote. The package
#: offers a template where a table name is data (a table per application), and
#: an author who copies it without a check of their own would otherwise reach
#: the DDL with whatever arrived (task keepup-15).
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def identifier(name: str) -> str:
    """The name, if it is one.

    Args:
        name: a table or column name about to be written into a statement.

    Returns:
        The same name.

    Raises:
        ValueError: when it is not a plain identifier.
    """
    if not isinstance(name, str) or not IDENTIFIER.match(name):
        raise ValueError(f"{name!r} is not a usable table or column name")
    return name


def column_definition(column: Column) -> str:
    """What follows the column name in ``ADD COLUMN``, for the dialect in force."""
    spec = str(CreateColumn(column).compile(dialect=dialect())).strip()
    quoted = dialect().identifier_preparer.quote(column.name)
    for prefix in (quoted + " ", column.name + " "):
        if spec.startswith(prefix):
            return spec[len(prefix):]
    return spec


def ensure_tables(*tables: Table) -> Dict[str, List[str]]:
    """Make the database hold these tables, their columns and their indexes.

    Returns, per table, the columns this call added -- empty when the table was
    already complete. Order matters only for foreign keys, and the caller passes
    tables in the order their references need, as the hand-written DDL did.
    """
    manager = _db.DatabaseManagerV2
    current = dialect()
    added: Dict[str, List[str]] = {}
    for declared in tables:
        manager.execute_commit(
            str(CreateTable(declared, if_not_exists=True).compile(dialect=current)).strip())
        added[declared.name] = _add_missing(declared.name, declared.columns)
        for index in sorted(declared.indexes, key=lambda i: i.name or ""):
            if not _wanted_here(index, current):
                continue
            manager.execute_commit(
                str(CreateIndex(index, if_not_exists=True).compile(dialect=current)).strip())
    return added


def _add_missing(name: str, columns: Iterable[Column]) -> List[str]:
    manager = _db.DatabaseManagerV2
    present = existing_columns(name)
    missing = [c for c in columns if c.name not in present]
    for column in missing:
        manager.add_column_if_missing(name, column.name, column_definition(column))
    return [c.name for c in missing]


def ensure_columns(name: str, *columns: Column) -> List[str]:
    """Add columns to a table another module declares.

    The application extends tables of the framework (``users`` gets a tenant's
    SSH key, a Telegram persona); the framework cannot know those columns, and
    declaring ``users`` a second time would make two owners of one table. The
    columns are declared here, by the module that needs them, and added to the
    existing table -- never creating it: that is its owner's job.
    """
    for column in columns:
        if getattr(column, "table", None) is None:
            # A free column cannot be compiled; a throw-away table outside the
            # catalogue gives it a parent without declaring anything.
            Table(f"_columns_of_{name}", MetaData(), column)
    return _add_missing(name, columns)


def ensure_indexes(name: str, *indexes: Index) -> None:
    """Create indexes on a table another module declares.

    The counterpart of ``ensure_columns``: a plugin that joins against a table
    it does not own may need an index there, and declaring the table a second
    time to carry it would make two owners of one table. Each index is given
    by name and column names; the table is never created here.
    """
    current = dialect()
    for index in indexes:
        if index.table is None:
            # Unbound columns cannot be compiled; a throw-away table outside the
            # catalogue gives the index its columns without declaring anything.
            Table(f"_indexes_of_{name}", MetaData(),
                  *[Column(expression, Integer) for expression in index.expressions], index)
        if not _wanted_here(index, current):
            continue
        text = str(CreateIndex(index, if_not_exists=True).compile(dialect=current)).strip()
        # The throw-away table's name is not the real one.
        text = text.replace(f"_indexes_of_{name}", name)
        _db.DatabaseManagerV2.execute_commit(text)

