"""What a deployment offers: the built-in catalogue, the application's file, a profile.

The catalogue is data, and it is read before any plugin is loaded, which is the
point: what runs is decided from files and the environment in the first phase,
and the administrator's own decision -- which lives in a database a plugin may
provide -- is read only afterwards (``doc/plugin_constructor.md`` section 4.8).

Three layers become one catalogue here: the file the framework ships
(``builtin.json``: the framework's own plugins and their defaults), the file the
application names (``KeepupSettings.plugins_config_path``: its plugins, and what
it thinks of the framework's), and a profile -- a named patch that says what
this deployment is. The application's entry wins field by field, so
``{"id": "metrics", "enabled": false}`` says "not here" without restating a
priority or a requirement.
"""

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping

from keepup.kernel.descriptor import KIND_OPTIONAL, KIND_REQUIRED

logger = logging.getLogger(__name__)

__all__ = [
    "BUILTIN_FILENAME",
    "CatalogueError",
    "apply_profile",
    "declarations",
    "declared_ids",
    "kind_of",
    "merge",
    "read",
]

#: The file the framework ships, beside this module's parent package.
BUILTIN_FILENAME = "builtin.json"

#: Where a profile's patches live in a catalogue.
PROFILES_FIELD = "profiles"


class CatalogueError(Exception):
    """A catalogue contradicts itself, or names something that is not there."""


def read(path: Any) -> Dict[str, Any]:
    """Read one catalogue file.

    Args:
        path: a path, or None -- an application may declare nothing of its own.

    Returns:
        The parsed catalogue, or an empty one.

    Raises:
        CatalogueError: when the file exists and cannot be read or parsed.
    """
    if path is None:
        return {}
    if isinstance(path, Mapping):
        return copy.deepcopy(dict(path))
    location = Path(path)
    if not location.exists():
        logger.info("Catalogue file %s is not there; the built-in one is used", location)
        return {}
    try:
        with open(location, "r", encoding="utf-8") as handle:
            parsed = json.load(handle)
    except (OSError, ValueError) as error:
        raise CatalogueError(f"{location}: {error}") from error
    if not isinstance(parsed, dict):
        raise CatalogueError(f"{location}: a catalogue is an object")
    return parsed


def builtin() -> Dict[str, Any]:
    """The framework's own declaration, read from beside this package."""
    return read(Path(__file__).resolve().parent.parent / "plugins" / BUILTIN_FILENAME)


def merge(*catalogues: Mapping[str, Any]) -> Dict[str, Any]:
    """Merge catalogues by plugin id, later winning field by field.

    Args:
        *catalogues: the built-in one first, the application's last.

    Returns:
        One catalogue: plugins merged by id, profiles merged by name, every
        other key taken from the last catalogue that declares it.
    """
    merged: Dict[str, Any] = {}
    plugins: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    profiles: Dict[str, Any] = {}

    for catalogue in catalogues:
        if not catalogue:
            continue
        for key, value in catalogue.items():
            if key == "plugins" or key == PROFILES_FIELD:
                continue
            merged[key] = copy.deepcopy(value)
        for profile_name, patch in (catalogue.get(PROFILES_FIELD) or {}).items():
            profiles[profile_name] = copy.deepcopy(patch)
        for entry in catalogue.get("plugins") or []:
            if not isinstance(entry, Mapping) or not entry.get("id"):
                raise CatalogueError(f"a plugin declaration needs an id: {entry!r}")
            plugin_id = str(entry["id"])
            if plugin_id not in plugins:
                plugins[plugin_id] = {}
                order.append(plugin_id)
            for key, value in entry.items():
                if key == "config" and isinstance(value, Mapping):
                    existing = plugins[plugin_id].get("config")
                    merged_config = dict(existing) if isinstance(existing, Mapping) else {}
                    merged_config.update(value)
                    plugins[plugin_id]["config"] = merged_config
                else:
                    plugins[plugin_id][key] = copy.deepcopy(value)

    if profiles:
        merged[PROFILES_FIELD] = profiles
    merged["plugins"] = [plugins[plugin_id] for plugin_id in order]
    return merged


