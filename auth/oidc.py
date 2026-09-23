"""Signing in through an external identity provider, over OpenID Connect.

The person proves who they are to their provider, and this application never
sees their password. What comes back is a code, which is exchanged for an
``id_token`` -- a signed statement by the provider about who just signed in.

Two things are worth knowing before reading further.

**Everything is checked before anybody is believed.** The order is: the state
we issued, then the exchange, then the signature against the provider's keys,
then issuer, audience, expiry and nonce. A failure at any point answers exactly
like a failure at any other; the reason goes to the log. Different answers for
different failures would tell whoever is probing which check they got past.

**Nothing of the provider's is kept.** The access and refresh tokens are not
stored: they are keys to somebody's account at the provider, and this
application has no business holding them. The ``id_token`` is read once and
dropped. What is kept is the pair (issuer, subject) -- who this is -- and that
is the whole point of the exchange.
"""

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWTError as JWTError

from keepup.auth.signing_key import resolve_signing_key

logger = logging.getLogger(__name__)

#: The cookie carrying the state of one sign-in attempt. SameSite=Lax rather
#: than Strict on purpose: the return from the provider is a navigation from
#: another site, and Strict would withhold the cookie exactly then -- that is,
#: always.
FLOW_COOKIE = "ss_oidc"
FLOW_COOKIE_MAX_AGE = 600

#: Symmetric algorithms and "none" are never accepted for an id_token: the
#: provider signs with its private key, and anything else means the token was
#: signed by whoever handed it to us.
ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256")

#: The one answer every refusal gives. See the module docstring.
REFUSAL = "Sign-in failed"


class SignInRefused(Exception):
    """Raised for every failed check; the reason is for the log, not the caller."""


@dataclass
class ProviderMetadata:
    """What the provider says about itself, from its discovery document."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


class ProviderDirectory:
    """The provider's metadata and signing keys, kept for the process.

    Held in memory rather than in the database: none of it is secret, losing it
    costs one request, and a shared copy would need invalidating across
    replicas -- which costs more than it saves. Keys are re-read when a token
    arrives signed by a key id we do not know, because providers rotate keys
    without telling anyone and sign-in must not stay broken until a restart.
    """

    def __init__(self, settings, client_factory=None):
        self._settings = settings
        self._client_factory = client_factory or self._default_client
        self._metadata: Optional[ProviderMetadata] = None
        self._keys: Dict[str, Dict[str, Any]] = {}

    def _default_client(self):
        return httpx.AsyncClient(timeout=self._settings.timeout_seconds)

    async def _get_json(self, url: str) -> Dict[str, Any]:
        async with self._client_factory() as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    async def metadata(self) -> ProviderMetadata:
        """The discovery document, fetched once."""
        if self._metadata is None:
            url = self._settings.issuer.rstrip("/") + "/.well-known/openid-configuration"
            document = await self._get_json(url)
            self._metadata = ProviderMetadata(
                issuer=document["issuer"],
                authorization_endpoint=document["authorization_endpoint"],
                token_endpoint=document["token_endpoint"],
                jwks_uri=document["jwks_uri"],
            )
            logger.info(f"OIDC provider discovered: {self._metadata.issuer}")
        return self._metadata

    async def key_for(self, kid: Optional[str], refresh: bool = False) -> Dict[str, Any]:
        """The signing key with this id, re-reading the key set if it is new."""
        if refresh or not self._keys:
            metadata = await self.metadata()
            document = await self._get_json(metadata.jwks_uri)
            self._keys = {key.get("kid"): key for key in document.get("keys", [])}
            logger.debug(f"OIDC keys loaded: {sorted(k for k in self._keys if k)}")

        if kid in self._keys:
            return self._keys[kid]
        if not refresh:
            # A key we have not seen: the provider has rotated, not lied.
            return await self.key_for(kid, refresh=True)
        raise SignInRefused(f"the provider has no signing key {kid!r}")

    def forget(self):
        """Drop what is cached; for tests and for a changed configuration."""
        self._metadata = None
        self._keys = {}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def make_pkce_pair() -> Tuple[str, str]:
    """A verifier that stays here and the challenge that goes to the provider."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def issue_flow_token(state: str, nonce: str, verifier: str, now: Optional[int] = None) -> str:
    """Pack one sign-in attempt into a short-lived signed value.

    In a cookie rather than a row: a table of unfinished sign-ins would need a
    row per attempt and a sweep for the abandoned ones, to hold what a signed
    value holds by itself. One-time use is enforced by clearing the cookie at
    the return.
    """
    issued = now if now is not None else int(time.time())
    return jwt.encode(
        {"state": state, "nonce": nonce, "verifier": verifier,
         "iat": issued, "exp": issued + FLOW_COOKIE_MAX_AGE},
        resolve_signing_key(),
        algorithm="HS256",
    )


