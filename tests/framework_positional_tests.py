"""Positional parameters are a function of the framework (keepup-56).

Three applications kept identical copies of the helper that converts ``?``
statements for DatabaseManagerV2, held equal by a test of their own. The
framework now carries it; these are the same cases, against its module.

    python3 -m pytest keepup/tests/framework_positional_tests.py -v
"""

import pytest
from sqlalchemy import create_engine, text

from keepup.positional_sql import positional


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(text("CREATE TABLE t (id INTEGER, note TEXT)"))
        yield connection


def test_placeholders_become_named_and_the_values_travel(db):
    db.execute(text(*positional("INSERT INTO t (id, note) VALUES (?, ?)", (1, "a"))[:1]),
               positional("INSERT INTO t (id, note) VALUES (?, ?)", (1, "a"))[1])
    query, params = positional("SELECT note FROM t WHERE id IN (?, ?)", [1, 2])
    assert query == "SELECT note FROM t WHERE id IN (:p0, :p1)"
    assert params == {"p0": 1, "p1": 2}
    assert [r[0] for r in db.execute(text(query), params)] == ["a"]


def test_a_question_mark_or_a_colon_inside_quotes_stays_text(db):
    query, params = positional("INSERT INTO t (id, note) VALUES (?, 'what? it''s 12:30')", (2,))
    db.execute(text(query), params)
    assert db.execute(text("SELECT note FROM t WHERE id = 2")).scalar() == "what? it's 12:30"


def test_a_cast_is_left_alone_and_a_wrong_count_is_refused():
    assert positional("SELECT x::int FROM t WHERE a = ?", (3,)) == \
        ("SELECT x::int FROM t WHERE a = :p0", {"p0": 3})
    with pytest.raises(ValueError, match="1 placeholders and 0 parameters"):
        positional("SELECT ?", ())
    assert positional("SELECT 1") == ("SELECT 1", {})


def test_the_drivers_own_percent_s_is_a_placeholder_too():
    assert positional("SELECT * FROM t WHERE a = %s AND b LIKE '%s%' AND c = %s::int", (1, 2)) == \
        ("SELECT * FROM t WHERE a = :p0 AND b LIKE '%s%' AND c = :p1::int", {"p0": 1, "p1": 2})


# --- what the removed manager answered, through the pool ----------------------------

@pytest.fixture
def pool(tmp_path, monkeypatch):
    """DatabaseManagerV2 over a SQLite file of its own."""
    from sqlalchemy.orm import sessionmaker
    from keepup.db import DatabaseManagerV2, db_config
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    monkeypatch.setattr(DatabaseManagerV2, "_engine", engine)
    monkeypatch.setattr(DatabaseManagerV2, "_session_factory", sessionmaker(bind=engine))
    monkeypatch.setattr(db_config, "db_type", "sqlite")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT UNIQUE)"))
    yield engine
    engine.dispose()


def notes(engine):
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("SELECT note FROM t ORDER BY id"))]


def test_an_insert_answers_the_id_it_made(pool):
    from keepup.positional_sql import insert_returning_id
    assert insert_returning_id("INSERT INTO t (note) VALUES (?)", ("a",)) == 1
    assert insert_returning_id("INSERT INTO t (note) VALUES (?)", ("b",)) == 2


def test_a_batch_is_written_whole_or_not_at_all(pool):
    from keepup.positional_sql import execute_many
    assert execute_many("INSERT INTO t (note) VALUES (?)", [("a",), ("b",)]) is True
    assert execute_many("INSERT INTO t (note) VALUES (?)", [("c",), ("a",)]) is False
    assert notes(pool) == ["a", "b"]
    assert execute_many("INSERT INTO t (note) VALUES (?)", []) is True


def test_a_raw_connection_comes_from_the_pool_and_goes_back(pool):
    from keepup.positional_sql import raw_connection
    conn = raw_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO t (note) VALUES (?)", ("r",))
    conn.commit()
    cursor.close()
    conn.close()
    assert notes(pool) == ["r"]
    assert pool.pool.checkedout() == 0
