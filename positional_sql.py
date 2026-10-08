"""Positional ``?`` parameters for DatabaseManagerV2, which takes named ones.

The manager removed in 0.2.0 took SQL with ``?`` placeholders and a tuple; the
pooled DatabaseManagerV2 takes ``:name`` parameters. Statements written for the
old one -- several assembled at run time, with lists of ``?`` -- are converted
here, at the call, rather than rewritten by hand one by one:

    DatabaseManagerV2.execute(*positional("SELECT ... WHERE id = ?", (5,)))

``?`` (or ``%s``) outside quotes becomes ``:p0``, ``:p1``...; a colon that
SQLAlchemy would take for a parameter of its own -- a time in a literal,
``'12:30'`` -- is escaped, and ``::`` casts are left alone.

Three applications kept identical copies of this module, held equal by a test,
because the framework did not have it (keepup-56). Moving to this release, an
application imports it from here and deletes its copy.
"""

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple


#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "execute_many",
    "insert_returning_id",
    "positional",
    "positional_many",
    "raw_connection",
]


def positional(query: str, params: Optional[Sequence[Any]] = None) -> Tuple[str, Dict[str, Any]]:
    """The query with named placeholders, and its parameters as a dictionary."""
    out, count, i, quote = [], 0, 0, None
    while i < len(query):
        ch = query[i]
        if quote:
            if ch == quote:
                # A doubled quote inside a literal is the quote itself.
                if i + 1 < len(query) and query[i + 1] == quote:
                    out.append(ch * 2)
                    i += 2
                    continue
                quote = None
            if ch == ":" and _takes_colon(query, i):
                out.append("\\:")
            else:
                out.append(ch)
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "?" or (ch == "%" and query[i + 1:i + 2] == "s"):
            # %s is the driver's own style, which some PostgreSQL branches used.
            out.append(f":p{count}")
            count += 1
            i += 2 if ch == "%" else 1
            continue
        elif ch == ":" and _takes_colon(query, i):
            out.append("\\:")
        else:
            out.append(ch)
        i += 1
    values = list(params or ())
    if len(values) != count:
        raise ValueError(f"{count} placeholders and {len(values)} parameters in: {query[:120]}")
    return "".join(out), {f"p{n}": value for n, value in enumerate(values)}


def _takes_colon(query: str, i: int) -> bool:
    """Whether SQLAlchemy would read the colon at i as the start of a parameter."""
    before = query[i - 1] if i else ""
    after = query[i + 1] if i + 1 < len(query) else ""
    return before != ":" and after != ":" and (after.isalnum() or after == "_")


def positional_many(query: str, rows: Sequence[Sequence[Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    """The query with named placeholders, and one parameter dictionary per row."""
    named, _ = positional(query, rows[0] if rows else [None] * query.count("?"))
    return named, [positional(query, row)[1] for row in rows]


def insert_returning_id(query: str, params: Optional[Sequence[Any]] = None) -> Optional[int]:
    """Run one INSERT and return the id of the row it made, on both dialects.

    What the removed manager's ``execute_commit`` answered; the query carries no RETURNING
    of its own -- DatabaseManagerV2 adds it on PostgreSQL.
    """
    from keepup.db import DatabaseManagerV2

    row = DatabaseManagerV2.execute_commit_returning(*positional(query, params), "id")
    return row["id"] if row else None


def execute_many(query: str, rows: Sequence[Sequence[Any]]) -> bool:
    """Run one statement for many positional rows in one transaction.

    True on success and False on a failure, which rolls the whole batch back --
    what the removed manager's ``executemany_commit`` answered.
    """
    if not rows:
        return True
    try:
        from keepup.db import DatabaseManagerV2

        DatabaseManagerV2.execute_many(*positional_many(query, rows))
        return True
    except Exception as e:
        logging.error(f"Batch statement failed and was rolled back: {e}")
        return False


def raw_connection():
    """A driver connection out of DatabaseManagerV2's pool, for code written
    against a cursor; ``close()`` hands it back to the pool.

    The statements run on it are the driver's own: ``?`` on SQLite, ``%s`` on
    PostgreSQL, with no conversion.
    """
    from keepup.db import DatabaseManagerV2

    with DatabaseManagerV2.get_session() as session:
        engine = session.get_bind()
    return engine.raw_connection()
