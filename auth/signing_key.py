"""The key that signs access tokens, and the single place that resolves it.

The key used to live as a module constant next to the provider base class, and
half the code signed with that constant while the other half read the value out
of ``auth_config``. Nothing kept the two in step, and the constant shipped in the
repository: anyone who read it could mint a token for any account, including an
administrator's. The agent's sources are meant to be opened to host owners, so a
secret that lives in the source is a secret that is already published.

Resolution order is the environment first, ``config/auth.yaml`` second. The
environment is what a deployment actually controls; the YAML file is committed
with real-looking values and is an environment, not a template, so it is the
fallback rather than the source.

A key that is absent, too short, or left at one of the placeholder values from
the setup examples is refused rather than used. The refusal is what the caller
turns into a failed start: a deployment brought up with a placeholder must not
come up at all, because the alternative is discovering it the day someone signs
their own administrator token.
"""

import os
from typing import Mapping, Optional

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "SigningKeyUnavailable",
    "resolve_signing_key",
]

#: Where a deployment puts the key. Named in the refusal, so it stays a constant.
SIGNING_KEY_ENV = "SECRET_KEY"

#: Shortest key accepted. HS256 keys shorter than the digest they feed buy
#: nothing over a guessable passphrase, and 32 characters is what the token
#: generators in this repository already produce.
MINIMUM_KEY_LENGTH = 32

#: Values that appear in the setup examples of this repository and in the
#: documentation. A deployment that still carries one of them has not been
#: configured, whatever its length.
PLACEHOLDER_KEYS = frozenset({
    "your-secret-key-here-change-in-production",
    "your-jwt-secret-key-change-in-production",
    "your-secret-key-change-in-production",
    "your-very-secure-secret-key-change-this",
    "change-me",
    "changeme",
    "secret",
})


class SigningKeyUnavailable(RuntimeError):
    """No usable signing key: the message says which variable to set."""


def _configured_key(config: Optional[object]) -> Optional[str]:
    """Read the key out of the auth configuration, if it carries one."""
    if config is None:
        return None
    value = getattr(config, "jwt_secret", None)
    return value if isinstance(value, str) else None


def signing_key_problem(
        environ: Optional[Mapping[str, str]] = None,
        config: Optional[object] = None,
) -> Optional[str]:
    """Return why the signing key is unusable, or None when it is fine.

    Reports rather than raises, so the start-up check can name the problem and
    the resolver can raise on the same verdict.
    """
    if environ is None:
        environ = os.environ
    if config is None:
        config = _auth_config()

    candidate = environ.get(SIGNING_KEY_ENV) or _configured_key(config) or ""
    candidate = candidate.strip()

    if not candidate:
        return (
            f"Token signing secret is not set: set the environment variable "
            f"{SIGNING_KEY_ENV} to a value at least {MINIMUM_KEY_LENGTH} characters long."
        )

    if candidate in PLACEHOLDER_KEYS:
        return (
            f"Token signing secret is left at the example configuration value: "
            f"set the environment variable {SIGNING_KEY_ENV} to your own value "
            f"at least {MINIMUM_KEY_LENGTH} characters long."
        )

    if len(candidate) < MINIMUM_KEY_LENGTH:
        return (
            f"Token signing secret is shorter than {MINIMUM_KEY_LENGTH} characters: "
            f"set the environment variable {SIGNING_KEY_ENV} to a longer value."
        )

    return None


def resolve_signing_key(
        environ: Optional[Mapping[str, str]] = None,
        config: Optional[object] = None,
) -> str:
    """Return the key that signs and verifies access tokens.

    Not cached: reading an environment variable costs nothing next to the
    signature that follows it, and a cache would make the key of a running
    process depend on which test imported it first.
    """
    if environ is None:
        environ = os.environ
    if config is None:
        config = _auth_config()

    problem = signing_key_problem(environ, config)
    if problem:
        raise SigningKeyUnavailable(problem)

    return (environ.get(SIGNING_KEY_ENV) or _configured_key(config) or "").strip()


def _auth_config() -> Optional[object]:
    """The loaded auth configuration, or None when it cannot be imported.

    Imported lazily: this module is imported by the provider base class, which
    the configuration module does not depend on, and a module-level import would
    close that loop.
    """
    try:
        from keepup.auth.config import auth_config
    except Exception:  # pragma: no cover - configuration is optional here
        return None
    return auth_config
