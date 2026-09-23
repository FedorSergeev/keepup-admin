"""The catalogue of panel sections.

A section is a piece of the front end -- a script, a stylesheet, an init
function -- and a role is granted the sections it may see. The catalogue lives
in the database; the file the application ships is a seed and a fallback, and
new sections are added from it without touching what an administrator has
already switched off or taken away from a role.
"""

import json
from pathlib import Path
import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel

from keepup.auth.dependencies import get_current_admin, get_current_user
from keepup.db import DatabaseManager, DatabaseManagerV2, db_config

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "create_or_update_module",
    "delete_module",
    "get_all_modules_from_db",
    "get_module_by_id",
    "get_modules_for_role",
    "import_modules_from_json",
    "register_module_routes",
    "sync_framework_sections",
    "sync_new_modules_from_json",
    "update_role_modules",
]

logger = logging.getLogger(__name__)

MODULES_CONFIG_PATH = 'config/modules.json'


class ModuleCreate(BaseModel):
    """A panel section being declared."""

    module_id: str
    name: str
    description: Optional[str] = None
    js_path: Optional[str] = None
    css_path: Optional[str] = None
    init_function: Optional[str] = None
    version: str = "1.0.0"
    config: Optional[Dict[str, Any]] = None


class ModuleUpdate(BaseModel):
    """The parts of a declared section that may be changed."""

    name: Optional[str] = None
    description: Optional[str] = None
    js_path: Optional[str] = None
    css_path: Optional[str] = None
    init_function: Optional[str] = None
    version: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class RoleModulesUpdate(BaseModel):
    """Which sections a role is granted."""

    role_name: str
    module_ids: List[str]


def get_all_modules_from_db():
    """Return every module from the database."""
    return DatabaseManager.execute_sql('''
    SELECT * FROM frontend_modules 
    WHERE is_active = TRUE 
    ORDER BY name
    ''')


def get_module_by_id(module_id: str):
    """Return a module by id."""
    return DatabaseManager.execute_sql_one('''
    SELECT * FROM frontend_modules 
    WHERE module_id = ? AND is_active = TRUE
    ''', (module_id,))


def get_modules_for_role(role_name: str):
    """Return the modules granted to a role."""
    return DatabaseManager.execute_sql('''
    SELECT fm.* 
    FROM frontend_modules fm
    JOIN role_modules rm ON fm.module_id = rm.module_id
    WHERE rm.role_name = ? 
    AND rm.is_active = TRUE 
    AND fm.is_active = TRUE
    ORDER BY fm.name
    ''', (role_name,))


def create_or_update_module(module_data: Dict[str, Any]):
    """Create or update a module."""
    try:
        module_id = module_data['id']
        logger.info(f"=== Processing module: {module_id} ===")

        name = module_data['name']
        description = module_data.get('description')
        js_path = module_data.get('js')
        css_path = module_data.get('css')
        init_function = module_data.get('initFunction')
        version = module_data.get('version', '1.0.0')
        config = json.dumps(module_data.get('config', {}))

        logger.info(f"DB type: {db_config.db_type}")
        logger.info(f"Name: {name}")
        logger.info(f"JS path: {js_path}")
        logger.info(f"CSS path: {css_path}")
        logger.info(f"Init function: {init_function}")

        existing = get_module_by_id(module_id)
        logger.info(f"Module exists: {existing is not None}")

        if existing:
            logger.info(f"UPDATE path for module: {module_id}")
            if db_config.is_postgres():
                query = '''
                UPDATE frontend_modules 
                SET name = %s, description = %s, js_path = %s, css_path = %s, 
                    init_function = %s, version = %s, config = %s, updated_at = CURRENT_TIMESTAMP
                WHERE module_id = %s
                '''
            else:
                query = '''
                UPDATE frontend_modules 
                SET name = ?, description = ?, js_path = ?, css_path = ?, 
                    init_function = ?, version = ?, config = ?, updated_at = CURRENT_TIMESTAMP
                WHERE module_id = ?
                '''
            params = (name, description, js_path, css_path, init_function, version, config, module_id)
        else:
            logger.info(f"INSERT path for module: {module_id}")
            if db_config.is_postgres():
                query = '''
                INSERT INTO frontend_modules 
                (module_id, name, description, js_path, css_path, init_function, version, config)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                '''
            else:
                query = '''
                INSERT INTO frontend_modules 
                (module_id, name, description, js_path, css_path, init_function, version, config)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                '''
            params = (module_id, name, description, js_path, css_path, init_function, version, config)

        logger.info(f"Final query: {query}")
        logger.info(f"Params count: {len(params)}")
        logger.info(f"Params: {params}")

        logger.info("Testing database connection...")
        test_conn = DatabaseManager.get_connection()
        if test_conn:
            logger.info("Database connection OK")
            test_conn.close()
        else:
            logger.error("Database connection FAILED")
            return False

        logger.info("Executing SQL query...")
        start_time = time.time()

        result = DatabaseManager.execute_commit_only(query, params)

        execution_time = time.time() - start_time
        logger.info(f"Query executed in {execution_time:.2f} seconds")

        if result:
            logger.info(f"✓ Successfully {'updated' if existing else 'created'} module: {module_id}")
        else:
            logger.error(f"✗ Failed to {'update' if existing else 'create'} module: {module_id}")

    except Exception as e:
        logger.error(f"🚨 Error in create_or_update_module for {module_data.get('id', 'unknown')}: {str(e)}")
        import traceback
        logger.error(f"Stack trace: {traceback.format_exc()}")
        raise


