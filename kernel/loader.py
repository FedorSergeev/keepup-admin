"""Finding plugin classes: installed distributions, and the application's own files.

Two sources, one shape. A capability that ships as a distribution declares itself
through an entry point of the group ``keepup.plugins``; a plugin an application
writes lives in a directory as one file per plugin, which is how applications
have always written them. The application's directory is consulted first, so an
application's plugin shadows a built-in one of the same identifier and says so
in the log.

Discovery reads a class, never an instance: a descriptor is a class attribute,
and a plugin whose requirements cannot be met must be reportable without being
constructed (``kernel/descriptor.py``).
"""

import importlib.metadata
import importlib.util
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional

from keepup.kernel.descriptor import PluginDescriptor
from keepup.plugins.base import (
    OUTCOME_FAILED,
    OUTCOME_NOT_FOUND,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ENTRY_POINT_GROUP",
    "Candidate",
    "PluginLoader",
    "plugin_class_name",
]

#: The group a distribution declares its plugin in.
ENTRY_POINT_GROUP = "keepup.plugins"


def plugin_class_name(plugin_id: str) -> str:
    """The class name a plugin id implies.

    The rule is 0.3.0's, kept because a wrong name does not raise in it -- the
    plugin is logged as not found and never loads -- so changing the rule would
    be a silent change for every application that has one.

    Args:
        plugin_id: the plugin identifier.

    Returns:
        The class name, e.g. ``Integration_logsPlugin`` for ``integration_logs``.
    """
    return f"{plugin_id.capitalize()}Plugin"


@dataclass
class Candidate:
    """One plugin class the loader found, and where it came from.

    Attributes:
        plugin_id: the identifier it was found under.
        plugin_class: the class itself.
        source: ``"directory"`` or ``"entry-point"``.
        descriptor: what the class declares, or a derived descriptor.
    """

    plugin_id: str
    plugin_class: Any
    source: str
    descriptor: PluginDescriptor

    @property
    def name(self) -> str:
        """What the panel should call the plugin."""
        return self.descriptor.name or self.plugin_id


class PluginLoader:
    """Where plugin classes come from, and how they are constructed."""

    def __init__(
        self,
        plugins_dir: Optional[str] = None,
        entry_points: Optional[Iterable[Any]] = None,
        group: str = ENTRY_POINT_GROUP,
    ) -> None:
        self.plugins_dir = plugins_dir
        self.group = group
        self._entry_points = list(entry_points) if entry_points is not None else None

    # --- discovery ----------------------------------------------------------

    def entry_points(self) -> List[Any]:
        """The declared entry points of the group, or the ones given to us."""
        if self._entry_points is not None:
            return list(self._entry_points)
        try:
            return list(importlib.metadata.entry_points(group=self.group))
        except Exception as error:  # noqa: BLE001 - a broken distribution must not stop a start
            logger.warning("Entry points of %s are unreadable: %s", self.group, error)
            return []

    def candidates(self) -> Dict[str, Candidate]:
        """Every plugin class available to this deployment, by identifier.

        Returns:
            The candidates, entry points first and the application's directory
            second, so that a file of the application shadows a distribution of
            the same identifier.
        """
        found: Dict[str, Candidate] = {}
        for entry_point in self.entry_points():
            plugin_id = str(getattr(entry_point, "name", "") or "")
            if not plugin_id:
                continue
            try:
                plugin_class = entry_point.load()
            except Exception as error:  # noqa: BLE001
                logger.warning("Entry point %s does not load: %s", plugin_id, error)
                continue
            found[plugin_id] = self._candidate(plugin_id, plugin_class, "entry-point")
        for plugin_id, plugin_class in self._directory_classes().items():
            if plugin_id in found:
                logger.info(
                    "Plugin %s comes from the application's directory, shadowing a distribution",
                    plugin_id,
                )
            found[plugin_id] = self._candidate(plugin_id, plugin_class, "directory")
        return found

    def _candidate(self, plugin_id: str, plugin_class: Any, source: str) -> Candidate:
        """Wrap a class with the descriptor it declares, or a derived one."""
        descriptor = PluginDescriptor.of(plugin_class, plugin_id)
        return Candidate(plugin_id, plugin_class, source, descriptor)

    def _directory_classes(self) -> Dict[str, Any]:
        """The plugin classes of the application's directory, by file name.

        A file that cannot be imported is logged and passed over: one broken
        plugin of an application must not take the plugin runtime down with it.
        """
        if not self.plugins_dir:
            return {}
        directory = os.path.abspath(self.plugins_dir)
        if not os.path.isdir(directory):
            logger.info("Plugin directory %s is not there", directory)
            return {}
        classes: Dict[str, Any] = {}
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".py") or name.startswith("_"):
                continue
            plugin_id = name[:-3]
            path = os.path.join(directory, name)
            try:
                spec = importlib.util.spec_from_file_location(plugin_id, path)
                if spec is None or spec.loader is None:
                    raise ImportError(f"{path} is not importable")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception as error:  # noqa: BLE001
                logger.error("Plugin %s does not import: %s", plugin_id, error)
                continue
            plugin_class = getattr(module, plugin_class_name(plugin_id), None)
            if plugin_class is None:
                logger.warning(
                    "Plugin file %s declares no %s", path, plugin_class_name(plugin_id)
                )
                continue
            classes[plugin_id] = plugin_class
        return classes

    def outcome_for_missing(self, plugin_id: str) -> tuple:
        """Why a declared plugin could not be found, as an outcome and a reason."""
        return (OUTCOME_NOT_FOUND, f"no class for {plugin_id}")

    # --- construction -------------------------------------------------------

    def instantiate(
        self,
        candidate: Candidate,
        config: Optional[Mapping[str, Any]] = None,
        priority: int = 0,
        manager: Any = None,
    ) -> Any:
        """Create one plugin and hand it what the manager always has.

        Args:
            candidate: the class to construct.
            config: the plugin's own configuration.
            priority: what the catalogue declares; the loader passes it on.
            manager: the object plugins reach for each other -- the runtime.

        Returns:
            The plugin instance.

        Raises:
            Exception: whatever the constructor raises; the caller records it as
                an outcome rather than letting it stop the start.
        """
        instance = candidate.plugin_class(dict(config or {}))
        if manager is not None and hasattr(instance, "set_plugin_manager"):
            instance.set_plugin_manager(manager)
        if hasattr(instance, "set_priority"):
            instance.set_priority(priority)
        else:
            instance.priority = priority
        return instance

    @staticmethod
    def failure(plugin_id: str, error: BaseException) -> tuple:
        """The outcome and reason of a plugin that could not be constructed."""
        return (OUTCOME_FAILED, f"load error: {type(error).__name__}: {error}")
