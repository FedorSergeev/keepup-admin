"""A pluggable identity provider: somebody else's system behind keepup.

For an application installed inside a larger system whose own subsystem knows
who everybody is and what they may do. A plugin implements
:class:`IdentityProvider` for that system; the deployment names it in the
``identity_provider`` section of its authentication file; the framework then
takes that system's tokens, lets its people into the panel with its passwords,
and asks it about rights. See doc/external_identity_provider.md.
"""

from keepup.auth.identity.access import require_permission
from keepup.auth.identity.config import IdentityProviderConfig, IdentityProviderMisconfigured
from keepup.auth.identity.contract import (
    AccessRequest,
    ExternalIdentity,
    IdentityProvider,
    IdentityRejected,
    ProviderUnavailable,
)
from keepup.auth.identity.loader import ENTRY_POINT_GROUP

#: What an application or a provider plugin may import from this package.
__all__ = [
    "AccessRequest",
    "ENTRY_POINT_GROUP",
    "ExternalIdentity",
    "IdentityProvider",
    "IdentityProviderConfig",
    "IdentityProviderMisconfigured",
    "IdentityRejected",
    "ProviderUnavailable",
    "require_permission",
]
