"""The configured provider at work: its calls, their timeout and their caches.

One runtime per process, set when the application is assembled
(:func:`configure`) and closed at shutdown. It is module state, the way the
panel gate and the password rule are: routes are generated long before any
request, and they reach the provider through here.

**The token cache** keeps what the provider said about a token so that a burst
of requests with one token costs the other system one call. It is in the memory
of this replica -- sharing it through the database would cost more than asking
the provider -- bounded in size, keyed by the SHA-256 of the token rather than
the token, and never keeps an answer longer than the configuration allows or
the identity itself lives. A refusal is kept too, briefly, so that a stream of
garbage tokens is not relayed to somebody else's server one by one.

**Every call is timed out**, and anything a plugin raises other than a refusal
counts as the provider being unavailable -- see keepup/auth/identity/contract.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Tuple

from keepup.auth.identity import accounts
from keepup.auth.identity.config import IdentityProviderConfig, resolve
from keepup.auth.identity.contract import (
    AccessRequest,
    ExternalIdentity,
    IdentityProvider,
    IdentityRejected,
    ProviderUnavailable,
)
from keepup.auth.identity.loader import build_provider

logger = logging.getLogger(__name__)

#: How many answers a cache keeps before the oldest go.
CACHE_ENTRIES = 4096
#: The longest a refusal is kept, whatever the positive cache is set to.
REFUSAL_CACHE_SECONDS = 5


class _Cache:
    """A small LRU with a deadline per entry, on a clock that can be replaced."""

    def __init__(self, clock: Callable[[], float], size: int = CACHE_ENTRIES):
        self._clock = clock
        self._size = size
        self._entries: "OrderedDict[Any, Tuple[float, Any]]" = OrderedDict()

    def get(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return None
        until, value = entry
        if until <= self._clock():
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return entry

    def put(self, key, value, seconds: float) -> None:
        if seconds <= 0:
            return
        self._entries[key] = (self._clock() + seconds, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._size:
            self._entries.popitem(last=False)

    def keys(self):
        return list(self._entries)

    def __len__(self):
        return len(self._entries)

    def clear(self):
        self._entries.clear()


def fingerprint(token: str) -> str:
    """What a token is remembered by: never the token itself."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _seconds_left(expires_at: Optional[datetime]) -> Optional[float]:
    """Seconds until the identity stops being vouched for, or None for no end."""
    if expires_at is None:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return (expires_at - datetime.now(timezone.utc)).total_seconds()


