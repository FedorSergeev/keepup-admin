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
    "load_and_initialize",
    "raw_request_wrapper",
    "read_plugin_overrides",
    "register_plugin_admin_routes",
    "register_plugin_routes",
    "run_post_construct_processors",
    "signed_in_websocket",
    "write_plugin_override",
]

logger = logging.getLogger(__name__)


async def load_and_initialize(manager, config_path=MODULES_CONFIG_PATH, environ=None):
    """Read the declaration, then load and initialise the plugins it enables.

    No application is needed and none is touched: loading a plugin and letting it
    publish its services is one thing, and binding what it contributes to a
    transport is another (keepup-102). A runtime that serves no HTTP calls this
    and stops here.

    An unreadable declaration is reported and the deployment runs without
    plugins; a single plugin that cannot be loaded or initialised is recorded
    against itself and the rest still come up (keepup/plugins/base.py).

    Args:
        manager: the plugin manager.
        config_path: the catalogue the application named.
        environ: the environment the enablement rules read; the process's own
            when None.

    Returns:
        The resolution, or None when the declaration could not be read at all.
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except (OSError, ValueError) as error:
        logger.error(f"Plugin declaration {config_path} is unreadable: {error}")
        return None

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
    return resolution


async def initialize_plugins(app, manager, config_path=MODULES_CONFIG_PATH, environ=None):
    """Load, initialise and bind the plugins to this application.

    Route registration is outside the try that the loading phase tolerates: a
    declaration the route runtime refuses -- a mask naming a parameter the
    handler does not take, a permission on a route nobody signs in to -- is a
    mistake found here, at the start, rather than a route that quietly goes
    missing (keepup-102).

    Args:
        app: the FastAPI application the routes are registered on.
        manager: the plugin manager.
        config_path: the catalogue the application named.
        environ: the environment the enablement rules read.

    Returns:
        The resolution, or None when the declaration could not be read at all.
    """
    resolution = await load_and_initialize(manager, config_path, environ)
    if resolution is None:
        return None
    await register_plugin_routes(app, manager)
    return resolution


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


async def _post_construct(plugin_id: str, plugin):
    """The plugin's post_construct, on this replica or on one replica of the set.

    Once per set: the replica that takes the lock runs it and leaves the lock in
    place -- it expires after post_construct_quiet_seconds -- so a replica of the
    same rollout that starts a minute later finds it taken and skips. Released,
    the lock would only stop replicas that start at the very same moment.
    """
    if not getattr(plugin, "post_construct_once_per_cluster", False):
        return await plugin.post_construct()
    from keepup.locks import DatabaseLock
    lock = DatabaseLock(f"post_construct_{plugin_id}", timeout=5,
                        max_lock_time=int(plugin.post_construct_quiet_seconds))
    if not await lock.acquire():
        logger.info(f"Post-construct of {plugin_id} ran on another replica; skipped here")
        return None
    return await plugin.post_construct()


def _run_plugin_post_construct_sync(plugin_id: str, plugin):
    """Synchronous wrapper running post_construct in its own thread."""
    try:
        logger.info(f"Running post-construct for plugin: {plugin_id}")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            result = loop.run_until_complete(_post_construct(plugin_id, plugin))
            logger.info(f"Post-construct completed for plugin: {plugin_id}")
            return result
        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Error in post-construct for plugin {plugin_id}: {str(e)}")
        return None
