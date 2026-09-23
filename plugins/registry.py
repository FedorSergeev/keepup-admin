"""Assembling the plugins of an application, and the package's way in.

What happens here is the start-up itself: read the declaration, ask
``keepup.plugins.admin`` which of the declared plugins this deployment runs,
load and initialise them in priority order, hand their routes to
``keepup.plugins.routes``, and afterwards run the self-checks that need a live
server.

This module is also **the address the package publishes**. The two modules the
work moved into are internal: an application imports ``initialize_plugins``,
``register_plugin_routes`` and the rest from here, as it always has, and a
rearrangement inside the package is not a breaking change for anybody who
installed it (keepup-21).
"""

import asyncio
import concurrent.futures
import json
import logging
import os

from keepup.plugins import enablement
from keepup.plugins.admin import (
    MODULES_CONFIG_PATH,
    clear_plugin_override,
    get_plugins,
    get_plugins_status,
    read_plugin_overrides,
    register_plugin_admin_routes,
    write_plugin_override,
)
from keepup.plugins.base import (
    OUTCOME_DISABLED,
    OUTCOME_INITIALIZED,
)
from keepup.plugins.routes import (
    accepted_params,
    admit_params,
    raw_request_wrapper,
    register_plugin_routes,
    signed_in_websocket,
)

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
#:
#: The names below live in keepup.plugins.routes and keepup.plugins.admin and
#: are published here: one address outwards, whatever the layout inside.
__all__ = [
    "accepted_params",
    "admit_params",
    "clear_plugin_override",
    "get_plugins",
    "get_plugins_status",
    "initialize_plugins",
    "raw_request_wrapper",
    "read_plugin_overrides",
    "register_plugin_admin_routes",
    "register_plugin_routes",
    "run_post_construct_processors",
    "signed_in_websocket",
    "write_plugin_override",
]

logger = logging.getLogger(__name__)


async def initialize_plugins(app, manager, config_path=MODULES_CONFIG_PATH, environ=None):
    """Load and initialise the plugins the configuration and environment enable.

    Which plugins run is decided by keepup.plugins.enablement -- the plugin's own
    ``enabled`` flag, an administrator's override kept in this deployment's
    database, and PLUGINS_ENABLE / PLUGINS_DISABLE -- never by the roles'
    plugin lists, which only say what a role is shown.
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        resolution = enablement.resolve(
            config, environ if environ is not None else os.environ,
            overrides=read_plugin_overrides())
        # On the manager rather than in this module: the administrative list
        # reads it through the manager it is already given, and a process may
        # hold more than one application (keepup-21).
        manager.resolution = resolution
        for unknown in resolution.unknown:
            logger.warning(
                f"{enablement.ENABLE_ENV}/{enablement.DISABLE_ENV} name "
                f"an undeclared plugin: {unknown}"
            )
        logger.info(enablement.summary_line(resolution))

        enabled = set(enablement.enabled_ids(resolution))
        for plugin_config in enablement.declared_plugins(config):
            plugin_id = plugin_config['id']
            if plugin_id in enabled:
                manager.load_plugin(
                    plugin_id,
                    plugin_config.get('config', {}),
                    priority=plugin_config.get('priority') or 0,
                )
            else:
                # Recorded rather than passed over: a plugin switched off and a
                # plugin that fell over look the same from outside.
                manager.record_outcome(plugin_id, OUTCOME_DISABLED)

        if await manager.initialize_plugins():
            logger.info("All plugins initialized successfully")
        else:
            logger.error("Some plugins failed to initialize")

        initialized = sum(1 for plugin_id in enablement.declared_plugins(config)
                          if manager.get_outcome(plugin_id['id'])[0] == OUTCOME_INITIALIZED)
        declared = len(enablement.declared_plugins(config))
        logger.info(f"Plugins: initialized {initialized} of {declared} declared")

        await register_plugin_routes(app, manager)
        return resolution

    except Exception as e:
        logger.error(f"Error initializing plugins: {str(e)}")


async def run_post_construct_processors(manager):
    """Run every plugin's post-construct method in a separate executor."""
    try:
        logger.info("Starting post-construct processors in background...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="post_construct") as executor:
            tasks = []

            for plugin_id, plugin in manager.plugins.items():
                if plugin.initialized and callable(getattr(plugin, 'post_construct', None)):
                    task = asyncio.get_event_loop().run_in_executor(
                        executor,
                        _run_plugin_post_construct_sync,
                        plugin_id, plugin
                    )
                    tasks.append(task)
            if tasks:
                done, pending = await asyncio.wait(tasks, timeout=300)

                for task in pending:
                    task.cancel()

                logger.info(f"Post-construct completed: {len(done)} successful, {len(pending)} timed out/cancelled")

        logger.info("All post-construct processors completed")

    except Exception as e:
        logger.error(f"Error running post-construct processors: {str(e)}")


def _run_plugin_post_construct_sync(plugin_id: str, plugin):
    """Synchronous wrapper running post_construct in its own thread."""
    try:
        logger.info(f"Running post-construct for plugin: {plugin_id}")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            result = loop.run_until_complete(plugin.post_construct())
            logger.info(f"Post-construct completed for plugin: {plugin_id}")
            return result
        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Error in post-construct for plugin {plugin_id}: {str(e)}")
        return None
