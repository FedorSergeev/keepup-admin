"""Creating a theme answers its own id (keepup-82).

On SQLite the id was read with a separate `SELECT last_insert_rowid()`, which
could go to another pooled connection and answer another insert's id or 0; a
theme created active then activated some other row. The insert and the read
now share one session.

    python3 -m pytest keepup/tests/theme_create_id_tests.py -v
"""

import warnings

import pytest

from keepup import cache
from keepup.db import DatabaseManagerV2
from keepup.schema import init_db
from keepup.themes import ConfigService


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


@pytest.fixture
def themes():
    cache.invalidate_all()
    service = ConfigService()
    service.initialize()
    yield service
    cache.invalidate_all()


def row_id(name):
    return DatabaseManagerV2.execute_one(
        "SELECT id FROM visual_themes WHERE theme_name = :n", {"n": name})["id"]


def test_each_created_theme_answers_its_own_row(themes):
    # Other statements in between, on whatever connections the pool hands out.
    for i in range(5):
        created = themes.create_theme(f"own-id-{i}", "index_new.html", False)
        DatabaseManagerV2.execute("SELECT COUNT(*) AS n FROM visual_themes")
        assert created == row_id(f"own-id-{i}")


def test_a_theme_created_active_is_the_active_one(themes):
    for i in range(3):
        themes.create_theme(f"filler-{i}", "index_new.html", False)
    themes.create_theme("made-active-82", "index_new.html", is_active=True)
    assert themes.get_active_theme()["theme_name"] == "made-active-82"


def test_asking_for_the_last_id_without_the_session_warns():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        DatabaseManagerV2.get_last_insert_rowid()
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
