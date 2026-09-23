"""What the deployment decided about each plugin, and the surface that shows it.

Three sources decide whether a declared plugin runs: its own flag in the
configuration file, an administrator's override kept in this deployment's
database, and the environment of this stand. The rule itself is
``keepup.plugins.enablement``, which is pure; what lives here is everything
around it that is not -- reading the file, reading and writing the overrides,
and the administrative endpoints that report the decision and change it.

The endpoints are the reason any of this is worth reporting at all. Once the
server is up, a plugin that was switched off and a plugin that fell over look
exactly the same from outside: the routes are absent and the server is
healthy. The difference is kept by the manager and published here.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict

from fastapi import Depends, HTTPException

from keepup.auth.dependencies import get_current_admin, get_current_user
from keepup.db import DatabaseManagerV2
from keepup.plugins import enablement
from keepup.plugins import route_mask

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "clear_plugin_override",
    "get_plugins",
    "get_plugins_status",
    "read_plugin_overrides",
    "register_plugin_admin_routes",
    "write_plugin_override",
]

logger = logging.getLogger(__name__)

#: Where this deployment declares its plugins. An application that keeps the
#: file elsewhere passes its own path to initialize_plugins(); this is the
#: default, and what the administrative endpoints read.
MODULES_CONFIG_PATH = 'config/modules.json'


def read_plugin_overrides():
    """What an administrator decided from the panel, per plugin (task 65).

    Never fatal: a database that cannot answer must not keep the server from
    starting. Without the overrides the file and the environment still decide,
    which is the behaviour that existed before the panel could.
    """
    try:
        rows = DatabaseManagerV2.execute(
            "SELECT plugin_id, enabled FROM plugin_overrides", {})
    except Exception as e:
        logger.warning(f"Could not read plugin overrides, falling back to file and environment: {e}")
        return {}
    return {row["plugin_id"]: bool(row["enabled"]) for row in rows}


def write_plugin_override(plugin_id: str, enabled: bool, changed_by=None) -> None:
    """Record the panel's decision about one plugin, replacing any previous."""
    updated = DatabaseManagerV2.execute_commit(
        "UPDATE plugin_overrides SET enabled = :enabled, changed_by = :who, "
        "changed_at = :now WHERE plugin_id = :pid",
        {"enabled": enabled, "who": changed_by, "now": datetime.utcnow(), "pid": plugin_id})
    if not updated:
        DatabaseManagerV2.execute_commit(
            "INSERT INTO plugin_overrides (plugin_id, enabled, changed_by, changed_at) "
            "VALUES (:pid, :enabled, :who, :now)",
            {"pid": plugin_id, "enabled": enabled, "who": changed_by, "now": datetime.utcnow()})


def clear_plugin_override(plugin_id: str) -> None:
    """Give the decision back to the file and the environment."""
    DatabaseManagerV2.execute_commit(
        "DELETE FROM plugin_overrides WHERE plugin_id = :pid", {"pid": plugin_id})


def _declared_plugin_ids():
    with open(MODULES_CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)
    return {p["id"] for p in enablement.declared_plugins(config)}


def _refuse_if_the_deployment_decides(plugin_id: str):
    """A plugin named in the environment is not the panel's to switch.

    Storing an override the environment would overrule is a trap: the
    administrator switches it, restarts, and nothing changes. The refusal
    names the reason instead.
    """
    if plugin_id not in _declared_plugin_ids():
        raise HTTPException(status_code=404, detail="config/modules.json does not declare this plugin")
    if enablement.decided_by_environment(plugin_id, os.environ):
        raise HTTPException(
            status_code=409,
            detail=(f"This plugin is decided by the deployment: "
                    f"{enablement.ENABLE_ENV}/{enablement.DISABLE_ENV} name it. "
                    f"Change the environment of this stand and restart."))


async def get_plugins(manager, current_user):
    """The plugins available to this user: granted to the role and running.

    A module-level function rather than only a closure of the route, because
    this is the decision worth checking and a closure can be reached only
    through the HTTP layer.
    """
    try:
        with open(MODULES_CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)

        user_role = current_user['role']
        role_config = None
        for role in config.get('roles', []):
            if role.get('name') == user_role:
                role_config = role
                break

        if not role_config:
            return {"plugins": []}
        allowed_plugin_names = role_config.get('plugins', [])

        # The role's list is visibility, not enablement: a plugin the role is
        # shown but that did not come up has no routes to advertise.
        available_plugins = []
        for plugin_config in config.get('plugins', []):
            if plugin_config.get('id') in allowed_plugin_names \
                    and manager.get_plugin(plugin_config.get('id')) is not None:
                available_plugins.append({
                    "id": plugin_config.get('id'),
                    "name": plugin_config.get('name'),
                    "config": plugin_config.get('config', {})
                })

        return {"plugins": available_plugins}

    except Exception as e:
        logger.error(f"Error getting plugins: {str(e)}")
        return {"plugins": []}


