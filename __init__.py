"""KeepUP — a framework over FastAPI for applications built like this one.

An application supplies a :class:`~keepup.settings.KeepupSettings` and gets an
application back from :func:`~keepup.factory.create_app`. Everything the
framework cannot know -- the product's name, the collector its logs go to,
which fields of a request are secret, what a password must look like, which
tables belong to the product -- travels in those settings, never as a default
in here. See ``doc/keepup.md``.

Imported lazily: ``keepup.factory`` pulls in the database layer and the
authentication provider, and importing the package to reach one constant
should not do that.
"""

#: The framework's own version, which is the version of the distribution it
#: was installed from -- not the version of the application it serves. The
#: application's version is its own metadata and reaches the framework through
#: settings; reading one for the other is how a panel ends up reporting the
#: framework's number as the product's (keepup-5).
try:
    from importlib.metadata import version as _distribution_version

    __version__ = _distribution_version("keepup-admin")
except Exception:
    # Running from a source tree that was never installed -- the repository
    # itself, until the applications switch to installing by version.
    __version__ = "0.0.0+source"

__all__ = ["KeepupSettings", "StaticMount", "create_app", "__version__"]


def __getattr__(name):
    if name in ("KeepupSettings", "StaticMount"):
        from keepup import settings

        return getattr(settings, name)
    if name == "create_app":
        from keepup.factory import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
