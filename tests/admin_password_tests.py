"""The first administrator's password stays out of the logs, and the public one
is retired (keepup-74).

A generated password was written into the start-up log -- a file on disk and a
stream shipped to the collector -- and the password earlier builds gave the
account, the same everywhere and in the breach lists, only drew a warning.

    python3 -m pytest keepup/tests/admin_password_tests.py -v
"""

import io
import logging
import sqlite3
from types import SimpleNamespace

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup.auth import seed_accounts
from keepup.auth.routes import register_auth_routes
from keepup.db import DatabaseManagerV2
from keepup.schema import init_db

PUBLIC = seed_accounts.RETIRED_ADMIN_PASSWORDS[0]


@pytest.fixture
def cursor():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, "
                       "password_hash TEXT, status TEXT, role TEXT, auth_source TEXT)")
    yield connection.cursor()
    connection.close()


def stored(cursor):
    cursor.execute("SELECT password_hash FROM users WHERE username = ?",
                   (seed_accounts.ADMIN_USERNAME,))
    return cursor.fetchone()[0]


def with_public_password(cursor):
    cursor.execute("INSERT INTO users (username, password_hash, status, role, auth_source) "
                   "VALUES (?, ?, 'active', 'ADMIN', 'local')",
                   (seed_accounts.ADMIN_USERNAME, seed_accounts.hash_password(PUBLIC)))


def test_a_generated_password_goes_to_the_console_and_to_no_log(cursor, caplog):
    console = io.StringIO()
    announced = {}
    original = seed_accounts.announce_generated_admin_password

    def capture(password, log=seed_accounts.logger):
        announced["password"] = password
        original(password, log=log, console=console)

    with caplog.at_level(logging.DEBUG):
        seed_accounts.announce_generated_admin_password = capture
        try:
            seed_accounts.ensure_admin(cursor, role="ADMIN", auth_source="local",
                                       is_postgres=False, environ={})
        finally:
            seed_accounts.announce_generated_admin_password = original
    password = announced["password"]
    assert password in console.getvalue()
    assert password not in caplog.text
    assert bcrypt.checkpw(password.encode(), stored(cursor).encode())


def test_the_public_password_is_replaced_when_the_deployer_gives_one(cursor):
    with_public_password(cursor)
    seed_accounts.ensure_admin(cursor, role="ADMIN", auth_source="local", is_postgres=False,
                               environ={seed_accounts.ADMIN_PASSWORD_ENV: "the-deployer-s-choice"})
    assert bcrypt.checkpw(b"the-deployer-s-choice", stored(cursor).encode())


def test_without_one_the_public_password_is_named_not_replaced(cursor, caplog):
    with_public_password(cursor)
    before = stored(cursor)
    with caplog.at_level(logging.WARNING):
        seed_accounts.ensure_admin(cursor, role="ADMIN", auth_source="local",
                                   is_postgres=False, environ={})
    assert stored(cursor) == before
    assert seed_accounts.ADMIN_PASSWORD_ENV in caplog.text


# --- signing in ----------------------------------------------------------------------

@pytest.fixture
def client():
    init_db()
    DatabaseManagerV2.execute_commit("DELETE FROM login_attempts")
    app = FastAPI()
    register_auth_routes(app, SimpleNamespace(plugins={}))
    return TestClient(app)


def set_admin_password(password):
    DatabaseManagerV2.execute_commit(
        "UPDATE users SET password_hash = :h, status = 'active' WHERE username = :u",
        {"h": seed_accounts.hash_password(password), "u": seed_accounts.ADMIN_USERNAME})


def sign_in(client, password):
    return client.post("/api/auth/login",
                       json={"username": seed_accounts.ADMIN_USERNAME, "password": password})


def test_the_public_password_no_longer_signs_in(client):
    set_admin_password(PUBLIC)
    refused = sign_in(client, PUBLIC)
    assert refused.status_code == 403
    assert seed_accounts.ADMIN_PASSWORD_ENV in refused.json()["detail"]


def test_a_password_of_its_own_still_signs_in(client):
    set_admin_password("a-password-of-its-own-74")
    assert sign_in(client, "a-password-of-its-own-74").status_code == 200