async def get_plugins_status(manager, current_user=None):
    """The state of every declared plugin.

    Enabled or not and from where, loaded or not, initialised or not, and why.
    An enabled plugin that did not come up is otherwise visible only as a line
    in the start-up log.
    """
    try:
        with open(MODULES_CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
        # Two resolutions: the snapshot taken at start-up is what is actually
        # running, and the one computed now carries the panel's latest
        # decisions. Where they differ, the row awaits a restart.
        desired = enablement.resolve(
            config, os.environ, overrides=read_plugin_overrides())
        return {"plugins": enablement.status_report(
            config, manager.resolution, manager,
            desired=desired, environ=os.environ)}
    except Exception as e:
        logger.error(f"Error reading plugin status: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


async def set_plugin_enabled(plugin_id: str, request: Dict[str, Any], admin: dict):
    """Switch a backend plugin on or off from the panel.

    Takes effect at the next start: the plugin's routes are built once, at
    start-up, and are not removed from a running application.
    """
    _refuse_if_the_deployment_decides(plugin_id)
    enabled = (request or {}).get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="Field 'enabled' must be true or false")
    try:
        write_plugin_override(plugin_id, enabled, changed_by=admin.get("id"))
    except Exception as e:
        logger.error(f"Could not store the plugin override for {plugin_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
    return {"success": True, "plugin_id": plugin_id, "enabled": enabled,
            "pending_restart": True}


async def clear_plugin_enabled(plugin_id: str, admin: dict = None):
    """Give the decision about this plugin back to the file and the environment."""
    _refuse_if_the_deployment_decides(plugin_id)
    try:
        clear_plugin_override(plugin_id)
    except Exception as e:
        logger.error(f"Could not clear the plugin override for {plugin_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
    return {"success": True, "plugin_id": plugin_id}


def register_plugin_admin_routes(app, manager):
    """Register the plugin endpoints on the application."""

    @app.get("/api/plugins")
    async def get_plugins_endpoint(current_user: dict = Depends(get_current_user)):
        """Return the plugins available to the current user."""
        return await get_plugins(manager, current_user)

    @app.get("/api/admin/plugins")
    async def get_plugins_status_endpoint(admin: dict = Depends(get_current_admin)):
        """The state of every declared plugin (administrators only)."""
        return await get_plugins_status(manager, admin)

    @app.get("/api/admin/route-masks")
    async def get_route_masks_endpoint(admin: dict = Depends(get_current_admin)):
        """The declared masks of every plugin route (administrators only).

        Machine-readable on purpose: the point of declaring what a route
        accepts is that a wrong request can be refused before the application,
        at a gateway or a proxy, and neither of those can read a signature.

        Behind the administrator's role because it is a map of the
        application's surface -- not a secret, but not something to hand to
        everybody who signed in either.
        """
        return {"routes": route_mask.describe_routes(manager.get_all_api_routes())}

    @app.post("/api/admin/plugins/{plugin_id}/enabled")
    async def set_plugin_enabled_endpoint(plugin_id: str, request: Dict[str, Any],
                                          admin: dict = Depends(get_current_admin)):
        """Switch a backend plugin on or off from the panel (administrators only)."""
        return await set_plugin_enabled(plugin_id, request, admin)

    @app.delete("/api/admin/plugins/{plugin_id}/enabled")
    async def clear_plugin_enabled_endpoint(plugin_id: str,
                                            admin: dict = Depends(get_current_admin)):
        """Give the decision about this plugin back to the file (administrators only)."""
        return await clear_plugin_enabled(plugin_id, admin)

    # There is deliberately no route that calls a named handler by name.
    # get_handlers() is how one plugin calls another inside the process, which
    # is a trusted surface: a handler is written for a caller that has already
    # decided who may do this, so many of them check nothing themselves.
    # Published over HTTP it turned every such handler into an endpoint open to
    # anybody signed in, and the difference between "no such plugin" and "no
    # such handler" listed them for free (task keepup-10). A plugin publishes
    # what it declares in get_api_routes(), and nothing besides.
