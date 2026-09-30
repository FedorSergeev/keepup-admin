"""The ``identity_provider`` section: which plugin, with what, doing what.

The section sits in the authentication file -- ``AUTH_CONFIG_PATH``, by default
``config/auth.yaml`` -- next to ``auth`` and ``jwt``, because in a deployment
that file is one mounted configmap and authentication is configured in one
place. It is read when the application is assembled rather than on import, and
unlike the rest of that file a problem in it stops the start: a deployment
inside somebody else's system that quietly came up without its provider would
be letting people in some other way than it was told to.

Secrets do not belong in a configmap, so a value of the plugin's ``settings``
may name an environment variable -- ``${CORP_AS_SECRET}`` -- and the variable is
read at start-up. A variable that is not set stops the start; the message names
the variable and never a value.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from keepup.roles import ROLE_CLIENT

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "AUTH_CONFIG_ENV",
    "IdentityProviderConfig",
    "IdentityProviderMisconfigured",
    "SECTION",
    "load_section",
    "resolve",
]

#: The variable naming the authentication file; the same one keepup.auth.config reads.
AUTH_CONFIG_ENV = "AUTH_CONFIG_PATH"
DEFAULT_AUTH_CONFIG = "config/auth.yaml"
SECTION = "identity_provider"

_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class IdentityProviderMisconfigured(RuntimeError):
    """Raised at start-up for a section the framework cannot act on."""


class IdentityProviderConfig(BaseModel):
    """One deployment's provider, and what it is used for."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True, frozen=True)

    #: The source of the accounts this provider vouches for: written into
    #: ``users.auth_source``, so renaming it later orphans every account made
    #: under the old name.
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    #: ``package.module:Class``, the name of an entry point in the
    #: ``keepup.identity_providers`` group, or -- from code only -- a provider
    #: already built.
    plugin: Any
    #: Handed to the plugin's constructor, environment references substituted.
    settings: Dict[str, Any] = Field(default_factory=dict)

    #: Take the other system's tokens on every route that signs the caller in.
    accept_tokens: bool = True
    #: Check a name that has no local password with the other system.
    password_sign_in: bool = False
    #: Who decides a route's ``permission``: this application, or the provider.
    authorization: Literal["local", "provider"] = "local"
    #: What happens to somebody the provider vouches for and this application
    #: has never seen. Refused by default: switching a provider on must not turn
    #: into open registration.
    new_accounts: Literal["refuse", "create"] = "refuse"
    #: The role of somebody none of whose roles is mapped.
    default_role: str = ROLE_CLIENT
    #: The other system's role -> one role here, or several.
    role_mapping: Dict[str, Union[str, List[str]]] = Field(default_factory=dict)

    #: How long a confirmed token is believed without asking again. 0 asks
    #: every time. It is also how long a revocation there may go unseen here.
    token_cache_seconds: int = Field(60, ge=0)
    #: How long a decision about a right is kept. 0 -- the default -- asks the
    #: provider about every request.
    decision_cache_seconds: int = Field(0, ge=0)
    #: How long a call to the provider may take before it counts as unavailable.
    timeout_seconds: float = Field(5.0, gt=0)

    @field_validator("plugin")
    @classmethod
    def _plugin_is_named(cls, value):
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("plugin must name a class or an entry point")
            return value.strip()
        from keepup.auth.identity.contract import IdentityProvider
        if isinstance(value, IdentityProvider):
            return value
        raise ValueError("plugin must be 'module:Class', an entry point name, "
                         "or an IdentityProvider instance")

    def mapped_roles(self, external_roles) -> List[str]:
        """The roles here that these roles of the other system add up to.

        In the order the mapping is written, each once. Nothing mapped means
        the default role: nobody here holds no role (keepup/auth/user_roles.py).
        """
        held = set(external_roles or ())
        roles: List[str] = []
        for external, internal in self.role_mapping.items():
            if external not in held:
                continue
            for role in ([internal] if isinstance(internal, str) else internal):
                if role not in roles:
                    roles.append(role)
        return roles or [self.default_role]

    def modes(self) -> List[str]:
        """The modes switched on, as the start-up line names them."""
        modes = []
        if self.accept_tokens:
            modes.append("tokens")
        if self.password_sign_in:
            modes.append("password sign-in")
        if self.authorization == "provider":
            modes.append("authorization")
        return modes


def substitute_environment(value: Any, environ: Optional[Mapping[str, str]] = None,
                           where: str = "settings") -> Any:
    """``value`` with every ``${NAME}`` replaced from the environment.

    Walks dictionaries and lists; only strings are substituted. A reference to
    a variable that is not set raises -- naming the variable and where it was
    referenced, never a value.
    """
    environ = os.environ if environ is None else environ
    if isinstance(value, str):
        def replace(match):
            name = match.group(1)
            if name not in environ:
                raise IdentityProviderMisconfigured(
                    f"{SECTION}.{where} refers to the environment variable {name}, "
                    "which is not set")
            return environ[name]
        return _REFERENCE.sub(replace, value)
    if isinstance(value, Mapping):
        return {key: substitute_environment(item, environ, f"{where}.{key}")
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [substitute_environment(item, environ, f"{where}[{index}]")
                for index, item in enumerate(value)]
    return value


def auth_config_path() -> Path:
    """The authentication file, as the environment names it right now."""
    return Path(os.environ.get(AUTH_CONFIG_ENV, DEFAULT_AUTH_CONFIG))


def load_section(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The raw section from the authentication file, or None if there is none.

    A missing file means no section. A file that exists and cannot be read
    stops the start: whether it held a provider cannot be known, and guessing
    "no" is guessing that the door should be the other one.
    """
    path = Path(path) if path is not None else auth_config_path()
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise IdentityProviderMisconfigured(f"cannot read {path}: {error}")
    if not isinstance(data, Mapping):
        raise IdentityProviderMisconfigured(f"{path} is not a mapping")
    section = data.get(SECTION)
    if section is None:
        return None
    if not isinstance(section, Mapping):
        raise IdentityProviderMisconfigured(f"{SECTION} in {path} is not a mapping")
    return dict(section)


def parse(section: Mapping[str, Any], environ: Optional[Mapping[str, str]] = None
          ) -> IdentityProviderConfig:
    """The section as a configuration, or IdentityProviderMisconfigured."""
    data = dict(section)
    data["settings"] = substitute_environment(data.get("settings") or {}, environ)
    try:
        return IdentityProviderConfig(**data)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or SECTION}: {item['msg']}"
            for item in error.errors())
        raise IdentityProviderMisconfigured(f"{SECTION}: {problems}")


def resolve(from_code: Optional[IdentityProviderConfig],
            path: Optional[Path] = None) -> Optional[IdentityProviderConfig]:
    """The deployment's provider configuration, from the file or from code.

    Both at once stop the start: which one wins is exactly the kind of thing
    nobody checks until it matters.
    """
    section = load_section(path)
    if section is not None and from_code is not None:
        raise IdentityProviderMisconfigured(
            f"the identity provider is configured twice: in {path or auth_config_path()} "
            "and in KeepupSettings.identity_provider. Keep one.")
    if section is not None:
        return parse(section)
    if from_code is not None and not isinstance(from_code, IdentityProviderConfig):
        raise IdentityProviderMisconfigured(
            "KeepupSettings.identity_provider must be an IdentityProviderConfig")
    return from_code
