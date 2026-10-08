"""System settings and visual themes."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException

from keepup.auth.dependencies import get_current_admin

from keepup import admin_trail, cache, tables
from keepup.db import DatabaseManagerV2

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "ConfigService",
    "config_service",
    "register_theme_routes",
]

# The panel declares its own tables (keepup-124); re-exported here so the path
# that predates the catalogue still creates them.
from keepup_ui.tables import VISUAL_THEMES  # noqa: E402

logger = logging.getLogger(__name__)

THEMES_TABLE = "visual_themes"

#: The replica cache that holds the active theme (keepup/cache.py): every page
#: load asks for it, and it changes when an administrator changes it.
ACTIVE_THEME_CACHE = "active_theme"



class ConfigService:
    """Reads and writes system settings and visual themes."""
    THEMES_TABLE = THEMES_TABLE
    INSERT_DEFAULT_THEME_SQL = """
        INSERT INTO visual_themes (theme_name, main_page_file, is_active)
        VALUES (:theme_name, :main_page_file, :is_active)
        ON CONFLICT (theme_name) DO NOTHING
    """

    INSERT_DEFAULT_THEME_SQLITE = """
        INSERT OR IGNORE INTO visual_themes (theme_name, main_page_file, is_active)
        VALUES (:theme_name, :main_page_file, :is_active)
    """

    GET_ACTIVE_THEME_SQL = """
        SELECT theme_name, main_page_file, brand_name, logo_url, is_active
        FROM visual_themes
        WHERE is_active = TRUE
        LIMIT 1
    """

    GET_ALL_THEMES_SQL = """
        SELECT id, theme_name, main_page_file, brand_name, logo_url, is_active
        FROM visual_themes
        ORDER BY id
    """

    SET_THEME_ACTIVE_SQL = """
        UPDATE visual_themes
        SET is_active = (id = :theme_id),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = :theme_id OR is_active = TRUE
    """

    CREATE_THEME_SQL = """
        INSERT INTO visual_themes (theme_name, main_page_file, brand_name, logo_url, is_active)
        VALUES (:theme_name, :main_page_file, :brand_name, :logo_url, :is_active)
        ON CONFLICT (theme_name) DO UPDATE SET
            main_page_file = EXCLUDED.main_page_file,
            brand_name = EXCLUDED.brand_name,
            logo_url = EXCLUDED.logo_url,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
    """

    CREATE_THEME_SQLITE = """
        INSERT OR REPLACE INTO visual_themes (theme_name, main_page_file, brand_name, logo_url, is_active)
        VALUES (:theme_name, :main_page_file, :brand_name, :logo_url, :is_active)
    """

    THEME_STATE_SQL = """
        SELECT id, is_active FROM visual_themes WHERE id = :theme_id
    """

    DELETE_THEME_SQL = """
        DELETE FROM visual_themes
        WHERE id = :theme_id
        AND is_active = FALSE
    """

    def __init__(self):
        """Create the service without touching the database.

        The bootstrap used to run from here, and this module builds its service
        at import: themes were therefore created before the application existed,
        with an empty list of declared ones. Nothing an application did
        afterwards could fix that -- see ``initialize``.
        """
        self._initialized = False
        self._initializing = False

    def initialize(self):
        """Create the themes table and make the declared themes exist.

        Called when the application is assembled, after it has declared its
        themes, and lazily by the first use of the service -- whoever comes
        first gets a working one. Idempotent: a second call changes nothing.
        """
        if self._initialized:
            return

        logger.info("Initializing ConfigService...")

        self._initializing = True
        try:
            DatabaseManagerV2.initialize()
            self._ensure_themes_table()
            self._ensure_default_theme()
            # Bringing the service up writes the declared themes: what anyone
            # cached before that is no longer the answer.
            cache.invalidate_everywhere(ACTIVE_THEME_CACHE)
            self._initialized = True
            logger.info("ConfigService initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize ConfigService: {str(e)}")
            raise
        finally:
            self._initializing = False

    def _ensure_ready(self):
        """Raise the service if nobody has yet.

        The re-entry guard matters: the bootstrap itself reads the themes, and
        without it the first read inside ``initialize`` would call it again.
        """
        if self._initialized or self._initializing:
            return
        self.initialize()

    def _ensure_themes_table(self):
        """Create the visual themes table, or give an older one its branding columns."""
        tables.ensure_tables(VISUAL_THEMES)

    #: (theme_name, main_page_file, brand_name, logo_url), supplied by the
    #: application through register_built_in_themes(). The framework ships no
    #: theme of its own: a theme is a product's face, and a default one here
    #: would put somebody else's brand in the corner of every application built
    #: on this. A theme that carries no brand leaves the name baked into the
    #: markup by replace_placeholder.sh at build time.
    BUILT_IN_THEMES = ()

    def register_built_in_themes(self, themes):
        """Declare the themes this application ships, and make them exist.

        Applying them here rather than only remembering them is what makes the
        order of calls stop mattering: the application may declare its themes
        before the service is up or after, and either way they reach the
        database. Remembering alone was the defect -- the only place that read
        the list had already run, at import.
        """
        self.BUILT_IN_THEMES = tuple(themes)
        if self._initialized:
            self._ensure_built_in_themes()

    def _ensure_built_in_themes(self):
        """Register the themes shipped with the application.

        Idempotent, like every other DDL here: an existing row keeps its id and
        its active flag, so re-running this never steals the active theme.
        """
        existing = {theme["theme_name"] for theme in self.get_all_themes()}
        for theme_name, page_file, brand_name, logo_url in self.BUILT_IN_THEMES:
            if theme_name in existing:
                continue
            self.create_theme(
                theme_name=theme_name,
                main_page_file=page_file,
                is_active=False,
                brand_name=brand_name,
                logo_url=logo_url,
            )
            logger.info(f"Built-in theme '{theme_name}' registered")

    def _ensure_default_theme(self):
        """Make sure the database holds a theme, and that one of them is active.

        A database with no themes at all happens once in its life, and at that
        moment the only party that knows what the panel is called is the
        application. So its themes go in and the first of them becomes active;
        the nameless default is for an application that ships none -- otherwise
        a fresh stand greets people with the name left over from the build.

        A database that already holds themes keeps the administrator's choice:
        restarting MUST NOT move the active flag.
        """
        themes = self.get_all_themes()

        if themes:
            if not self.get_active_theme():
                logger.info("No active theme found. Activating first theme...")
                self.set_active_theme(themes[0]['id'])
            self._ensure_built_in_themes()
            return

        self._ensure_built_in_themes()
        declared = self.get_all_themes()
        if declared:
            logger.info("Empty database: activating the theme the application ships")
            self.set_active_theme(declared[0]['id'])
            return

        logger.info("No themes found and none declared. Adding default theme...")
        self._add_default_theme()
        logger.info("Default theme added successfully")

    def _add_default_theme(self):
        """Insert the default theme."""
        from keepup.db import db_config

        params = {
            "theme_name": "default",
            "main_page_file": "index_new.html",
            "is_active": True if db_config.is_postgres() else 1
        }

        if db_config.is_postgres():
            DatabaseManagerV2.execute_one(
                self.INSERT_DEFAULT_THEME_SQL,
                params
            )
        else:
            DatabaseManagerV2.execute_commit(
                self.INSERT_DEFAULT_THEME_SQLITE,
                params
            )

    def get_active_theme(self) -> Optional[Dict[str, Any]]:
        """Return the currently active theme.

        Every page load asks, so the answer is kept in this replica for the
        cache's lifetime (keepup/cache.py) and dropped when a theme changes.

        Returns:
            Dict with theme_name, main_page_file, brand_name, logo_url and
            is_active, or None when no theme is active.
        """
        self._ensure_ready()
        try:
            theme = cache.get_cache(ACTIVE_THEME_CACHE).get("active", self._read_active_theme)
        except Exception as e:
            # Not kept: the next page asks the database again.
            logger.error(f"Error getting active theme: {str(e)}")
            return None
        # A copy: a caller that edits what it got must not edit the cache.
        return dict(theme) if theme else theme

    def _read_active_theme(self) -> Optional[Dict[str, Any]]:
        result = DatabaseManagerV2.execute_one(self.GET_ACTIVE_THEME_SQL)
        if result and 'is_active' in result:
            from keepup.db import db_config
            if db_config.is_sqlite():
                result['is_active'] = bool(result['is_active'])
        return result

    def get_active_theme_brand(self) -> Dict[str, Any]:
        """Return the branding of the active theme.

        Only the name and the logo: this is served without authentication, so
        nothing else about the theme may leak through it.
        """
        theme = self.get_active_theme() or {}
        return {
            "brand_name": theme.get("brand_name"),
            "logo_url": theme.get("logo_url"),
        }

    def get_active_theme_page_file(self) -> str:
        """Return the main page file name of the active theme.

        Returns:
            The file name, or 'index_new.html' by default.
        """
        active_theme = self.get_active_theme()

        if active_theme and 'main_page_file' in active_theme:
            return active_theme['main_page_file']

        logger.warning("No active theme found, using default 'index_new.html'")
        return "index_new.html"

    def get_all_themes(self) -> List[Dict[str, Any]]:
        """Return every visual theme.

        Returns:
            List[Dict]: the themes.
        """
        self._ensure_ready()
        try:
            themes = DatabaseManagerV2.execute(self.GET_ALL_THEMES_SQL)
            from keepup.db import db_config
            for theme in themes:
                if db_config.is_sqlite() and 'is_active' in theme:
                    theme['is_active'] = bool(theme['is_active'])

            return themes

        except Exception as e:
            logger.error(f"Error getting all themes: {str(e)}")
            return []

    def set_active_theme(self, theme_id: int) -> bool:
        """Make a theme active.

        Args:
            theme_id: id of the theme to activate

        Returns:
            bool: whether the operation succeeded.
        """
        self._ensure_ready()
        try:
            DatabaseManagerV2.execute_commit(
                self.SET_THEME_ACTIVE_SQL,
                {"theme_id": theme_id}
            )
            cache.invalidate_everywhere(ACTIVE_THEME_CACHE)
            logger.info(f"Theme {theme_id} set as active")
            return True

        except Exception as e:
            logger.error(f"Error setting active theme: {str(e)}")
            return False

    def create_theme(self, theme_name: str, main_page_file: str, is_active: bool = False,
                     brand_name: str = None, logo_url: str = None) -> Optional[int]:
        """Create a theme.

        Args:
            theme_name: display name
            main_page_file: main page file name
            is_active: whether the theme becomes active
            brand_name: panel name this theme shows in the top left corner
            logo_url: image shown there instead of the name

        Returns:
            int: id of the created theme, or None on error.
        """
        self._ensure_ready()
        from keepup.db import db_config
        from keepup.web import static_page

        # Checked here as well as when the page is served. Authorisation alone
        # would leave an administrator reading the whole file system of the
        # server through the panel, and a name refused at the door never has to
        # be refused again by every reader of the column.
        if static_page(main_page_file) is None:
            logger.error("Refusing a theme whose page (%r) is outside the front-end directory",
                         main_page_file)
            raise HTTPException(
                status_code=400,
                detail="main_page_file must name a file inside the front-end directory")

        params = {
            "theme_name": theme_name,
            "main_page_file": main_page_file,
            "brand_name": brand_name,
            "logo_url": logo_url,
            "is_active": is_active if db_config.is_postgres() else (1 if is_active else 0)
        }

        try:
            if db_config.is_postgres():
                result = DatabaseManagerV2.execute_one(
                    self.CREATE_THEME_SQL,
                    params
                )
                theme_id = result['id'] if result else None
            else:
                # The insert and last_insert_rowid() in one session: asked
                # separately, the second query could go to another pooled
                # connection and answer another insert's id, or 0 -- and the
                # theme activated would not be this one (keepup-82).
                result = DatabaseManagerV2.execute_commit_returning(
                    self.CREATE_THEME_SQLITE, params, "id")
                theme_id = result['id'] if result else None

            # An upsert: a theme of the same name may be the active one, with a
            # new page or branding now.
            cache.invalidate_everywhere(ACTIVE_THEME_CACHE)

            if theme_id and is_active:
                self.set_active_theme(theme_id)

            logger.info(f"Theme '{theme_name}' created with id {theme_id}")
            return theme_id

        except Exception as e:
            logger.error(f"Error creating theme: {str(e)}")
            return None

    def deletion_refusal(self, theme_id: int) -> Optional[str]:
        """Why this theme cannot be deleted, or None when it can.

        Read by id from the table. The check used to compare with the cached
        active theme, whose query does not select the id, so it never fired:
        only the condition in the delete kept the active theme, and the answer
        could not say which of "not found" and "active" it was (keepup-58).
        """
        self._ensure_ready()
        row = DatabaseManagerV2.execute_one(self.THEME_STATE_SQL, {"theme_id": theme_id})
        if not row:
            return f"There is no theme {theme_id}."
        if row.get("is_active"):
            return "The active theme cannot be deleted; activate another theme first."
        return None

    def delete_theme(self, theme_id: int) -> bool:
        """Delete a theme. The active theme cannot be deleted.

        Args:
            theme_id: id of the theme to delete

        Returns:
            bool: whether the operation succeeded.
        """
        self._ensure_ready()
        try:
            refusal = self.deletion_refusal(theme_id)
            if refusal:
                logger.warning(f"Theme {theme_id} not deleted: {refusal}")
                return False

            # The condition in the statement stays: the theme may have been
            # activated between the check and the delete.
            affected = DatabaseManagerV2.execute_commit(
                self.DELETE_THEME_SQL,
                {"theme_id": theme_id}
            )

            if affected > 0:
                logger.info(f"Theme {theme_id} deleted")
                return True
            else:
                logger.warning(f"Theme {theme_id} not found or is active")
                return False

        except Exception as e:
            logger.error(f"Error deleting theme: {str(e)}")
            return False

    def is_initialized(self) -> bool:
        """Return whether the service has been initialised."""
        return self._initialized


# --- HTTP surface -------------------------------------------------------------
#
# The branding endpoint answers without authentication on purpose: the logo and
# the product name are on the sign-in screen, before anyone has signed in.


def register_theme_routes(app, config_service):
    """Register the visual theme endpoints on the application."""

    @app.get("/api/theme/brand")
    async def get_active_theme_brand():
        """Return the active theme's branding.

        Deliberately unauthenticated: the logo is on the login screen, before there
        is a user to authorise. The response carries only the name and the logo URL.
        """
        return config_service.get_active_theme_brand()
    # These four change what every visitor of the panel is served, so they are
    # administrative -- and until task keepup-10 that was said in a docstring
    # and nowhere in the code. A docstring is not a check: anybody at all could
    # create a theme, point it at a file outside the front-end directory and
    # read it back through /selfcare.
    @app.get("/themes")
    async def get_all_themes(admin: dict = Depends(get_current_admin)):
        """Return every theme."""
        return {
            "themes": config_service.get_all_themes(),
            "active_theme": config_service.get_active_theme()
        }
    @app.post("/themes/{theme_id}/activate")
    async def activate_theme(theme_id: int, admin: dict = Depends(get_current_admin)):
        """Activate a theme by id."""
        success = config_service.set_active_theme(theme_id)

        if success:
            await admin_trail.record(admin_trail.THEMES_CHANGED, admin,
                                     f"Theme {theme_id} activated",
                                     action="activate", theme_id=theme_id)
            return {"success": True, "message": f"Theme {theme_id} activated"}
        else:
            return {"success": False, "message": f"Failed to activate theme {theme_id}"}
    @app.post("/themes/create")
    async def create_theme(theme_name: str, main_page_file: str, is_active: bool = False,
                           admin: dict = Depends(get_current_admin)):
        """Create a theme."""
        theme_id = config_service.create_theme(theme_name, main_page_file, is_active)

        if theme_id:
            await admin_trail.record(admin_trail.THEMES_CHANGED, admin,
                                     f"Theme {theme_id} created",
                                     action="create", theme_id=theme_id,
                                     theme_name=theme_name, main_page_file=main_page_file,
                                     is_active=is_active)
            return {"success": True, "theme_id": theme_id, "message": "Theme created"}
        else:
            return {"success": False, "message": "Failed to create theme"}
    @app.delete("/themes/{theme_id}")
    async def delete_theme(theme_id: int, admin: dict = Depends(get_current_admin)):
        """Delete a theme; the answer says why when it cannot be."""
        refusal = config_service.deletion_refusal(theme_id)
        if refusal:
            return {"success": False, "message": refusal}
        success = config_service.delete_theme(theme_id)

        if success:
            await admin_trail.record(admin_trail.THEMES_CHANGED, admin,
                                     f"Theme {theme_id} deleted",
                                     action="delete", theme_id=theme_id)
            return {"success": True, "message": f"Theme {theme_id} deleted"}
        else:
            return {"success": False, "message": f"Failed to delete theme {theme_id}"}


#: The one instance the application and the framework share.
config_service = ConfigService()
