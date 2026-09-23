"""Settings the framework promised and did not keep.

Task keepup-14. Four places where a deployment could configure something, be
told nothing, and get the old behaviour anyway -- which for a published package
is worse than having no setting at all: somebody relies on it.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/auth_settings_tests.py -v
"""

from pathlib import Path

import pytest

from keepup.auth import panel_session

REPO = Path(__file__).resolve().parents[2]


# --- the lifetime of a token --------------------------------------------------

def test_the_token_lifetime_comes_from_the_configuration():
    """`jwt.expire_minutes` was parsed out of config/auth.yaml and read by nobody.

    A deployment that asked for fifteen minutes went on handing out day-long
    tokens, and had no way to find out.
    """
    source = (REPO / "keepup/auth/providers/base.py").read_text(encoding="utf-8")
    assert "auth_config" in source
    assert "ACCESS_TOKEN_EXPIRE_MINUTES = 1440" not in source


def test_the_lifetime_is_a_usable_number():
    from keepup.auth.providers import base

    assert isinstance(base.ACCESS_TOKEN_EXPIRE_MINUTES, int)
    assert base.ACCESS_TOKEN_EXPIRE_MINUTES > 0


# --- the password rule --------------------------------------------------------

def test_the_password_rule_applies_when_an_administrator_sets_a_password():
    """It applied on one of the two paths that set a password.

    A deployment asking for twelve characters got them at registration and lost
    them here, without a word.
    """
    source = (REPO / "keepup/auth/routes.py").read_text(encoding="utf-8")

    change = source[source.index("new_password_hash = bcrypt.hashpw") - 1200:
                    source.index("new_password_hash = bcrypt.hashpw")]
    assert "password_rule" in change, (
        "the administrator's password change does not consult the rule")


# --- blocking an account ------------------------------------------------------

def test_blocking_an_account_revokes_its_sessions():
    """An HTTP request is refused anyway -- the status is read on every one.

    A WebSocket is not: it authenticates once at the handshake and then runs,
    so a blocked account kept whatever socket it already had open.
    """
    source = (REPO / "keepup/auth/routes.py").read_text(encoding="utf-8")

    block = source[source.index("notify_account_blocked(manager, int(user_id), admin)") - 800:
                   source.index("notify_account_blocked(manager, int(user_id), admin)")]
    assert "revoke_all" in block
    assert "REASON_ACCOUNT_BLOCKED" in block


# --- the cookies --------------------------------------------------------------

def test_a_deployment_can_insist_on_a_secure_cookie(monkeypatch):
    """Behind a proxy that terminates TLS and does not say so, there was no way.

    The cookie went out without Secure and travelled over plain HTTP the first
    time somebody typed the address without the scheme.
    """
    class PlainRequest:
        headers = {}

        class url:
            scheme = "http"

        cookies = {}

    monkeypatch.delenv(panel_session.FORCE_SECURE_COOKIES_ENV, raising=False)
    assert panel_session.is_https(PlainRequest()) is False

    monkeypatch.setenv(panel_session.FORCE_SECURE_COOKIES_ENV, "true")
    assert panel_session.is_https(PlainRequest()) is True


def test_the_proxy_is_still_believed_when_nobody_insists(monkeypatch):
    class ForwardedRequest:
        headers = {"x-forwarded-proto": "https"}

        class url:
            scheme = "http"

        cookies = {}

    monkeypatch.delenv(panel_session.FORCE_SECURE_COOKIES_ENV, raising=False)
    assert panel_session.is_https(ForwardedRequest()) is True


def test_the_external_sign_in_cookie_asks_the_same_question():
    """It carries the PKCE verifier, the nonce and the state.

    This one was False unconditionally -- not even asking, the way the session
    cookie does.
    """
    source = (REPO / "keepup/auth/oidc_routes.py").read_text(encoding="utf-8")
    assert "secure=False" not in source
    assert "secure=panel_session.is_https(request)" in source
