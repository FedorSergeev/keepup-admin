"""The pages and the health of the application itself.

Everything here is served from the working directory, not from the package:
the front end and the version file are data of the deployment, and the
application says where they are through ``configure()``. The public health
answer deliberately carries nothing about the database beyond whether it
answered -- the detailed one is administrative.
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from keepup import cluster
from keepup.api_versions import describe
from keepup.audit import incoming_requests_buffer
from keepup.auth.dependencies import get_current_admin
from keepup.db import DatabaseManager, db_config
from keepup.instance import get_instance_id, get_instance_name
from keepup.themes import config_service

logger = logging.getLogger(__name__)

#: Where the front end lives, relative to the working directory.
STATIC_DIR = "static"
CLIENT_PAGE = "main.html"
VERSION_FILE = "version.json"
FAVICON_FILE = "favicon.ico"


#: The package's own front end -- the panel shell. Resolved from this file's
#: location and never from the working directory: it is data of the package,
#: and the process may have been started anywhere.
SHELL_DIR = str(Path(__file__).resolve().parent / "static")

#: The page served when the active theme names a file that may not be served.
#: Not an error page: an administrator who broke the theme still has to be able
#: to reach the panel and fix it.
DEFAULT_PANEL_PAGE = "index_new.html"


def static_page(name):
    """Resolve a page name against the front ends, refusing to leave either.

    Two directories, in this order: the application's, then the package's. The
    application first because a theme of its own must be able to replace one of
    the package's by name; the package second because that is where the shipped
    panel lives once keepup is installed rather than sitting in the repository.

    The name reaches here from the `visual_themes` table, so it is data, not a
    constant of the code: `os.path.join(STATIC_DIR, "../config/auth.yaml")` is
    a perfectly ordinary path, and FileResponse would serve it. Containment is
    checked after resolution, so that `..`, a symbolic link and an absolute
    name are all refused by the same rule.

    Args:
        name: the file name as it was stored.

    Returns:
        The path to serve, or None when the name leads outside both directories
        or names nothing in either.
    """
    if not name:
        return None
    for root_dir in (STATIC_DIR, SHELL_DIR):
        root = os.path.realpath(root_dir)
        candidate = os.path.realpath(os.path.join(root_dir, name))
        if candidate != root and not candidate.startswith(root + os.sep):
            continue
        if os.path.isfile(candidate):
            return candidate
    return None


def theme_page():
    """The active theme's page, or the default one when it may not be served."""
    name = config_service.get_active_theme_page_file()
    path = static_page(name)
    if path is None:
        logger.error(
            "The active theme names a page that is outside the front ends or "
            "absent from both (%r); serving %s instead", name, DEFAULT_PANEL_PAGE)
        return os.path.join(SHELL_DIR, DEFAULT_PANEL_PAGE)
    return path


def configure(static_dir=None, client_page=None, version_file=None, favicon_file=None):
    """Supply the application's front-end paths."""
    global STATIC_DIR, CLIENT_PAGE, VERSION_FILE, FAVICON_FILE
    if static_dir is not None:
        STATIC_DIR = static_dir
    if client_page is not None:
        CLIENT_PAGE = client_page
    if version_file is not None:
        VERSION_FILE = version_file
    if favicon_file is not None:
        FAVICON_FILE = favicon_file



def register_web_routes(app):
    """Register the pages, the version and the health endpoints."""

    @app.get("/", response_class=FileResponse)
    async def read_root():
        return FileResponse(os.path.join(STATIC_DIR, CLIENT_PAGE))
    @app.get("/selfcare", response_class=FileResponse)
    async def read_root_selfcare():
        """Return the main page of the active visual theme."""
        file_path = theme_page()
        logger.debug(f"Serving {file_path} for selfcare page")
        return FileResponse(file_path)
    @app.get("/api/version", response_class=FileResponse)
    async def get_version():
        return FileResponse(os.path.join(STATIC_DIR, VERSION_FILE))
    @app.get("/selfcare/modules/{path:path}")
    async def serve_modules_app(path: str):
        """Serve every path under /modules to the frontend."""
        file_path = theme_page()
        logger.debug(f"Serving {file_path} for selfcare page")
        return FileResponse(file_path)
    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return FileResponse(os.path.join(STATIC_DIR, FAVICON_FILE))
    @app.get("/api/health")
    async def health_check():
        """Whether this instance is up and reaches its database -- and nothing more.

        The answer is public: the deployment proxies it to the world. It used to carry
        the database connection string, which named the host, port, database and user
        inside the stand's network -- a network deliberately kept behind the public
        proxy. Details are for an administrator, at /api/admin/health.

        A replica an administrator stopped answers 503 "stopped": that is what
        makes a load balancer take it out (keepup/cluster.py).
        """
        if cluster.current_state() == cluster.STATE_STOPPED:
            return JSONResponse(status_code=503, content={
                "status": cluster.STATE_STOPPED,
                "instance_id": get_instance_id(),
                "timestamp": datetime.utcnow().isoformat(),
            })
        try:
            db_status = DatabaseManager.test_connection()
        except Exception as e:
            db_status = {"success": False, "error": str(e)}

        if not db_status.get("success"):
            # The reason goes to the log; the caller learns that it failed, not where.
            logger.error(f"Health check failed: {db_status.get('error')}")
            raise HTTPException(status_code=503, detail="Service unavailable")

        return {
            "status": "healthy",
            # Which instance answered: how one tells the public address and the stand
            # are the same deployment (doc/stand_update.md). A container name, not an address.
            "instance_id": get_instance_id(),
            "timestamp": datetime.utcnow().isoformat(),
        }
    @app.get("/api/versions")
    async def api_versions():
        """Which versions of the API this server speaks; public, read by agents at start."""
        from keepup.api_versions import describe
        return describe()
    @app.get("/api/admin/health")
    async def admin_health_check(admin: dict = Depends(get_current_admin)):
        """The full picture for an administrator: database, its version and settings."""
        try:
            db_status = DatabaseManager.test_connection()
        except Exception as e:
            db_status = {"success": False, "error": str(e)}
        return {
            "status": "healthy" if db_status.get("success") else "unhealthy",
            "database": db_status,
            "instance_id": get_instance_id(),
            "timestamp": datetime.utcnow().isoformat(),
            "incoming_requests_buffer_size": len(incoming_requests_buffer),
        }
