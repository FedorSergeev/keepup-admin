"""Where a name that moved went, said once, for one release.

Task keepup-114. 0.4.0 moves code out of the base package and into the
distributions beside it (`doc/distributions.md`): the table language leaves
`keepup.tables`, the pool leaves `keepup.db`, the sign-in leaves `keepup.auth`.
An application that imports one of those names must keep working for one release
-- and must be told where the name went, because a silent shim is how an
application ends up pinned to a release it cannot leave.

Nothing is redirected here while the module still exists: this is the map, the
warning, and the proxy that keepup-124 installs for each name it moves.
"""

import importlib
import importlib.util
import sys
import types
import warnings
from typing import Dict, Optional

__all__ = [
    "MOVED",
    "MOVED_NAMES",
    "destination_of",
    "install",
    "installed",
    "shim",
    "warn_moved",
]

#: The old name, and where it went. The target is a module of a distribution
#: (`packages/`), named as a person would import it.
MOVED: Dict[str, str] = {
    "keepup.db": "keepup_db",
    "keepup.tables": "keepup_db.tables",
    "keepup.schema": "keepup_db.schema",
    "keepup.audit": "keepup_audit",
    "keepup.events": "keepup_audit.events",
    "keepup.admin_trail": "keepup_audit.trail",
    "keepup.metrics": "keepup_metrics",
    "keepup.metrics_api": "keepup_metrics.api",
    "keepup.integrations": "keepup_integration_log",
    "keepup.themes": "keepup_ui.themes",
    "keepup.auth": "keepup_auth",
    "keepup.auth.user_roles": "keepup_users.roles",
}

#: The same names, for a caller that wants the short form.
MOVED_NAMES = tuple(MOVED)

#: What has already been said, so a warning is not repeated on every import.
_said: set = set()

#: What this process installed, by old name.
_installed: Dict[str, types.ModuleType] = {}


def _submodules(package) -> list:
    """The modules inside a distribution, so an old name finds what it exported."""
    import pkgutil

    prefix = package.__name__ + "."
    found = [module for name, module in list(sys.modules.items())
             if name.startswith(prefix) and isinstance(module, types.ModuleType)]
    for module in pkgutil.iter_modules(getattr(package, "__path__", [])):
        name = f"{package.__name__}.{module.name}"
        if name in sys.modules:
            continue
        try:
            found.append(importlib.import_module(name))
        except Exception:  # noqa: BLE001 - a module that will not import is not this one
            continue
    return found


def destination_of(name: str) -> Optional[str]:
    """Where a moved name went, or None when it did not move.

    Args:
        name: the old import path.

    Returns:
        The new import path, or None.
    """
    if name in MOVED:
        return MOVED[name]
    for old, new in MOVED.items():
        if name.startswith(old + "."):
            return new + name[len(old):]
    return None


def warn_moved(name: str, destination: str) -> None:
    """Say once, per process, where a name went.

    Args:
        name: the old import path.
        destination: the new one.
    """
    if name in _said:
        return
    _said.add(name)
    warnings.warn(
        f"{name} moved to {destination} in keepup 0.4.0 and goes away in 0.5.0; "
        f"import it from there",
        DeprecationWarning,
        stacklevel=3,
    )


class _Moved(types.ModuleType):
    """A module that says where it went, and then answers like the new one."""

    def __init__(self, name: str, destination: str):
        super().__init__(name)
        self.__dict__["_destination"] = destination
        # The stand-in keeps its own docstring -- it has something to say the
        # moved module did not -- but it must answer for the declaration: a check
        # that reads `module.__all__` to learn the interface found nothing here
        # and reported every name the application takes as undeclared
        # (keepup-125).

    def _target(self):
        """The module this name now lives in.

        Raises:
            ModuleNotFoundError: naming the distribution to install, because
                "No module named keepup_db" is a riddle and "install keepup-db"
                is an answer. The base package deliberately does not depend on
                the distributions it moved code into: that dependency would point
                both ways (a distribution needs the kernel it is loaded by).
        """
        destination = self.__dict__["_destination"]
        try:
            return importlib.import_module(destination)
        except ModuleNotFoundError as missing:
            distribution = destination.split(".")[0].replace("_", "-")
            raise ModuleNotFoundError(
                f"{self.__name__} moved to {destination} in keepup 0.4.0: install "
                f"{distribution} to keep using it",
                name=destination,
            ) from missing

    def __getattr__(self, item):
        # A stand-in has to answer for a module, not only for a package: the
        # module that moved exported names a distribution's __init__ does not
        # re-export -- its private helpers, its `__file__` -- and code that used
        # them has to keep working for the release.
        if item in ("__file__", "__path__", "__spec__", "__loader__", "__all__"):
            return getattr(self._target(), item)
        if item.startswith("__") and item.endswith("__"):
            raise AttributeError(item)
        warn_moved(self.__name__, self.__dict__["_destination"])
        target = self._target()
        if hasattr(target, item):
            return getattr(target, item)
        for module in _submodules(target):
            if hasattr(module, item):
                return getattr(module, item)
        raise AttributeError(
            f"{self.__name__} moved to {self.__dict__['_destination']}, which has "
            f"no {item}"
        )


def shim(name: str, destination: Optional[str] = None) -> types.ModuleType:
    """A module that stands in for a moved one.

    Args:
        name: the old import path.
        destination: where it went; looked up when not given.

    Returns:
        The stand-in, which is *not* installed: a caller decides that.
    """
    where = destination or destination_of(name)
    if not where:
        raise KeyError(f"{name} did not move, so it needs no stand-in")
    return _Moved(name, where)


def install() -> Dict[str, types.ModuleType]:
    """Stand in for every moved name whose module is no longer here.

    Returns:
        What was installed, by old name. A name whose module still exists is
        left alone: the shim is for the release in which it does not.
    """
    for name, destination in MOVED.items():
        if name in sys.modules or _module_exists(name):
            continue
        stand_in = shim(name, destination)
        sys.modules[name] = stand_in
        _installed[name] = stand_in
    return dict(_installed)


def installed() -> Dict[str, types.ModuleType]:
    """What this process stood in for, by old name."""
    return dict(_installed)


def _module_exists(name: str) -> bool:
    """Whether the module is still part of the package.

    Asked of the finder rather than by importing it: importing `keepup` must not
    load the metrics API, the event log or the pool, which is exactly what the
    lazy-import checks of those capabilities are about (keepup-112).
    """
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False
