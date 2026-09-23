"""Which provider answers "is this the right password".

Since task 170 there is one answer -- accounts live in this application's own
table -- but the choice stays a lookup rather than a call of LocalAuthProvider:
`config/auth.yaml` names a provider, and a name that no longer exists has to
stop the start with a message instead of silently becoming the local one.
"""

from keepup.auth.providers.base import AuthProvider
from keepup.auth.config import auth_config, AuthProviderType
from keepup.auth.providers.local import LocalAuthProvider


class AuthProviderFactory:
    """Factory creating authentication providers."""

    #: One entry since task 170: accounts live in this application's table.
    #: Signing in through an external provider is a separate path, not another
    #: entry here -- that path never receives a password to check.
    _providers = {
        AuthProviderType.LOCAL: LocalAuthProvider,
    }

    @classmethod
    def get_provider(cls) -> AuthProvider:
        """Return the currently configured authentication provider."""
        provider_class = cls._providers.get(auth_config.provider, LocalAuthProvider)
        return provider_class()