class IdentityRuntime:
    """One deployment's provider and the rules its answers are used by."""

    def __init__(self, config: IdentityProviderConfig, provider: IdentityProvider,
                 clock: Callable[[], float] = time.monotonic):
        self.config = config
        self.provider = provider
        self.tokens = _Cache(clock)
        self.decisions = _Cache(clock)

    # --- calls ---------------------------------------------------------------

    async def _call(self, what: str, call: Callable[[], Awaitable[Any]]) -> Any:
        """Await one call of the plugin under the timeout, translating its failures."""
        try:
            return await asyncio.wait_for(call(), timeout=self.config.timeout_seconds)
        except IdentityRejected:
            raise
        except ProviderUnavailable as error:
            logger.warning("Identity provider %s unavailable for %s: %s",
                           self.config.name, what, error)
            raise
        except asyncio.TimeoutError:
            logger.warning("Identity provider %s did not answer %s within %ss",
                           self.config.name, what, self.config.timeout_seconds)
            raise ProviderUnavailable(f"{what} timed out")
        except Exception as error:
            # The type and not the text: the text of an error in a plugin can
            # carry what the plugin held, a token or a key among it.
            logger.error("Identity provider %s failed in %s: %s",
                         self.config.name, what, type(error).__name__)
            raise ProviderUnavailable(f"{what} failed: {type(error).__name__}")

    @staticmethod
    def _checked(identity: Any) -> ExternalIdentity:
        if not isinstance(identity, ExternalIdentity):
            raise ProviderUnavailable("the provider returned something other than an identity")
        if not identity.subject or not str(identity.subject).strip():
            raise IdentityRejected("the provider vouched for an identity with no subject")
        left = _seconds_left(identity.expires_at)
        if left is not None and left <= 0:
            raise IdentityRejected("the identity the provider returned has already expired")
        return identity

    # --- tokens --------------------------------------------------------------

    async def identity_for_token(self, token: str) -> ExternalIdentity:
        """Whose token this is, from the cache or from the provider."""
        key = fingerprint(token)
        cached = self.tokens.get(key)
        if cached is not None:
            _, value = cached
            if value is None:
                raise IdentityRejected("refused a moment ago")
            return value

        try:
            identity = self._checked(
                await self._call("verify_token", lambda: self.provider.verify_token(token)))
        except IdentityRejected:
            self.tokens.put(key, None, min(REFUSAL_CACHE_SECONDS, self.config.token_cache_seconds))
            raise

        lifetime = float(self.config.token_cache_seconds)
        left = _seconds_left(identity.expires_at)
        if left is not None:
            lifetime = min(lifetime, left)
        self.tokens.put(key, identity, lifetime)
        return identity

    async def identity_for_password(self, username: str, password: str) -> ExternalIdentity:
        """Whose credentials these are. Never cached: a password is not a key to keep."""
        return self._checked(await self._call(
            "verify_password", lambda: self.provider.verify_password(username, password)))

    async def user_for_identity(self, identity: ExternalIdentity) -> Dict[str, Any]:
        """The account of this identity, roles in step, read off the event loop."""
        return await asyncio.to_thread(accounts.account_for, self.config, identity)

    def signed_in(self, account: Mapping[str, Any], identity: ExternalIdentity) -> Dict[str, Any]:
        """The user a route receives when the caller came in with a token of the provider.

        No session of this framework: the token is the other system's, and so
        is its end. What a route may want to know about the caller travels with
        it -- where they came from and what the provider said.
        """
        user = dict(account)
        user["authenticated_by"] = self.config.name
        user["identity"] = identity.public()
        user["session_id"] = None
        user["session_started_at"] = None
        # Naive UTC, like the expiry of a token of this framework: a handler that
        # compares the two must not trip over one being aware and one not.
        expires = identity.expires_at
        if expires is not None and expires.tzinfo is not None:
            expires = expires.astimezone(timezone.utc).replace(tzinfo=None)
        user["token_expires_at"] = expires
        return user

    # --- decisions -----------------------------------------------------------

    async def decide(self, user: Mapping[str, Any], request: AccessRequest) -> bool:
        """What the provider says about this person taking this action."""
        public = user.get("identity")
        identity = ExternalIdentity.from_public(public) if public else None
        key = (user.get("id"), user.get("authenticated_by"), request.permission,
               request.method, request.path)
        cached = self.decisions.get(key)
        if cached is not None:
            return cached[1]
        allowed = await self._call(
            "decide", lambda: self.provider.decide(identity, user, request))
        if not isinstance(allowed, bool):
            raise ProviderUnavailable("the provider's decision is not a yes or a no")
        self.decisions.put(key, allowed, self.config.decision_cache_seconds)
        return allowed

    async def close(self) -> None:
        try:
            await self.provider.close()
        except Exception as error:
            logger.warning("Identity provider %s did not close cleanly: %s",
                           self.config.name, type(error).__name__)


# --- the process's runtime ----------------------------------------------------

_runtime: Optional[IdentityRuntime] = None


def current() -> Optional[IdentityRuntime]:
    """The configured runtime, or None when the deployment has no provider."""
    return _runtime


def install(runtime: Optional[IdentityRuntime]) -> None:
    """Make ``runtime`` the process's one. For configure() and for tests."""
    global _runtime
    _runtime = runtime


def configure(from_code: Optional[IdentityProviderConfig] = None,
              path=None, discover=None) -> Optional[IdentityRuntime]:
    """Build the deployment's runtime, or none; raise on any doubt about it.

    Replaces whatever the previous application in this process configured: an
    application without a provider must not inherit one.
    """
    config = resolve(from_code, path)
    if config is None:
        install(None)
        return None
    provider = build_provider(config, discover)
    runtime = IdentityRuntime(config, provider)
    install(runtime)
    logger.info("Identity provider: %s (%s) -- %s; new accounts: %s",
                config.name, type(provider).__name__, ", ".join(config.modes()),
                config.new_accounts)
    return runtime


async def shutdown() -> None:
    """Close the provider of this process, if there is one."""
    runtime = _runtime
    if runtime is not None:
        await runtime.close()
