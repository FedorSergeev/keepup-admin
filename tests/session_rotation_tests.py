"""A session is shorter, and the key behind it can be rotated (keepup-173).

A day-long token renewed for thirty days was the blast radius of one stolen
cookie. The defaults are now an hour and a day of renewal, and a deployment can
replace its signing key without signing everybody out: the old key is still
believed until the tokens it signed are gone.

    python -m pytest tests/session_rotation_tests.py -v
"""

from datetime import timedelta

import jwt
import pytest

from keepup import clock
from keepup_auth import config, providers, session_lifetime
from keepup_auth.dependencies import create_access_token
from keepup_auth.signing_key import (TOKEN_AUDIENCE, TOKEN_ISSUER, decode_token,
                                     resolve_signing_key)

OLD = "old-framework-tests-only-key-for-rotation"
NEW = "new-framework-tests-only-key-for-rotation"


def test_the_token_lives_an_hour_by_default():
    assert config.AuthConfig().jwt_expire_minutes == 60
    assert providers.base.ACCESS_TOKEN_EXPIRE_MINUTES == 60


def test_a_session_may_be_renewed_for_a_day():
    assert session_lifetime.DEFAULT_RENEWAL_WINDOW == timedelta(hours=24)
    assert session_lifetime.WARNING_WINDOW == timedelta(minutes=5)


def test_a_session_without_a_beginning_is_not_renewable_for_ever():
    assert session_lifetime.is_renewable(None) is False


def test_a_session_past_the_window_is_not_renewable():
    now = clock.utcnow()
    assert session_lifetime.is_renewable(now - timedelta(hours=23), now=now) is True
    assert session_lifetime.is_renewable(now - timedelta(hours=25), now=now) is False


def test_a_token_signed_with_the_previous_key_is_still_believed(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", NEW)
    monkeypatch.setenv("SECRET_KEY_PREVIOUS", OLD)
    token = jwt.encode({"sub": "ada", "sid": "s", "exp": clock.utcnow() + timedelta(minutes=5)},
                       OLD, algorithm="HS256")
    assert decode_token(token)["sub"] == "ada"


def test_an_unknown_key_is_not_believed(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", NEW)
    monkeypatch.setenv("SECRET_KEY_PREVIOUS", OLD)
    token = jwt.encode({"sub": "ada"}, "z" * 40, algorithm="HS256")
    with pytest.raises(jwt.PyJWTError):
        decode_token(token)


def test_a_token_says_which_service_it_is_for(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", NEW)
    token = create_access_token({"sub": "ada", "sid": "s"})
    payload = jwt.decode(token, resolve_signing_key(), algorithms=["HS256"],
                         audience=TOKEN_AUDIENCE)
    assert payload["iss"] == TOKEN_ISSUER
    assert payload["aud"] == TOKEN_AUDIENCE
