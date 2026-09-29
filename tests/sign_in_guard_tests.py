"""Sign-in: the guessing limit holds against parallel attempts, and the time of
the answer does not say which names exist (keepup-63).

Against the session's throwaway SQLite database, through the real sign-in route:
attempts are sent at once from one event loop, the way a guesser would send them.

    python3 -m pytest keepup/tests/sign_in_guard_tests.py -v
"""

import asyncio
import statistics
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from keepup.auth import dependencies, login_throttle
from keepup.auth.routes import register_auth_routes
from keepup.db import DatabaseManagerV2
from keepup.schema import init_db

LIMIT = 5
PASSWORD = "a-long-password-63"


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv(login_throttle.MAX_ATTEMPTS_ENV, str(LIMIT))
    DatabaseManagerV2.execute_commit("DELETE FROM login_attempts")
    yield
    DatabaseManagerV2.execute_commit("DELETE FROM login_attempts")


@pytest.fixture(scope="module")
def app():
    application = FastAPI()
    register_auth_routes(application, SimpleNamespace(plugins={}))
    return application


def account(name):
    if not dependencies.get_user_by_username(name):
        uid = dependencies.save_user_to_db(name, PASSWORD)
        dependencies.update_user(uid, status="active")
    return name


async def attempts(app, name, password, count):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        answers = await asyncio.gather(*(
            client.post("/api/auth/login", json={"username": name, "password": password})
            for _ in range(count)))
    return [answer.status_code for answer in answers]


def test_parallel_guesses_stop_at_the_limit(app):
    name = account("guess-target")
    codes = asyncio.run(attempts(app, name, "wrong", 20))
    assert codes.count(401) == LIMIT
    assert codes.count(429) == 20 - LIMIT


def test_the_right_password_still_gets_in_and_clears_the_count(app):
    name = account("guess-owner")
    asyncio.run(attempts(app, name, "wrong", LIMIT - 1))
    assert asyncio.run(attempts(app, name, PASSWORD, 1)) == [200]
    assert asyncio.run(attempts(app, name, "wrong", LIMIT)) == [401] * LIMIT


def test_a_missing_name_takes_as_long_as_a_wrong_password(app):
    name = account("timing-owner")

    def timed(who):
        samples = []
        for _ in range(3):
            DatabaseManagerV2.execute_commit("DELETE FROM login_attempts")
            started = time.perf_counter()
            asyncio.run(attempts(app, who, "wrong", 1))
            samples.append(time.perf_counter() - started)
        return statistics.median(samples)

    existing, missing = timed(name), timed("nobody-by-this-name")
    # Both do one bcrypt check; before, the missing name answered ~100 times faster.
    assert missing > existing * 0.5
