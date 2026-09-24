"""Authentication: providers, dependencies, the panel session and its routes.

The provider is chosen by ``config/auth.yaml`` through
:mod:`keepup.auth.factory`. What the application adds -- documents a person
must accept, the rule a password is held to, where a sign-in is written down --
arrives through settings, not from here.
"""
