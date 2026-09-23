"""The whole sign-in, through a real application built for the occasion.

The module tests next door check each step on its own. These go through the two
endpoints the way a browser does -- follow the redirect to the provider, come
back with a code, end up with the application's session cookie -- because the
steps can each be right while what they add up to is not: the cookie not set,
the account not found, a replay accepted.

    python3 -m pytest keepup/tests/oidc_flow_tests.py -v
"""

import time
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from keepup.auth import oidc, oidc_policy, panel_session
from keepup.db import DatabaseManagerV2
from keepup.factory import create_app
from keepup.schema import init_db
from keepup.settings import KeepupSettings, OidcSettings

from oidc_tests import CLIENT_ID, ISSUER, REDIRECT, FakeProvider


@pytest.fixture(scope="module", autouse=True)
def tables():
    init_db()


@pytest.fixture
def provider():
    return FakeProvider()


def build(provider, policy=None, **overrides):
    """An application with this provider and nothing else of its own."""
    settings = KeepupSettings(
        title="External Sign-in",
        static_mounts=(),
        plugin_manager=None,
        oidc=OidcSettings(issuer=ISSUER, client_id=CLIENT_ID, client_secret="shh",
                          redirect_uri=REDIRECT, **overrides),
        oidc_account_policy=policy,
    )
    app = create_app(settings)
    # The application's own provider directory talks to the fake one.
    app.state.oidc_directory._client_factory = provider.client_factory
    for route in app.routes:
        pass
    return app


@pytest.fixture
def client_and_provider(provider):
    app = build(provider, policy=oidc_policy.create_account())
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as client:
        yield client, provider


def forget(issuer, subject):
    DatabaseManagerV2.execute_commit(
        "DELETE FROM users WHERE auth_source = :source AND external_id = :subject",
        {"source": issuer, "subject": str(subject)})


def begin(client):
    """Start a sign-in and return (state, nonce, the flow cookie)."""
    response = client.get("/api/auth/oidc/login")
    assert response.status_code == 303, response.text
    query = parse_qs(urlparse(response.headers["location"]).query)
    flow = oidc.read_flow_token(response.cookies[oidc.FLOW_COOKIE])
    return query["state"][0], query["nonce"][0], response.cookies[oidc.FLOW_COOKIE]


def return_from_provider(client, provider, state, nonce, cookie, claims=None, code=None):
    """Come back from the provider with a code.

    A fresh code per return by default, because a provider redeems each one
    once; passing the same code twice on purpose is what the replay test does.
    """
    provider.next_token = provider.sign(provider.claims(nonce=nonce, **(claims or {})))
    client.cookies.set(oidc.FLOW_COOKIE, cookie)
    return client.get("/api/auth/oidc/callback",
                      params={"code": code or f"code-{uuid.uuid4()}", "state": state})


# --- the routes exist only when a provider is configured -----------------------

def test_without_a_provider_the_routes_are_absent():
    """The possibility is off, not broken, and 404 says exactly that."""
    app = create_app(KeepupSettings(static_mounts=(), plugin_manager=None))

    paths = {route.path for route in app.routes}
    assert "/api/auth/oidc/login" not in paths
    assert "/api/auth/oidc/callback" not in paths


def test_with_a_provider_the_login_redirects(client_and_provider):
    client, _ = client_and_provider

    response = client.get("/api/auth/oidc/login")

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"{ISSUER}/authorize?")
    assert oidc.FLOW_COOKIE in response.cookies


# --- the whole way through -----------------------------------------------------

def test_a_person_signs_in_and_gets_the_application_session(client_and_provider):
    client, provider = client_and_provider
    forget(ISSUER, "subject-42")

    state, nonce, cookie = begin(client)
    response = return_from_provider(client, provider, state, nonce, cookie)

    assert response.status_code == 303
    assert response.headers["location"] == "/selfcare"
    # The session is the application's ordinary one, not something OIDC-shaped.
    assert panel_session.SESSION_COOKIE in response.cookies