def read_flow_token(token: Optional[str]) -> Dict[str, Any]:
    """Unpack the attempt, refusing anything expired or not ours."""
    if not token:
        raise SignInRefused("no sign-in attempt cookie")
    try:
        return jwt.decode(token, resolve_signing_key(), algorithms=["HS256"])
    except JWTError as error:
        raise SignInRefused(f"sign-in attempt cookie rejected: {error}")


def authorization_url(metadata: ProviderMetadata, settings, state: str, nonce: str,
                      challenge: str) -> str:
    """Where to send the browser to have the person identified."""
    query = {
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "scope": " ".join(settings.scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{metadata.authorization_endpoint}?{urlencode(query)}"


async def exchange_code(directory: ProviderDirectory, settings, code: str,
                        verifier: str) -> Dict[str, Any]:
    """Trade the code for tokens, proving we started this attempt.

    The verifier proves it: it never left this server, and only the party that
    began the attempt can produce it.
    """
    metadata = await directory.metadata()
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.redirect_uri,
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "code_verifier": verifier,
    }
    async with directory._client_factory() as client:
        response = await client.post(metadata.token_endpoint, data=form)
    if response.status_code != 200:
        raise SignInRefused(f"the provider refused the code: {response.status_code}")
    tokens = response.json()
    if "id_token" not in tokens:
        raise SignInRefused("the provider returned no id_token")
    return tokens


async def verify_id_token(directory: ProviderDirectory, settings, id_token: str,
                          nonce: str) -> Dict[str, Any]:
    """Check the provider's statement, and refuse it on any doubt.

    Checked here rather than trusted: an id_token is a bearer statement, and
    the only thing making it worth anything is that the signature belongs to
    the issuer we were configured with.
    """
    try:
        header = jwt.get_unverified_header(id_token)
    except JWTError as error:
        raise SignInRefused(f"unreadable id_token: {error}")

    algorithm = header.get("alg")
    if algorithm not in ALLOWED_ALGORITHMS:
        raise SignInRefused(f"id_token signed with {algorithm!r}")

    description = await directory.key_for(header.get("kid"))
    metadata = await directory.metadata()

    # The key set gives descriptions of keys, not keys. Turning one into a key
    # is a step of its own here, and so is its refusal: a description that
    # cannot be read is a provider publishing something we do not understand,
    # which is worth saying plainly rather than reporting as a signature that
    # did not match.
    try:
        key = jwt.PyJWK.from_dict(description)
    except Exception as error:
        raise SignInRefused(f"unreadable signing key {header.get('kid')!r}: {error}")

    try:
        claims = jwt.decode(
            id_token,
            key,
            algorithms=[algorithm],
            audience=settings.client_id,
            issuer=metadata.issuer,
        )
    except JWTError as error:
        raise SignInRefused(f"id_token rejected: {error}")

    if claims.get("nonce") != nonce:
        raise SignInRefused("id_token carries another attempt's nonce")
    if not claims.get("sub"):
        raise SignInRefused("id_token names no subject")

    return claims
