"""The two roles the framework itself distinguishes.

Anything finer -- who may see which section, which permissions a group maps to
-- belongs to the application and to ``keepup.auth.permissions``. The framework
only needs to tell an administrator from an ordinary user, because that is the
distinction its own endpoints are guarded by.
"""

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "ROLE_ADMIN",
    "ROLE_CLIENT",
]

ROLE_ADMIN = "ADMIN"
ROLE_CLIENT = "CLIENT"
