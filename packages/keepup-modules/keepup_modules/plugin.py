"""The catalogue of panel sections as a capability (keepup-113).

`modules.py` held the model -- which section exists, who may see it, where its
files are -- in the kernel's package, together with the ten routes that manage
it; `schema.py` created its two tables for every deployment, panel or not. The
capability owns both now:

* the model is the `catalogue` service, read on every request for a person's
  roles (through the replica cache, which is why a section's change is published
  to the other replicas);
* the declaration of a section is *data*: the navigation fields are columns of
  `frontend_modules` (`keepup_modules/tables.py`), and the file the framework
  ships is imported into the database at start instead of being read by the
  panel;
* the import runs on the freeze (``post_construct``), because it is the union of
  what the running plugins declare, the framework's own `sections.json` and the
  application's `config/modules.json` -- the optional set is only known then
  (doc/plugin_constructor.md, 9.1);
* the decision about which backend plugins run is this capability's too
  (`plugin_decisions`), because the kernel reads it without a panel.

The ten routes are declared in `keepup_modules.catalogue` and registered by the
application until the transport that mounts what a plugin declares exists
(`keepup-http`, keepup-121).
"""

import asyncio
import logging
from typing import Any, Dict, List

from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.plugins.base import BasePlugin

logger = logging.getLogger(__name__)

__all__ = ["ModulesPlugin", "SERVICE_CATALOGUE", "SERVICE_PLUGIN_DECISIONS"]

#: What the panel asks for to know which sections a person may see.
SERVICE_CATALOGUE = "catalogue"

#: What the runtime reads to know what an administrator decided about a plugin.
SERVICE_PLUGIN_DECISIONS = "plugin_decisions"


class ModulesPlugin(BasePlugin):
    """The catalogue of sections, and the decision about the plugins."""

    descriptor = PluginDescriptor(
        id="modules",
        name="Module management",
        version="0.5.0",
        distribution="keepup-modules",
        kind=KIND_OPTIONAL,
        priority=70,
        provides=(f"{SERVICE_CATALOGUE}>=1", f"{SERVICE_PLUGIN_DECISIONS}>=1"),
        requires=("datasource>=1",),
        contributions=("routes", "sections", "tables"),
    )

    def __init__(self, config=None):
        super().__init__("modules", "Module management", config)

    def register(self, services):
        """Publish the catalogue model where there is a database to read it from."""
        if services.has(SERVICE_DATASOURCE):
            from keepup_modules import catalogue, decisions

            services.provide(SERVICE_CATALOGUE, catalogue, version=1, plugin_id="modules")
            services.provide(SERVICE_PLUGIN_DECISIONS, decisions, version=1,
                             plugin_id="modules")

    async def initialize(self):
        """Nothing to build: the tables are the data source's, the import is the freeze's."""
        return True

    async def post_construct(self):
        """Import what the sections declare, now that every plugin is known.

        Idempotent, and the same rule for both files: the fields that are code
        are the declaring plugin's and are re-pointed, while the name, the
        description, `is_active` and the grants are the administrator's and are
        only ever inserted (doc/plugin_constructor.md, 9.1).
        """
        settings = getattr(self.plugin_manager, "settings", None)
        path = getattr(settings, "plugins_config_path", None)
        from keepup_modules.catalogue import (import_declared_sections,
                                              set_running_plugins,
                                              sync_framework_sections,
                                              sync_new_modules_from_json)

        added = await asyncio.to_thread(sync_framework_sections)
        declared = await asyncio.to_thread(sync_new_modules_from_json, path)
        # What the running plugins declare themselves, each attributed to the
        # plugin that declared it: a section of a plugin that does not run is
        # not in the panel, and the row waits for it to come back (keepup-113).
        plugins = getattr(self.plugin_manager, "loaded_plugins", {}) or {}
        for plugin_id, plugin in sorted(plugins.items()):
            sections = plugin.get_panel_sections() if hasattr(plugin, "get_panel_sections") else []
            if sections:
                await asyncio.to_thread(import_declared_sections,
                                        {"modules": list(sections)}, plugin_id)
        set_running_plugins(plugins)
        logger.info("Catalogue imported: %s framework section(s), %s declared; %s plugin(s) running",
                    added, declared, len(plugins))

    def get_declared_tables(self) -> List[Any]:
        """The catalogue's tables, declared by the capability that owns them."""
        from keepup_modules.tables import FRONTEND_MODULES, PLUGIN_OVERRIDES, ROLE_MODULES

        return [FRONTEND_MODULES, ROLE_MODULES, PLUGIN_OVERRIDES]

    def get_api_routes(self) -> List[Dict[str, Any]]:
        """No routes yet: the application registers the ten through the bridge."""
        return []

    def get_handlers(self) -> Dict[str, Any]:
        from keepup_modules import catalogue

        return {"catalogue": catalogue}
