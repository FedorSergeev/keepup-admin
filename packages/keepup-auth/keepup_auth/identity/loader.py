"""Finding the provider class a configuration names, and building it.

Two spellings. ``package.module:Class`` is imported directly: the deployment
says exactly what to load. A bare name is looked up among the entry points of
the ``keepup.identity_providers`` group, so a provider package installed into
the image announces itself and the configmap says ``corp-as`` rather than a
module path.

A provider is not a keepup plugin from ``modules.json``: it is needed before
any plugin initialises and before the first request, and it belongs to the
deployment, not to the catalogue of sections.
"""

from __future__ import annotations

import importlib
import logging
from importlib.metadata import entry_points
from typing import Callable, Iterable, Optional

from keepup_auth.identity.config import IdentityProviderConfig, IdentityProviderMisconfigured
from keepup_auth.identity.contract import IdentityProvider

logger = logging.getLogger(__name__)

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = ["ENTRY_POINT_GROUP", "build_provider", "find_class"]

ENTRY_POINT_GROUP = "keepup.identity_providers"


def _entry_points() -> Iterable:
    return entry_points(group=ENTRY_POINT_GROUP)


def find_class(reference: str, discover: Optional[Callable[[], Iterable]] = None):
    """The class ``reference`` names, or IdentityProviderMisconfigured."""
    if ":" in reference:
        module_name, _, attribute = reference.partition(":")
        try:
            module = importlib.import_module(module_name)
        except Exception as error:
            raise IdentityProviderMisconfigured(
                f"identity provider {reference!r}: cannot import {module_name}: {error}")
        found = getattr(module, attribute, None)
        if found is None:
            raise IdentityProviderMisconfigured(
                f"identity provider {reference!r}: {module_name} has no {attribute}")
    else:
        candidates = [point for point in (discover or _entry_points)()
                      if point.name == reference]
        if not candidates:
            raise IdentityProviderMisconfigured(
                f"identity provider {reference!r}: no such entry point in the "
                f"{ENTRY_POINT_GROUP} group; install the package that provides it, "
                "or name it as 'module:Class'")
        try:
            found = candidates[0].load()
        except Exception as error:
            raise IdentityProviderMisconfigured(
                f"identity provider {reference!r}: the entry point failed to load: {error}")

    if not (isinstance(found, type) and issubclass(found, IdentityProvider)):
        raise IdentityProviderMisconfigured(
            f"identity provider {reference!r} is not a subclass of "
            "keepup.auth.identity.IdentityProvider")
    return found


def check_capabilities(provider: IdentityProvider, config: IdentityProviderConfig) -> None:
    """Refuse a configuration that asks the provider for what it cannot do."""
    kind = type(provider)
    can = kind.capabilities()
    if not can:
        raise IdentityProviderMisconfigured(
            f"identity provider {config.name}: {kind.__name__} implements none of "
            "verify_token, verify_password, decide")
    needs = {
        "verify_token": config.accept_tokens,
        "verify_password": config.password_sign_in,
        "decide": config.authorization == "provider",
    }
    switch = {
        "verify_token": "accept_tokens: false",
        "verify_password": "password_sign_in: false",
        "decide": "authorization: local",
    }
    for capability, needed in needs.items():
        if needed and capability not in can:
            raise IdentityProviderMisconfigured(
                f"identity provider {config.name}: the configuration needs "
                f"{capability}, which {kind.__name__} does not implement; implement "
                f"it or set {switch[capability]}")
    if not any(needs.values()):
        raise IdentityProviderMisconfigured(
            f"identity provider {config.name} is configured and used for nothing: "
            "switch on accept_tokens, password_sign_in or authorization: provider")


def build_provider(config: IdentityProviderConfig,
                   discover: Optional[Callable[[], Iterable]] = None) -> IdentityProvider:
    """The provider this configuration describes, checked against its modes."""
    if isinstance(config.plugin, IdentityProvider):
        provider = config.plugin
    else:
        kind = find_class(config.plugin, discover)
        try:
            provider = kind(config.settings)
        except IdentityProviderMisconfigured:
            raise
        except Exception as error:
            # The type and not the text: the text of a constructor's exception
            # may carry the very settings it was handed, secrets among them.
            raise IdentityProviderMisconfigured(
                f"identity provider {config.name}: {kind.__name__} failed to "
                f"start ({type(error).__name__})")
    check_capabilities(provider, config)
    return provider
