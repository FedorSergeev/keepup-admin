"""The plugin contract and the manager that loads plugins.

The manager knows nothing about which application it serves: the directory to
load from is supplied by that application. Resolving it from this module's own
location would point inside the framework package, where no plugin lives.
"""

import importlib.util
import logging
import os
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

from keepup.plugins import enablement

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "BasePlugin",
    "MODULES_CONFIG_FILE",
    "OUTCOME_DISABLED",
    "OUTCOME_FAILED",
    "OUTCOME_INITIALIZED",
    "OUTCOME_NOT_FOUND",
    "PluginManager",
]

logger = logging.getLogger(__name__)

MODULES_CONFIG_FILE = "modules.json"

#: Outcome of a declared plugin, as reported to the admin plugin list.
OUTCOME_INITIALIZED = "initialized"
OUTCOME_DISABLED = "disabled"
OUTCOME_NOT_FOUND = "not_found"
OUTCOME_FAILED = "failed"


class BasePlugin(ABC):
    """Base class for every plugin."""

    def __init__(self, plugin_id: str, name: str, config: Dict = None):
        self.plugin_id = plugin_id
        self.name = name
        self.config = config or {}
        self.plugin_manager = None
        self.initialized = False
        self.priority = 0

    def set_plugin_manager(self, plugin_manager):
        """Store the reference to the plugin manager."""
        self.plugin_manager = plugin_manager

    def set_priority(self, priority: int):
        """Set the plugin priority."""
        self.priority = priority

    @abstractmethod
    async def initialize(self) -> bool:
        """Initialise the plugin."""
        pass

    async def post_construct(self):
        """Called once the application is fully initialised and all endpoints registered.

        Use it for self-checks that need a running server.
        """
        pass

    def get_websocket_routes(self) -> List[Dict]:
        """Return the plugin's WebSocket routes.

        Each route is a dict with:
        - path: str -- the WebSocket endpoint path
        - handler: callable -- async handler taking the WebSocket and its parameters
        """
        return []

    @abstractmethod
    def get_api_routes(self) -> List[Dict]:
        """Return the plugin's API routes."""
        pass

    @abstractmethod
    def get_handlers(self) -> Dict[str, Any]:
        """Return the plugin's named handlers."""
        pass

    async def cleanup(self):
        """Release the plugin's resources."""
        pass


