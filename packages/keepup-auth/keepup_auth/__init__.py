"""Sign-in, sessions, login throttling, roles, OIDC and somebody else's identity system.

This distribution provides `auth, permissions` and declares one plugin,
`auth`, in the `keepup.plugins` group. It depends on `keepup-admin`
for the constructor -- the descriptor, the catalogue, the service registry and
the lifecycle -- and on nothing of another capability.

The code moves here in keepup-124: until then the plugin lives in the framework's
own `builtin/auth.py` and this package is the home it is moving to.
"""

__version__ = "0.4.0"


# What the sign-in package exported, now that it is this distribution.
"""Authentication: providers, dependencies, the panel session and its routes.

The provider is chosen by ``config/auth.yaml`` through
:mod:`keepup.auth.factory`. What the application adds -- documents a person
must accept, the rule a password is held to, where a sign-in is written down --
arrives through settings, not from here.
"""
