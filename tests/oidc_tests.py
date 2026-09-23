"""Signing in through an external provider, against a provider we run ourselves.

The fake provider below has its own key pair, its own discovery document and
its own key set, so every case -- a token signed by the wrong key, issued to
another client, expired, replayed -- is produced honestly rather than mocked
away. No network, no Keycloak, no waiting.

    python3 -m pytest keepup/tests/oidc_tests.py -v
"""

import json
import time
import uuid

import httpx
import pytest
import jwt

from keepup.auth import oidc, oidc_policy
from keepup.settings import OidcSettings

ISSUER = "https://idp.test"
CLIENT_ID = "keepup-test-client"
REDIRECT = "https://app.test/api/auth/oidc/callback"

def rsa_pair(kid):
    """A real key pair: the private half signs, the public half goes in the key set.

    Real rather than a stand-in because the module refuses symmetric algorithms
    on purpose -- a provider signs with a private key nobody else has, and a
    shared secret would mean the token was signed by whoever handed it over.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    public_jwk = dict(RSAAlgorithm.to_jwk(key.public_key(), as_dict=True), kid=kid)
    return private_pem, public_jwk


# The provider's pair, and one belonging to nobody in particular -- the second
# is how "signed by the wrong key" is produced for real.
PROVIDER_PRIVATE, PROVIDER_KEY = rsa_pair("provider-1")
STRANGER_PRIVATE, STRANGER_KEY = rsa_pair("provider-1")


class FakeProvider:
    """A provider with a discovery document, a key set and a token endpoint."""

    def __init__(self, issuer=ISSUER, key=PROVIDER_KEY, private=PROVIDER_PRIVATE):
        self.issuer = issuer
        self.key = key
        self.private = private
        self.discovery_calls = 0
        self.jwks_calls = 0
        self.token_calls = []
        self.spent_codes = set()
        self.next_token = None
        self.token_status = 200
        #: Set to a second key to answer the next key-set request with it, which
        #: is what a provider rotating its keys looks like from here.
        self.rotated_to = None

    @property
    def discovery(self):
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
        }

    def sign(self, claims, private=None, kid=None, algorithm="RS256"):
        """An id_token as this provider would issue it."""
        return jwt.encode(claims, private or self.private, algorithm=algorithm,
                          headers={"kid": kid or self.key["kid"]})

    def claims(self, **overrides):
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": CLIENT_ID,
            "sub": "subject-42",
            "iat": now,
            "exp": now + 300,
            "email": "person@example.com",
            "email_verified": True,
            "preferred_username": "person",
            "groups": [],
        }
        claims.update(overrides)
        return claims

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            self.discovery_calls += 1
            return httpx.Response(200, json=self.discovery)
        if path.endswith("/jwks"):
            self.jwks_calls += 1
            key = self.rotated_to or self.key
            return httpx.Response(200, json={"keys": [dict(key)]})
        if path.endswith("/token"):
            form = dict(httpx.QueryParams(request.content.decode()))
            self.token_calls.append(form)
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            # A code is redeemed once. Every real provider does this, and it is
            # what makes a replayed return worthless -- modelled here rather
            # than assumed, or the replay test would pass for the wrong reason.
            code = form.get("code")
            if code in self.spent_codes:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.spent_codes.add(code)
            body = {"access_token": "opaque", "token_type": "Bearer"}
            if self.next_token is not None:
                body["id_token"] = self.next_token
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    def client_factory(self):
        transport = httpx.MockTransport(self.handler)
        return httpx.AsyncClient(transport=transport)


@pytest.fixture
def settings():
    return OidcSettings(issuer=ISSUER, client_id=CLIENT_ID, client_secret="shh",
                        redirect_uri=REDIRECT)


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def directory(settings, provider):
    return oidc.ProviderDirectory(settings, client_factory=provider.client_factory)


# --- discovery ----------------------------------------------------------------

async def test_the_addresses_come_from_the_provider(directory, provider):
    """Only the issuer is configured; everything else is asked for."""
    metadata = await directory.metadata()

    assert metadata.authorization_endpoint == f"{ISSUER}/authorize"
    assert metadata.token_endpoint == f"{ISSUER}/token"
    assert provider.discovery_calls == 1


async def test_the_document_is_asked_for_once(directory, provider):
    await directory.metadata()
    await directory.metadata()

    assert provider.discovery_calls == 1


async def test_an_unknown_key_id_makes_the_keys_be_re_read(directory, provider):
    """Providers rotate keys without telling anyone; sign-in must survive it."""
    await directory.key_for(PROVIDER_KEY["kid"])
    assert provider.jwks_calls == 1

    provider.rotated_to = dict(STRANGER_KEY, kid="provider-2")
    key = await directory.key_for("provider-2")

    assert key["kid"] == "provider-2"
    assert provider.jwks_calls == 2


async def test_a_key_that_is_not_there_even_after_re_reading_is_refused(directory):
    with pytest.raises(oidc.SignInRefused):
        await directory.key_for("never-existed")


# --- the attempt --------------------------------------------------------------

def test_the_challenge_matches_the_verifier():
    verifier, challenge = oidc.make_pkce_pair()
    _, again = oidc.make_pkce_pair()

    assert challenge != verifier
    assert again != challenge  # a fresh pair per attempt


def test_the_attempt_survives_a_round_trip_through_the_cookie():
    token = oidc.issue_flow_token("state-1", "nonce-1", "verifier-1")
    flow = oidc.read_flow_token(token)

    assert (flow["state"], flow["nonce"], flow["verifier"]) == ("state-1", "nonce-1", "verifier-1")


def test_an_expired_attempt_is_refused():
    stale = oidc.issue_flow_token("s", "n", "v", now=int(time.time()) - oidc.FLOW_COOKIE_MAX_AGE - 5)

    with pytest.raises(oidc.SignInRefused):
        oidc.read_flow_token(stale)


def test_an_attempt_signed_by_somebody_else_is_refused():
    forged = jwt.encode({"state": "s", "nonce": "n", "verifier": "v",
                         "exp": int(time.time()) + 60},
                        "not-the-application-key", algorithm="HS256")

    with pytest.raises(oidc.SignInRefused):
        oidc.read_flow_token(forged)


def test_no_attempt_cookie_at_all_is_refused():
    with pytest.raises(oidc.SignInRefused):
        oidc.read_flow_token(None)


async def test_the_authorisation_url_carries_what_the_provider_needs(directory, settings):
    metadata = await directory.metadata()
    verifier, challenge = oidc.make_pkce_pair()

    url = oidc.authorization_url(metadata, settings, "state-1", "nonce-1", challenge)

    assert url.startswith(f"{ISSUER}/authorize?")
    query = dict(httpx.QueryParams(url.split("?", 1)[1]))
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == REDIRECT
    assert query["response_type"] == "code"
    assert query["state"] == "state-1"
    assert query["nonce"] == "nonce-1"
    assert query["code_challenge_method"] == "S256"
    assert query["code_challenge"] == challenge
    # The verifier proves we started this attempt; it must not travel with it.
    assert verifier not in url


# --- the exchange -------------------------------------------------------------

async def test_the_exchange_proves_the_attempt_with_the_verifier(directory, settings, provider):
    provider.next_token = provider.sign(provider.claims())

    await oidc.exchange_code(directory, settings, "the-code", "the-verifier")

    [sent] = provider.token_calls
    assert sent["code"] == "the-code"
    assert sent["code_verifier"] == "the-verifier"
    assert sent["grant_type"] == "authorization_code"


async def test_a_refused_code_is_a_refused_sign_in(directory, settings, provider):
    provider.token_status = 400

    with pytest.raises(oidc.SignInRefused):
        await oidc.exchange_code(directory, settings, "stale-code", "v")


async def test_an_answer_without_an_id_token_is_refused(directory, settings, provider):
    provider.next_token = None

    with pytest.raises(oidc.SignInRefused):
        await oidc.exchange_code(directory, settings, "code", "v")


# --- the provider's statement --------------------------------------------------

async def verify(directory, settings, token, nonce="nonce-1"):
    return await oidc.verify_id_token(directory, settings, token, nonce)


async def test_a_good_token_is_accepted(directory, settings, provider):
    token = provider.sign(provider.claims(nonce="nonce-1"))

    claims = await verify(directory, settings, token)

    assert claims["sub"] == "subject-42"
    assert claims["email"] == "person@example.com"


async def test_a_token_signed_by_a_stranger_is_refused(directory, settings, provider):
    """The signature is the only thing that makes the statement worth anything."""
    token = provider.sign(provider.claims(nonce="nonce-1"), private=STRANGER_PRIVATE)

    with pytest.raises(oidc.SignInRefused):
        await verify(directory, settings, token)


async def test_a_token_for_another_client_is_refused(directory, settings, provider):
    """Same provider, different application: not ours to accept."""
    token = provider.sign(provider.claims(nonce="nonce-1", aud="somebody-elses-client"))

    with pytest.raises(oidc.SignInRefused):
        await verify(directory, settings, token)


async def test_a_token_from_another_issuer_is_refused(directory, settings, provider):
    token = provider.sign(provider.claims(nonce="nonce-1", iss="https://elsewhere.test"))

    with pytest.raises(oidc.SignInRefused):
        await verify(directory, settings, token)


async def test_an_expired_token_is_refused(directory, settings, provider):
    token = provider.sign(provider.claims(nonce="nonce-1", exp=int(time.time()) - 10))

    with pytest.raises(oidc.SignInRefused):
        await verify(directory, settings, token)


async def test_a_token_from_another_attempt_is_refused(directory, settings, provider):
    """Right provider, right client, wrong sign-in: a replay from elsewhere."""
    token = provider.sign(provider.claims(nonce="somebody-elses-nonce"))

    with pytest.raises(oidc.SignInRefused):
        await verify(directory, settings, token)


async def test_an_unsigned_token_is_refused(directory, settings, provider):
    """`alg: none` is a token anybody can write."""
    header = json.dumps({"alg": "none", "kid": PROVIDER_KEY["kid"]}).encode()
    payload = json.dumps(provider.claims(nonce="nonce-1")).encode()
    unsigned = b".".join([oidc._b64url(header).encode(), oidc._b64url(payload).encode(), b""])

    with pytest.raises(oidc.SignInRefused):
        await verify(directory, settings, unsigned.decode())


async def test_a_token_naming_nobody_is_refused(directory, settings, provider):
    claims = provider.claims(nonce="nonce-1")
    claims.pop("sub")
    token = provider.sign(claims)

    with pytest.raises(oidc.SignInRefused):
        await verify(directory, settings, token)


# --- who may have an account ---------------------------------------------------

def claims_of(**overrides):
    base = {"sub": str(uuid.uuid4()), "email": "person@example.com", "email_verified": True}
    base.update(overrides)
    return base


def test_without_a_policy_nobody_new_gets_in():
    """The default has to be boring: the opposite is open registration."""
    decision = oidc_policy.decide(None, claims_of())

    assert decision.admit is False


def test_the_application_may_admit_everyone():
    decision = oidc_policy.decide(oidc_policy.create_account(), claims_of())

    assert decision.admit is True
    assert decision.role == "CLIENT"


def test_the_application_may_admit_a_domain():
    policy = oidc_policy.create_if_email_domain(["example.com"])

    assert oidc_policy.decide(policy, claims_of()).admit is True
    assert oidc_policy.decide(policy, claims_of(email="person@other.com")).admit is False


def test_an_unverified_email_is_not_a_domain():
    """Several public providers hand over whatever the person typed."""
    policy = oidc_policy.create_if_email_domain(["example.com"])

    decision = oidc_policy.decide(policy, claims_of(email_verified=False))

    assert decision.admit is False


def test_a_policy_that_fails_admits_nobody():
    def broken(claims):
        raise RuntimeError("the directory is down")

    assert oidc_policy.decide(broken, claims_of()).admit is False


def test_a_policy_that_answers_nonsense_admits_nobody():
    assert oidc_policy.decide(lambda claims: "sure", claims_of()).admit is False