def update_role_modules(role_name: str, module_ids: List[str]):
    """Update the modules granted to a role."""
    DatabaseManager.execute_commit_only('''
    UPDATE role_modules 
    SET is_active = FALSE 
    WHERE role_name = ?
    ''', (role_name,))

    for module_id in module_ids:
        if db_config.is_postgres():
            DatabaseManager.execute_commit_only('''
            INSERT INTO role_modules (role_name, module_id, is_active)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (role_name, module_id) 
            DO UPDATE SET is_active = TRUE
            ''', (role_name, module_id))
        else:
            DatabaseManager.execute_commit_only('''
            INSERT OR REPLACE INTO role_modules (role_name, module_id, is_active)
            VALUES (?, ?, TRUE)
            ''', (role_name, module_id))


def delete_module(module_id: str):
    """Delete a module (soft delete)."""
    DatabaseManager.execute_commit_only('''
    UPDATE frontend_modules 
    SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
    WHERE module_id = ?
    ''', (module_id,))


def import_modules_from_json():
    """Import the modules from the JSON file into the database."""
    try:
        with open(MODULES_CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)

        for module_config in config.get('modules', []):
            create_or_update_module(module_config)

        for role_config in config.get('roles', []):
            role_name = role_config.get('name')
            module_ids = role_config.get('modules', [])
            update_role_modules(role_name, module_ids)

        logger.info("Modules imported successfully from JSON")
        return True
    except Exception as e:
        logger.error(f"Error importing modules from JSON: {str(e)}")
        return False


#: The catalogue the framework ships. Resolved from this file's location, like
#: the panel shell: it is data of the package, and the process may have been
#: started anywhere.
FRAMEWORK_SECTIONS = str(Path(__file__).resolve().parent / "sections.json")


def sync_framework_sections(path: str = FRAMEWORK_SECTIONS) -> Dict[str, int]:
    """Bring the framework's own panel sections into the catalogue.

    The application does not declare them: eight sections -- users, the section
    catalogue itself, the cluster, metrics, themes, events, background tasks
    and the integration log -- belong to the framework, and an application that
    had to copy eight entries into its own file would get them wrong one at a
    time (task keepup-4).

    One rule here differs from sync_new_modules_from_json, and it is worth
    saying plainly:

        the framework owns *where the files of its sections are*;
        the administrator owns *whether they are shown and to whom*.

    So `js_path`, `css_path` and `init_function` are updated on rows that
    already exist, while `is_active` and the grants are only ever inserted when
    missing. Without the update, a deployment that has been running since
    before the move keeps `/static/modules/js/users.js` in its database, the
    file is not there any more, and the section breaks silently -- the panel
    fetches a 404 and shows nothing.

    `name` and `description` are left alone on an existing row: they are what a
    person reads in the menu, and an administrator may have changed them on
    purpose.

    Args:
        path: the catalogue to read; the package's own by default.

    Returns:
        How many rows were added and how many had their paths corrected.
    """
    counted = {"modules": 0, "repointed": 0, "grants": 0}
    try:
        with open(path, "r", encoding="utf-8") as catalogue:
            declared = json.load(catalogue)
    except Exception as error:
        logger.error("Framework panel sections not synchronised: cannot read %s: %s",
                     path, error)
        return counted

    for section in declared.get("modules", []):
        try:
            counted["modules"] += max(0, DatabaseManager.execute_commit_only(
                "INSERT INTO frontend_modules (module_id, name, description, js_path, "
                "css_path, init_function, version, config) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (module_id) DO NOTHING",
                (section["id"], section["name"], section.get("description"),
                 section.get("js"), section.get("css"), section.get("initFunction"),
                 section.get("version", "1.0.0"), json.dumps(section.get("config", {})))))

            counted["repointed"] += max(0, DatabaseManager.execute_commit_only(
                "UPDATE frontend_modules SET js_path = ?, css_path = ?, init_function = ? "
                "WHERE module_id = ? AND (js_path IS DISTINCT FROM ? "
                "OR css_path IS DISTINCT FROM ?)"
                if db_config.is_postgres() else
                "UPDATE frontend_modules SET js_path = ?, css_path = ?, init_function = ? "
                "WHERE module_id = ? AND (js_path IS NOT ? OR css_path IS NOT ?)",
                (section.get("js"), section.get("css"), section.get("initFunction"),
                 section["id"], section.get("js"), section.get("css"))))
        except Exception as error:
            logger.warning("Framework section %s not synchronised: %s",
                           section.get("id"), error)

    for role in declared.get("roles", []):
        for module_id in role.get("modules", []):
            try:
                counted["grants"] += max(0, DatabaseManager.execute_commit_only(
                    "INSERT INTO role_modules (role_name, module_id, is_active) "
                    "VALUES (?, ?, TRUE) ON CONFLICT (role_name, module_id) DO NOTHING",
                    (role.get("name"), module_id)))
            except Exception as error:
                logger.warning("Framework grant %s/%s not added: %s",
                               role.get("name"), module_id, error)

    if counted["modules"] or counted["repointed"]:
        logger.info("Framework panel sections: %s added, %s repointed",
                    counted["modules"], counted["repointed"])
    return counted