def test_the_same_person_comes_back_to_the_same_account(client_and_provider):
    """Even when the provider has since changed their email and their name."""
    client, provider = client_and_provider
    forget(ISSUER, "subject-42")

    state, nonce, cookie = begin(client)
    return_from_provider(client, provider, state, nonce, cookie)
    first = DatabaseManagerV2.execute_one(
        "SELECT id, username FROM users WHERE external_id = :subject", {"subject": "subject-42"})

    state, nonce, cookie = begin(client)
    return_from_provider(client, provider, state, nonce, cookie,
                         claims={"email": "renamed@example.com", "preferred_username": "renamed"})
    second = DatabaseManagerV2.execute_one(
        "SELECT id, username FROM users WHERE external_id = :subject", {"subject": "subject-42"})

    assert first["id"] == second["id"]
    assert DatabaseManagerV2.execute(
        "SELECT id FROM users WHERE external_id = :subject", {"subject": "subject-42"}).__len__() == 1


def test_a_replayed_return_is_refused(client_and_provider):
    """The intercepted return link must be worth nothing the second time.

    What refuses it is the code, not this application: a provider redeems an
    authorization code once, so the second exchange comes back an error. The
    flow cookie is cleared as well, which stops the ordinary case -- a person
    pressing "back" -- before the provider is even asked.
    """
    client, provider = client_and_provider
    forget(ISSUER, "subject-42")

    state, nonce, cookie = begin(client)
    first = return_from_provider(client, provider, state, nonce, cookie, code="captured")
    assert first.status_code == 303

    client.cookies.clear()
    replay = return_from_provider(client, provider, state, nonce, cookie, code="captured")

    assert replay.status_code == 401


def test_the_flow_cookie_is_cleared_at_the_return(client_and_provider):
    """So pressing "back" does not re-run a sign-in behind the person."""
    client, provider = client_and_provider
    forget(ISSUER, "subject-42")

    state, nonce, cookie = begin(client)
    response = return_from_provider(client, provider, state, nonce, cookie)

    cleared = [value for value in response.headers.get_list("set-cookie")
               if value.startswith(oidc.FLOW_COOKIE)]
    assert cleared, "the attempt cookie is not cleared at the return"
    assert 'Max-Age=0' in cleared[0] or 'expires=' in cleared[0].lower()


def test_a_return_without_an_attempt_is_refused(client_and_provider):
    client, provider = client_and_provider

    client.cookies.clear()
    provider.next_token = provider.sign(provider.claims(nonce="whatever"))
    response = client.get("/api/auth/oidc/callback",
                          params={"code": "code", "state": "made-up"})

    assert response.status_code == 401


def test_every_refusal_answers_the_same(client_and_provider):
    """Different answers would say which check the prober already passed."""
    client, provider = client_and_provider

    answers = []

    client.cookies.clear()
    answers.append(client.get("/api/auth/oidc/callback",
                              params={"code": "c", "state": "no-attempt"}))

    state, nonce, cookie = begin(client)
    answers.append(return_from_provider(client, provider, state, "another-nonce", cookie))

    state, nonce, cookie = begin(client)
    provider.next_token = provider.sign(provider.claims(nonce=nonce, aud="somebody-else"))
    client.cookies.set(oidc.FLOW_COOKIE, cookie)
    answers.append(client.get("/api/auth/oidc/callback", params={"code": "c", "state": state}))

    state, nonce, cookie = begin(client)
    provider.next_token = provider.sign(
        provider.claims(nonce=nonce, exp=int(time.time()) - 60))
    client.cookies.set(oidc.FLOW_COOKIE, cookie)
    answers.append(client.get("/api/auth/oidc/callback", params={"code": "c", "state": state}))

    assert {response.status_code for response in answers} == {401}
    assert {response.json()["detail"] for response in answers} == {oidc.REFUSAL}


# --- who gets an account -------------------------------------------------------