def declarations(catalogue: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The plugin declarations of a catalogue, in file order."""
    return [dict(entry) for entry in (catalogue.get("plugins") or []) if entry.get("id")]


def declared_ids(catalogue: Mapping[str, Any]) -> List[str]:
    """The ids a catalogue declares, in file order."""
    return [str(entry["id"]) for entry in declarations(catalogue)]


def kind_of(entry: Mapping[str, Any]) -> str:
    """The kind a declaration claims, defaulting to optional.

    A declaration is data and may be written before the plugin is installed, so
    the kind here is what the catalogue says, not what the code will say. The
    code wins when both exist; this keeps a profile's ``only`` honest for the
    plugins nobody has installed yet.
    """
    return str(entry.get("kind") or KIND_OPTIONAL)


def apply_profile(catalogue: Mapping[str, Any], name: str) -> Dict[str, Any]:
    """Apply a named profile to a catalogue.

    A profile is a patch: ``{"plugins": [{"id": ..., ...}]}`` overrides fields,
    ``{"enable": [...]}`` and ``{"disable": [...]}`` switch plugins, and
    ``{"only": [...]}`` switches every optional and transport plugin that is not
    named. Required plugins are never switched by a profile -- what a deployment
    cannot work without is not a deployment's preference.

    Args:
        catalogue: the merged catalogue.
        name: the profile's name.

    Returns:
        A new catalogue with the profile applied.

    Raises:
        CatalogueError: when the profile is not declared, or names a plugin that
            is not in the catalogue.
    """
    profiles = catalogue.get(PROFILES_FIELD) or {}
    if name not in profiles:
        known = ", ".join(sorted(profiles)) or "none"
        raise CatalogueError(f"unknown profile {name!r}; declared profiles: {known}")
    patch = profiles[name] or {}
    if not isinstance(patch, Mapping):
        raise CatalogueError(f"profile {name!r} is an object")

    result = copy.deepcopy(dict(catalogue))
    by_id = {str(entry["id"]): entry for entry in result.get("plugins") or []}
    touched: List[str] = []

    def known(plugin_id: Any) -> str:
        plugin_id = str(plugin_id)
        if plugin_id not in by_id:
            raise CatalogueError(f"profile {name!r} names the undeclared plugin {plugin_id!r}")
        touched.append(plugin_id)
        return plugin_id

    for plugin_id in patch.get("enable") or []:
        by_id[known(plugin_id)]["enabled"] = True
    for plugin_id in patch.get("disable") or []:
        entry = by_id[known(plugin_id)]
        if kind_of(entry) == KIND_REQUIRED:
            raise CatalogueError(
                f"profile {name!r} disables the required plugin {plugin_id!r}"
            )
        entry["enabled"] = False
    for entry_patch in patch.get("plugins") or []:
        if not isinstance(entry_patch, Mapping) or not entry_patch.get("id"):
            raise CatalogueError(f"profile {name!r}: a patch needs an id")
        entry = by_id[known(entry_patch["id"])]
        if entry_patch.get("enabled") is False and kind_of(entry) == KIND_REQUIRED:
            raise CatalogueError(
                f"profile {name!r} disables the required plugin {entry_patch['id']!r}"
            )
        for key, value in entry_patch.items():
            entry[key] = copy.deepcopy(value)
    only = patch.get("only")
    if only is not None:
        keep = {known(plugin_id) for plugin_id in only}
        for plugin_id, entry in by_id.items():
            if kind_of(entry) == KIND_REQUIRED:
                continue
            entry["enabled"] = plugin_id in keep
    for plugin_id in patch:
        if plugin_id not in ("enable", "disable", "plugins", "only", "_comment"):
            raise CatalogueError(f"profile {name!r}: unknown key {plugin_id!r}")
    return result


def compose(
    builtin_catalogue: Mapping[str, Any],
    application_catalogue: Mapping[str, Any],
    profile: str = None,
) -> Dict[str, Any]:
    """One catalogue from the framework's, the application's and a profile.

    Args:
        builtin_catalogue: what the framework ships.
        application_catalogue: what the application's file says.
        profile: the profile's name, or None.

    Returns:
        The merged catalogue with the profile applied.

    Raises:
        CatalogueError: when a file or the profile contradicts itself.
    """
    merged = merge(builtin_catalogue, application_catalogue)
    if profile:
        merged = apply_profile(merged, profile)
    return merged