def sync_new_modules_from_json(path: str = MODULES_CONFIG_PATH) -> Dict[str, int]:
    """Add what the file declares and the database has never seen; change nothing else.

    The catalogue of panel sections lives in the database, and the file is only its
    seed. Until this ran at start-up, a section added to the file stayed invisible
    after deployment until somebody remembered to call the import by hand. The full
    import cannot run on its own: it rewrites every role's grants from the file and
    so undoes what an administrator changed in the panel.

    So only rows that do not exist at all are written: a module whose id has no row,
    a grant whose (role, module) pair has no row. A section an administrator switched
    off or took away from a role keeps its row, and stays the way he left it.
    """
    added = {"modules": 0, "grants": 0}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception as e:
        logger.warning(f"Panel sections not synchronised: cannot read {path}: {e}")
        return added

    for module in config.get('modules', []):
        try:
            added["modules"] += max(0, DatabaseManager.execute_commit_only(
                "INSERT INTO frontend_modules (module_id, name, description, js_path, css_path, "
                "init_function, version, config) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (module_id) DO NOTHING",
                (module['id'], module['name'], module.get('description'), module.get('js'),
                 module.get('css'), module.get('initFunction'), module.get('version', '1.0.0'),
                 json.dumps(module.get('config', {})))))
        except Exception as e:
            logger.warning(f"Panel section {module.get('id')} not added: {e}")

    for role in config.get('roles', []):
        for module_id in role.get('modules', []):
            try:
                added["grants"] += max(0, DatabaseManager.execute_commit_only(
                    "INSERT INTO role_modules (role_name, module_id, is_active) VALUES (?, ?, TRUE) "
                    "ON CONFLICT (role_name, module_id) DO NOTHING",
                    (role.get('name'), module_id)))
            except Exception as e:
                logger.warning(f"Panel section {module_id} not granted to {role.get('name')}: {e}")

    if added["modules"] or added["grants"]:
        logger.info(f"Panel sections from {path}: added {added['modules']} section(s) and "
                    f"{added['grants']} role grant(s) the database did not have")
    return added


async def get_modules_from_json_fallback(current_user: dict):
    """Load the modules from the JSON file when the database read fails."""
    try:
        with open(MODULES_CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)

        user_role = current_user['role']
        role_config = next(
            (role for role in config.get('roles', []) if role.get('name') == user_role),
            None
        )

        if not role_config:
            return {"modules": []}

        allowed_module_names = set(role_config.get('modules', []))
        available_modules = [
            module for module in config.get('modules', [])
            if module.get('id') in allowed_module_names
        ]

        return {"modules": available_modules}

    except Exception as e:
        logger.error(f"Error getting modules from JSON fallback: {str(e)}")
        return {"modules": []}


