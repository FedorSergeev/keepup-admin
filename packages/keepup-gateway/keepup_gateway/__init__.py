"""One way in for the application's services.

This distribution provides `gateway` and declares one plugin, `gateway`, in the
`keepup.plugins` group. It depends on `keepup-admin` for the constructor and for
the kernel's question "who is calling" (`keepup.kernel.security`), and on nothing
of another capability: a deployment with no sign-in can still route.

The pure parts -- the table, the balancer, the headers and the identity a
service checks -- import nothing beyond the standard library, so a service
behind the gateway can verify what it was sent without installing FastAPI.
"""

__version__ = "0.7.0"

from keepup_gateway.identity import IdentityError, sign_identity, verify_identity  # noqa: E402

_PLUGIN_NAMES = {"GatewayPlugin", "SERVICE_GATEWAY"}

__all__ = ["GatewayPlugin", "SERVICE_GATEWAY", "IdentityError", "sign_identity",
           "verify_identity"]


def __getattr__(name):
    if name in _PLUGIN_NAMES:
        from keepup_gateway import plugin

        return getattr(plugin, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
