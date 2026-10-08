"""The panel as a capability: the shell, its sections and its themes.

Task keepup-104, first half. The shell, the sections and the theme chrome are
what a deployment shows people, and they were the kernel's -- served, catalogued
and themed by modules every deployment carried, including the ones whose only
purpose was a socket.

The plugin claims them and publishes the `ui` service: the sections another
capability contributed (`get_panel_sections()`), what the catalogue granted a
role, and the themes a deployment may keep in the database. Storage is a *want*,
not a requirement: a panel whose sections come from code can be served with no
database at all, which is exactly the deployment `doc/plugin_constructor.md`
calls a server that is not an admin panel. The shell's own routes, its static
mount and its CSP middleware travel here in keepup-124.
"""

import logging
from typing import Any, Dict, List, Mapping, Optional

from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.plugins.base import BasePlugin
from keepup.themes import VISUAL_THEMES

logger = logging.getLogger(__name__)

__all__ = ["UiPlugin", "UiService", "SERVICE_UI"]

#: What another plugin asks for to offer a section or ask what a role may see.
SERVICE_UI = "ui"


class UiService:
    """What the panel shows: contributed sections, granted ones, and themes.

    It is a reading of what the runtime collected plus the catalogue the panel
    keeps in the database (`keepup.modules`), so a caller never learns which of
    the two answered -- and never imports either.
    """

    def __init__(self, contributions: Any = None, datasource: Any = None):
        self.contributions = contributions
        self.datasource = datasource

    def contributed_sections(self) -> List[Dict[str, Any]]:
        """The sections plugins brought, in the order their priorities give."""
        if self.contributions is None:
            return []
        # Read through the handle rather than holding the collection: the runtime
        # collects after registration, and a copy taken then would be empty.
        collection = self.contributions() if callable(self.contributions) else self.contributions
        if collection is None:
            return []
        return [dict(section) for section in collection.of("sections")]

    def sections_for(self, user: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        """The sections this person may see.

        Args:
            user: the account as a route received it, or None for nobody.

        Returns:
            The granted catalogue rows when there is a catalogue, and the
            contributed sections when there is not: a panel served from code
            alone is a deployment too.
        """
        if self.datasource is None:
            return self.contributed_sections()
        try:
            from keepup import modules

            return modules.get_modules_for_roles(user or {})
        except Exception as error:  # noqa: BLE001 - a catalogue that cannot be read is not a broken panel
            logger.warning("Could not read the section catalogue: %s", error)
            return self.contributed_sections()

    async def themes(self) -> List[Dict[str, Any]]:
        """The themes a deployment added, if it has storage to keep them in."""
        if self.datasource is None:
            return []
        rows = await self.datasource.execute("SELECT * FROM visual_themes ORDER BY id")
        return [dict(row) for row in (rows or [])]


class UiPlugin(BasePlugin):
    """The capability: the shell, its sections, its themes."""

    descriptor = PluginDescriptor(
        id="ui",
        name="Panel",
        version="0.4.0",
        distribution="keepup-ui",
        kind=KIND_OPTIONAL,
        priority=70,
        provides=("ui>=1",),
        wants=("datasource>=1",),
        contributions=("routes", "sections", "tables", "middleware", "themes"),
    )

    def __init__(self, config=None):
        super().__init__("ui", "Panel", config)

    def register(self, services):
        """Publish the panel, with or without storage behind it.

        Args:
            services: the runtime's registry.
        """
        from keepup.kernel.datasource import SERVICE_DATASOURCE

        self.service = UiService(
            contributions=lambda: self.contributions,
            datasource=services.require(SERVICE_DATASOURCE)
            if services.has(SERVICE_DATASOURCE) else None,
        )
        services.provide(SERVICE_UI, self.service, version=1, plugin_id="ui")

    async def initialize(self):
        """The service was published at registration; build one if there is none."""
        if getattr(self, "service", None) is None:
            self.service = UiService(contributions=self.contributions)
        return True

    def get_declared_tables(self) -> List[Any]:
        """The theme table this capability owns, declared once, until keepup-124."""
        return [VISUAL_THEMES]

    def get_panel_sections(self) -> List[Dict[str, Any]]:
        """The sections the panel itself offers, beyond what plugins contribute."""
        return [{
            "id": "panel_sections",
            "name": "Panel sections",
            "description": "Which sections exist and which roles may see them",
            "js": "/keepup-static/modules/js/panel_sections.js",
            "css": "/keepup-static/modules/css/panel_sections.css",
            "initFunction": "initPanelSections",
            "icon": "layout",
            "version": "1.0.0",
        }]

    def get_api_routes(self) -> List[Any]:
        """No routes yet: the shell's pages and static mounts move here in keepup-124."""
        return []

    def get_handlers(self) -> Dict[str, Any]:
        """What another plugin may reach instead of importing the panel."""
        return {"service": self.service, "sections": self.service.contributed_sections}
