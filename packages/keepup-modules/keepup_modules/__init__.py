"""The catalogue of panel sections, and the decision about backend plugins.

This distribution provides `catalogue` and `plugin_decisions` and declares one
plugin, `modules`, in the `keepup.plugins` group. It depends on `keepup-admin`
for the constructor, on `keepup-db` for the two tables the catalogue keeps, on
`keepup-users` for the roles a section is granted to, on `keepup-auth` for the
panel guard of its routes -- the temporary edge the route bridge carries until
`keepup-http` mounts what a plugin declares (keepup-121) -- and on `keepup-audit`
for the trail an administrator's change is written to.
"""

from keepup_modules.catalogue import (
    get_all_modules_from_db,
    get_module_by_id,
    get_modules_for_role,
    get_modules_for_roles,
    sync_framework_sections,
    sync_new_modules_from_json,
)

__version__ = "0.7.0"

__all__ = [
    "get_all_modules_from_db",
    "get_module_by_id",
    "get_modules_for_role",
    "get_modules_for_roles",
    "sync_framework_sections",
    "sync_new_modules_from_json",
]

#: What the entry point asks for, looked up on access: importing the catalogue
#: model must not import the plugin's panel wiring.
_PLUGIN_NAMES = {"ModulesPlugin", "SERVICE_CATALOGUE", "SERVICE_PLUGIN_DECISIONS"}


def __getattr__(name):
    if name in _PLUGIN_NAMES:
        from keepup_modules import plugin

        return getattr(plugin, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