def register_module_routes(app):
    """Register the panel-section endpoints on the application."""

    @app.get("/api/admin/modules")
    async def get_all_modules_admin(admin: dict = Depends(get_current_admin)):
        """Return every module (administrators only)."""
        modules = get_all_modules_from_db()
        return {"modules": modules}
    @app.get("/api/admin/modules/{module_id}")
    async def get_module_admin(module_id: str, admin: dict = Depends(get_current_admin)):
        """Return a module by id (administrators only)."""
        module = get_module_by_id(module_id)
        if not module:
            raise HTTPException(status_code=404, detail="Module not found")
        return module
    @app.post("/api/admin/modules")
    async def create_module(
            module_data: ModuleCreate,
            admin: dict = Depends(get_current_admin)
    ):
        """Create a module."""
        try:
            create_or_update_module(module_data.dict())
            return {"success": True, "message": "Module created successfully"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    @app.put("/api/admin/modules/{module_id}")
    async def update_module(
            module_id: str,
            module_data: ModuleUpdate,
            admin: dict = Depends(get_current_admin)
    ):
        """Update a module."""
        existing = get_module_by_id(module_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Module not found")

        try:
            update_data = module_data.dict(exclude_unset=True)
            update_data['module_id'] = module_id
            create_or_update_module(update_data)
            return {"success": True, "message": "Module updated successfully"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    @app.delete("/api/admin/modules/{module_id}")
    async def delete_module_endpoint(
            module_id: str,
            admin: dict = Depends(get_current_admin)
    ):
        """Delete a module."""
        existing = get_module_by_id(module_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Module not found")

        delete_module(module_id)
        return {"success": True, "message": "Module deleted successfully"}
    @app.get("/api/admin/role-modules")
    async def get_role_modules(admin: dict = Depends(get_current_admin)):
        """Return the modules granted to each role."""
        roles_modules = {}
        roles = DatabaseManager.execute_sql('''
        SELECT DISTINCT role_name FROM role_modules WHERE is_active = TRUE
        ''')

        for role in roles:
            modules = get_modules_for_role(role['role_name'])
            roles_modules[role['role_name']] = modules

        return roles_modules
    @app.post("/api/admin/role-modules")
    async def update_role_modules_endpoint(
            role_data: RoleModulesUpdate,
            admin: dict = Depends(get_current_admin)
    ):
        """Update the modules granted to a role."""
        try:
            update_role_modules(role_data.role_name, role_data.module_ids)
            return {"success": True, "message": "Role modules updated successfully"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    @app.post("/api/admin/modules/import-from-json")
    async def import_modules_from_json_endpoint(admin: dict = Depends(get_current_admin)):
        """Import the modules from the JSON file."""
        success = import_modules_from_json()
        if success:
            return {"success": True, "message": "Modules imported successfully"}
        else:
            raise HTTPException(status_code=500, detail="Error importing modules")
    @app.post("/api/admin/modules/export-to-json")
    async def export_modules_to_json_endpoint(admin: dict = Depends(get_current_admin)):
        """Export the modules to the JSON file."""
        try:
            modules = get_all_modules_from_db()
            roles_modules = {}
            roles = DatabaseManager.execute_sql('SELECT DISTINCT role_name FROM role_modules WHERE is_active = TRUE')

            for role in roles:
                role_modules = get_modules_for_role(role['role_name'])
                roles_modules[role['role_name']] = [module['module_id'] for module in role_modules]

            export_data = {
                "roles": [
                    {
                        "name": role_name,
                        "modules": module_ids
                    }
                    for role_name, module_ids in roles_modules.items()
                ],
                "modules": [
                    {
                        "id": module['module_id'],
                        "name": module['name'],
                        "description": module['description'],
                        "js": module['js_path'],
                        "css": module['css_path'],
                        "initFunction": module['init_function'],
                        "version": module['version'],
                        "config": json.loads(module['config']) if module['config'] else {}
                    }
                    for module in modules
                ]
            }

            with open(MODULES_CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)

            return {"success": True, "message": "Modules exported successfully"}
        except Exception as e:
            logger.error(f"Error exporting modules to JSON: {str(e)}")
            raise HTTPException(status_code=500, detail="Error exporting modules")
    @app.get("/api/modules")
    async def get_modules(current_user: dict = Depends(get_current_user)):
        """Return the modules available to the current user, from the database."""
        try:
            user_role = current_user['role']
            modules = get_modules_for_role(user_role)

            if modules:
                formatted_modules = []
                for module in modules:
                    formatted_module = {
                        "id": module['module_id'],
                        "name": module['name'],
                        "description": module['description'],
                        "js": module['js_path'],
                        "css": module['css_path'],
                        "initFunction": module['init_function'],
                        "version": module['version']
                    }
                    if module['config']:
                        formatted_module["config"] = json.loads(module['config'])
                    formatted_modules.append(formatted_module)

                return {"modules": formatted_modules}
            else:
                logger.warning("No modules found in DB, falling back to JSON file")
                return await get_modules_from_json_fallback(current_user)

        except Exception as e:
            logger.error(f"Error getting modules from DB: {str(e)}")
            return await get_modules_from_json_fallback(current_user)
