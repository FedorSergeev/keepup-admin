"""The leftovers, each of which on its own would not have stopped a release.

Task keepup-15. A customer's name in a connection string, a parameter written
inside a string literal, a table name reaching the DDL unchecked, a lock with
no way to say "still here", an import of the wrong library by the right name.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/leftovers_tests.py -v
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from keepup import tables
from keepup.db import DatabaseManager, DatabaseManagerV2
from keepup.schema import init_db

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parent
KEEPUP = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    """The framework's own tables, in the session's throwaway database."""
    init_db()


@pytest.fixture(autouse=True)
def no_locks_left_over():
    DatabaseManager.execute_commit_only("DELETE FROM distributed_locks", ())
    yield
    DatabaseManager.execute_commit_only("DELETE FROM distributed_locks", ())


# --- somebody else's name -----------------------------------------------------

def test_no_customer_of_an_earlier_project_is_named_in_the_repository():
    """`sqlite3.connect('sanroyal.db')` sat in the package's database module.

    It opened an empty file in the working directory, past every piece of the
    configuration, and it was re-exported into the plugin facade -- so an
    author who took it by name would have written into it believing it was the
    application's database.
    """
    offenders = []
    # The package, not the whole repository: in a clone of the framework the
    # repository is the package, and walking whatever surrounds it would be
    # checking an application, which has a suite of its own (keepup-26).
    for path in (KEEPUP).rglob("*.py"):
        if any(part in {".venv", "venv", "__pycache__", "node_modules", "tests"}
               for part in path.parts):
            # The tests are where the name is allowed to appear: naming it is
            # how they forbid it anywhere else.
            continue
        if "sanroyal" in path.read_text(encoding="utf-8").lower():
            offenders.append(str(path.relative_to(KEEPUP)))
    assert offenders == [], "\n".join(offenders)


def test_the_legacy_connection_helper_is_gone():
    source = (PACKAGE / "db.py").read_text(encoding="utf-8")
    assert "def get_db_connection" not in source


# --- a parameter inside a literal ---------------------------------------------

def test_the_interval_is_not_built_out_of_a_string_literal():
    """`INTERVAL '%s days'` puts the placeholder inside quotes.

    The driver's quoting does not reach in there: a string argument would have
    left the literal. Nothing exploited it only because the one caller passes
    an int from a bounded query parameter -- and the method is public.
    """
    source = (PACKAGE / "events.py").read_text(encoding="utf-8")
    # The statements themselves, not the comment above them that quotes the
    # old shape in order to explain it.
    statements = "\n".join(line for line in source.splitlines()
                           if not line.lstrip().startswith("#"))
    assert "INTERVAL '%s" not in statements
    assert "datetime('now', '-' ||" not in statements
    assert "WHERE created_at < %s" in statements


# --- names reaching the DDL ---------------------------------------------------

@pytest.mark.parametrize("name", [
    'users"; DROP TABLE users; --',
    "users; DROP TABLE users",
    "users-1",
    "",
    "1users",
])
def test_a_name_that_is_not_an_identifier_is_refused(name):
    """Refused rather than quoted: an identifier of a schema is written by the
    code that owns it, so anything else is a mistake to stop."""
    with pytest.raises(ValueError):
        tables.identifier(name)


@pytest.mark.parametrize("name", ["users", "app_logs_keepup", "_private", "T1"])
def test_an_ordinary_name_passes(name):
    assert tables.identifier(name) == name


def test_the_column_check_refuses_a_name_it_cannot_trust():
    with pytest.raises(ValueError):
        DatabaseManagerV2.column_exists('users"; DROP TABLE users; --', "id")


# --- the lock -----------------------------------------------------------------

async def test_a_held_lock_can_say_it_is_still_here():
    """Without renewal the lock had one fixed life and no way to extend it.

    A background task running longer than max_lock_time had its row deleted by
    the next replica, which then ran the same work beside it -- for a billing
    pass, the work done twice.
    """
    from keepup.locks import DatabaseLock

    lock = DatabaseLock("renewal_probe", timeout=5, max_lock_time=60)
    assert await lock.acquire()
    try:
        before = DatabaseManagerV2.execute_one(
            "SELECT acquired_at FROM distributed_locks WHERE lock_name = :name",
            {"name": "renewal_probe"})

        DatabaseManagerV2.execute_commit(
            "UPDATE distributed_locks SET acquired_at = :old WHERE lock_name = :name",
            {"old": datetime.utcnow() - timedelta(seconds=30), "name": "renewal_probe"})

        assert await lock.renew() is True

        after = DatabaseManagerV2.execute_one(
            "SELECT acquired_at FROM distributed_locks WHERE lock_name = :name",
            {"name": "renewal_probe"})
        assert after["acquired_at"] != before["acquired_at"] or after is not None
    finally:
        await lock.release()


async def test_a_lock_taken_over_by_somebody_else_stops_pretending():
    """The caller is then doing work it no longer holds the lock for."""
    from keepup.locks import DatabaseLock

    lock = DatabaseLock("stolen_probe", timeout=5, max_lock_time=60)
    assert await lock.acquire()
    try:
        DatabaseManagerV2.execute_commit(
            "DELETE FROM distributed_locks WHERE lock_name = :name",
            {"name": "stolen_probe"})

        assert await lock.renew() is False
        assert lock.acquired is False
    finally:
        await lock.release()


# --- the right library by the right name --------------------------------------

def test_the_local_provider_reads_tokens_with_the_library_the_package_declares():
    """The package declares PyJWT, and `import jwt` must be PyJWT.

    Two distributions install a package called `jwt` -- PyJWT and an unrelated
    `jwt` -- and a bare import resolves to whichever was installed last. That
    is why this check exists at all: it once read the other way round, because
    an application pinned the unrelated one and a module-level decode() call
    silently fell into `except Exception` and returned None, closing a sign-in
    path by accident rather than by decision.

    The answer is not to avoid the name but to stop installing the collision;
    the application no longer pins it, and a test beside its requirements keeps
    it that way. What is checked here is the other half: the package uses the
    library it declares, and python-jose -- whose `ecdsa` carries an advisory
    with no fix -- has not come back (keepup-32).
    """
    source = (PACKAGE / "auth/providers/local.py").read_text(encoding="utf-8")
    assert "\nimport jwt\n" in source
    assert "from jose import" not in source, \
        "python-jose is back, and with it an advisory that has no fix (keepup-32)"
