"""Where the accounts of this deployment live.

One source: the application's own ``users`` table. Signing in through an
external identity provider is not a second source and not a second provider --
the account is still here, the provider only proves who is knocking. That path
is configured by the application through ``KeepupSettings.oidc`` and lives in
:mod:`keepup.auth.oidc`.

Until task 170 there was a second provider, an Apache Directory one, which
wrote into the directory as much as it read from it. It was never enabled on
any deployment and has been removed; ``provider: "ldap"`` now stops the start
rather than falling back to local, because a deployment expecting a directory
and silently getting local sign-in is an open door where nobody looked for one.
"""

import os
from enum import Enum
from pathlib import Path
from typing import Any, Dict

import yaml
from pydantic import BaseModel, ConfigDict, Field

from keepup.roles import ROLE_CLIENT

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "AuthConfig",
    "AuthProviderType",
    # Assigned inside a try at module level: the configuration is read on
    # import, and an application takes the object rather than building one.
    "auth_config",
]

#: Kept as an enum although it has one member: the configuration file says a
#: name, and a name has to be validated against something.
class AuthProviderType(str, Enum):
    """Where a deployment keeps its accounts."""

    LOCAL = "local"


#: Removed providers, named so that a deployment still asking for one is told
#: what happened instead of quietly getting something else.
RETIRED_PROVIDERS = {
    "ldap": "the Apache Directory provider was removed in task 170; put an "
            "OpenID Connect provider in front of your directory (Keycloak, for "
            "example) and configure the application with it",
}


class RetiredProvider(RuntimeError):
    """Raised at start-up for a provider that no longer exists."""


class AuthConfig(BaseModel):
    """The authentication settings of this deployment."""

    model_config = ConfigDict(validate_default=True)

    provider: AuthProviderType = Field(AuthProviderType.LOCAL,
                                       description="Where the accounts live")
    enabled: bool = Field(True, description="Whether authentication is enabled")
    default_role: str = Field(ROLE_CLIENT, description="Default role")

    #: No default. The placeholder that used to stand here is one of the
    #: values keepup.auth.signing_key refuses outright, so it never worked as
    #: a default -- it only put a key-shaped string into the package.
    jwt_secret: str = Field("", description="JWT signing key; supplied by the deployment")
    jwt_algorithm: str = Field("HS256", description="JWT algorithm")
    jwt_expire_minutes: int = Field(1440, description="Token lifetime, in minutes")

    @classmethod
    def from_yaml(cls, config_path: Path = Path("config/auth.yaml")) -> "AuthConfig":
        """Load the configuration from a YAML file."""
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f) or {}

        return cls.from_mapping(config_data)

    @classmethod
    def from_mapping(cls, config_data: Dict[str, Any]) -> "AuthConfig":
        """Build the configuration from already parsed data."""
        section = config_data.get("auth", config_data)
        provider_str = str(section.get("provider", "local")).lower()

        if provider_str in RETIRED_PROVIDERS:
            raise RetiredProvider(
                f"config/auth.yaml asks for the '{provider_str}' provider: "
                f"{RETIRED_PROVIDERS[provider_str]}")

        try:
            provider = AuthProviderType(provider_str)
        except ValueError:
            print(f"Unknown provider '{provider_str}', falling back to 'local'")
            provider = AuthProviderType.LOCAL

        jwt_config = config_data.get("jwt", {}) or {}

        return cls(
            provider=provider,
            enabled=section.get("enabled", True),
            default_role=section.get("default_role", ROLE_CLIENT),
            jwt_secret=jwt_config.get("secret", ""),
            jwt_algorithm=jwt_config.get("algorithm", "HS256"),
            jwt_expire_minutes=jwt_config.get("expire_minutes", 1440),
        )


try:
    config_path = Path(os.environ.get("AUTH_CONFIG_PATH", "config/auth.yaml"))
    auth_config = AuthConfig.from_yaml(config_path)
    print(f"Authentication configuration loaded from {config_path}")
    print(f"Provider in use: {auth_config.provider}")
except RetiredProvider:
    # Never swallowed: the whole point is that this deployment must not come up
    # signing people in some other way than it asked for.
    raise
except FileNotFoundError as e:
    print(f"Warning: {e}. Falling back to the default configuration.")
    auth_config = AuthConfig()
except Exception as e:
    print(f"Could not load the configuration: {e}. Falling back to the default one.")
    auth_config = AuthConfig()