class PluginManager:
    """Loads and manages plugins, honouring their priorities."""

    def __init__(self, plugins_dir: str, config_file: str = MODULES_CONFIG_FILE):
        self.plugins_dir = plugins_dir
        self.config_file = config_file
        #: Loaded, in the order they were loaded.
        self.plugins: Dict[str, BasePlugin] = {}
        #: The subset that initialised; only these answer get_plugin().
        self.loaded_plugins: Dict[str, BasePlugin] = {}
        #: Outcome per declared plugin id: (outcome, reason or None).
        #: A plugin that loaded but failed to initialise looks exactly like a
        #: disabled one from outside -- its routes are simply absent -- so the
        #: difference is kept here rather than only in the log.
        self.outcomes: Dict[str, tuple] = {}
        #: What the deployment decided about each declared plugin at start-up,
        #: for the administrative list. It belongs to this manager and not to
        #: the module that computes it: a process may hold more than one
        #: application, and a module-level snapshot would be the last one's.
        self.resolution = enablement.Resolution()

    def record_outcome(self, plugin_id: str, outcome: str, reason: str = None):
        """Remember how a declared plugin ended up."""
        self.outcomes[plugin_id] = (outcome, reason)

    def get_outcome(self, plugin_id: str) -> tuple:
        """Return (outcome, reason) for a declared plugin, or (None, None)."""
        return self.outcomes.get(plugin_id, (None, None))

    def load_plugin(self, plugin_id: str, plugin_config: Dict, priority: int = 0) -> bool:
        """Load a single plugin by its id and give it its priority.

        The priority is what initialize_plugins() orders by; it comes from the
        plugin's description in modules.json and was, until it arrived here,
        declared and never applied.
        """
        try:
            plugin_file = os.path.join(self.plugins_dir, f"{plugin_id}.py")

            if not os.path.exists(plugin_file):
                logger.warning(f"Plugin file not found: {plugin_file}")
                self.record_outcome(plugin_id, OUTCOME_NOT_FOUND, f"file not found: {plugin_file}")
                return False
            spec = importlib.util.spec_from_file_location(plugin_id, plugin_file)
            plugin_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(plugin_module)

            plugin_class_name = f"{plugin_id.capitalize()}Plugin"
            plugin_class = getattr(plugin_module, plugin_class_name, None)
            if not plugin_class:
                logger.warning(f"Plugin class {plugin_class_name} not found for: {plugin_id}")
                self.record_outcome(plugin_id, OUTCOME_NOT_FOUND, f"class {plugin_class_name} not found")
                return False

            plugin_instance = plugin_class(plugin_config)
            plugin_instance.set_plugin_manager(self)
            plugin_instance.set_priority(priority)
            self.plugins[plugin_id] = plugin_instance
            logger.info(f"Plugin loaded: {plugin_id}")
            return True

        except Exception as e:
            logger.error(f"Error loading plugin {plugin_id}: {str(e)}")
            self.record_outcome(plugin_id, OUTCOME_FAILED, f"load error: {e}")
            return False

    async def initialize_plugins(self) -> bool:
        """Initialise every loaded plugin, lowest priority first.

        Stable for equal priorities, so ties keep the load order -- the order
        of description in modules.json. A plugin whose initialize() fails is
        logged and left out of loaded_plugins; the rest still come up.
        """
        ordered = sorted(self.plugins.items(), key=lambda item: item[1].priority)
        logger.info(f"Initializing plugins in order: {[plugin_id for plugin_id, _ in ordered]}")

        success = True
        for plugin_id, plugin in ordered:
            try:
                initialized = await plugin.initialize()
                failure_reason = "initialize() returned false"
            except Exception as e:
                # An exception used to escape into the caller and stop the rest
                # of the plugins from initialising at all.
                initialized = False
                failure_reason = f"{type(e).__name__}: {e}"
                logger.exception(f"Error initializing plugin {plugin_id}")

            if initialized:
                self.loaded_plugins[plugin_id] = plugin
                self.record_outcome(plugin_id, OUTCOME_INITIALIZED)
                logger.info(f"Plugin initialized ({plugin.priority}): {plugin_id}")
            else:
                logger.error(f"Failed to initialize plugin: {plugin_id}")
                self.record_outcome(plugin_id, OUTCOME_FAILED, failure_reason)
                success = False
        return success

    def get_plugin(self, plugin_id: str) -> Optional[BasePlugin]:
        """Return a plugin instance by id."""
        return self.loaded_plugins.get(plugin_id)

    def get_all_api_routes(self) -> List[Dict]:
        """Return the API routes of every plugin."""
        routes = []
        for plugin in self.loaded_plugins.values():
            routes.extend(plugin.get_api_routes())
        return routes

    def get_all_websocket_routes(self) -> List[Dict]:
        """Collect the WebSocket routes of every plugin that initialised.

        The initialised ones, like get_all_api_routes -- this used to walk the
        loaded ones instead. A plugin whose initialize() failed lost its HTTP
        routes and kept its sockets, so the admin surface reported it failed
        while a socket of its went on listening against an object with no
        tables and no connections behind it (keepup-12).
        """
        all_routes = []
        for plugin in self.loaded_plugins.values():
            if hasattr(plugin, 'get_websocket_routes') and callable(plugin.get_websocket_routes):
                try:
                    routes = plugin.get_websocket_routes()
                    if routes:
                        all_routes.extend(routes)
                except Exception as e:
                    logger.error(f"Error getting WebSocket routes from plugin {plugin.name}: {e}")
        return all_routes

    async def cleanup_all(self):
        """Clean up every plugin."""
        for plugin in self.loaded_plugins.values():
            await plugin.cleanup()
        self.loaded_plugins.clear()

    def get_plugins_by_priority(self) -> List[BasePlugin]:
        """Return the loaded plugins sorted by priority."""
        return sorted(
            self.loaded_plugins.values(),
            key=lambda x: x.priority
        )