def test_an_unknown_person_is_refused_without_a_policy(provider):
    """The default, and the reason it is the default."""
    forget(ISSUER, "subject-42")
    app = build(provider, policy=None)

    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as client:
        state, nonce, cookie = begin(client)
        response = return_from_provider(client, provider, state, nonce, cookie)

    assert response.status_code == 401
    assert DatabaseManagerV2.execute(
        "SELECT id FROM users WHERE external_id = :subject", {"subject": "subject-42"}) == []


def test_a_domain_policy_admits_its_own_and_nobody_else(provider):
    forget(ISSUER, "subject-42")
    forget(ISSUER, "outsider")
    app = build(provider, policy=oidc_policy.create_if_email_domain(["example.com"]))

    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as client:
        state, nonce, cookie = begin(client)
        admitted = return_from_provider(client, provider, state, nonce, cookie)

        client.cookies.clear()
        state, nonce, cookie = begin(client)
        refused = return_from_provider(client, provider, state, nonce, cookie,
                                       claims={"sub": "outsider", "email": "someone@other.com"})

    assert admitted.status_code == 303
    assert refused.status_code == 401


def test_a_blocked_account_is_refused(client_and_provider):
    """Proving who you are is not the same as being allowed in."""
    client, provider = client_and_provider
    forget(ISSUER, "subject-42")

    state, nonce, cookie = begin(client)
    return_from_provider(client, provider, state, nonce, cookie)
    DatabaseManagerV2.execute_commit(
        "UPDATE users SET status = 'blocked' WHERE external_id = :subject",
        {"subject": "subject-42"})

    client.cookies.clear()
    state, nonce, cookie = begin(client)
    response = return_from_provider(client, provider, state, nonce, cookie)

    assert response.status_code == 401


# --- roles ---------------------------------------------------------------------

def test_a_role_from_the_claims_becomes_a_role_here_and_is_taken_away_again(client_and_provider):
    """The reason a central sign-in is worth having at all."""
    client, provider = client_and_provider
    forget(ISSUER, "subject-42")
    DatabaseManagerV2.execute_commit(
        "DELETE FROM external_role_mappings WHERE auth_source = :source", {"source": ISSUER})
    DatabaseManagerV2.execute_commit(
        "INSERT INTO external_role_mappings (external_role_name, internal_permission_name, auth_source) "
        "VALUES (:role, :permission, :source)",
        {"role": "staff-admins", "permission": "admin", "source": ISSUER})

    state, nonce, cookie = begin(client)
    return_from_provider(client, provider, state, nonce, cookie,
                         claims={"groups": ["staff-admins"]})
    promoted = DatabaseManagerV2.execute_one(
        "SELECT role FROM users WHERE external_id = :subject", {"subject": "subject-42"})

    client.cookies.clear()
    state, nonce, cookie = begin(client)
    return_from_provider(client, provider, state, nonce, cookie, claims={"groups": []})
    demoted = DatabaseManagerV2.execute_one(
        "SELECT role FROM users WHERE external_id = :subject", {"subject": "subject-42"})

    assert promoted["role"] == "ADMIN"
    assert demoted["role"] == "CLIENT"
    assert DatabaseManagerV2.execute(
        "SELECT permission_name FROM user_permissions WHERE user_id = "
        "(SELECT id FROM users WHERE external_id = :subject)", {"subject": "subject-42"}) == []


def test_the_providers_tokens_are_not_written_down(client_and_provider):
    """They are keys to somebody's account elsewhere; we have no use for them."""
    client, provider = client_and_provider
    forget(ISSUER, "subject-42")

    state, nonce, cookie = begin(client)
    return_from_provider(client, provider, state, nonce, cookie)

    row = DatabaseManagerV2.execute_one(
        "SELECT * FROM users WHERE external_id = :subject", {"subject": "subject-42"})
    stored = " ".join(str(value) for value in dict(row).values())
    assert "opaque" not in stored          # the access token the fake provider issues
    assert provider.next_token not in stored
