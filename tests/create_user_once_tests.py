"""Creating a user creates it once (keepup-79).

`create_user` inserted the account and then handed it to the provider again,
with the name and the password as one-element tuples; the provider's
`create_user` calls back into `create_user`, so a second insert ran on every
creation, failed, and was logged and swallowed.

    python3 -m pytest keepup/tests/create_user_once_tests.py -v
"""

import asyncio
import logging

import pytest

from keepup.auth import dependencies
from keepup.db import DatabaseManagerV2
from keepup.schema import init_db


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


def test_one_account_one_insert_and_nothing_logged(monkeypatch, caplog):
    inserts = []
    original = dependencies.auth_provider.create_user_in_db

    def counted(*args, **kwargs):
        inserts.append(args[0] if args else kwargs.get("username"))
        return original(*args, **kwargs)

    monkeypatch.setattr(dependencies.auth_provider, "create_user_in_db", counted)
    with caplog.at_level(logging.ERROR):
        user_id = asyncio.run(dependencies.create_user(
            "created-once", "a-long-password-79", agree_terms=True))

    assert user_id
    assert inserts == ["created-once"]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    rows = DatabaseManagerV2.execute(
        "SELECT id FROM users WHERE username = :u", {"u": "created-once"})
    assert len(rows) == 1


def test_the_provider_s_own_create_user_still_creates_one(caplog):
    with caplog.at_level(logging.ERROR):
        created = asyncio.run(dependencies.auth_provider.create_user(
            {"username": "created-by-provider", "password": "a-long-password-79",
             "agree_terms": True}))
    assert created is True
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
