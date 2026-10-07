"""A small application built on the framework, and nothing else.

It lives in the package's own tests and travels nowhere: the wheel names the
packages it installs and ``keepup.tests`` is not one of them. What it is for is
the checks that need a consumer -- which names an application may take out of
the framework (keepup-7), and which trees a route is looked for in (keepup-26).
In the repository the package grew in, those checks run against the applications
that stand beside it; in a clone of the package there is nobody to run them
against, and they used to pass by doing nothing. This is somebody.

Written the way the guide says to write an application: settings, a plugin with
a table and a route, an identity provider for a system that already knows who
everybody is, and paths to code resolved from this file while paths to data stay
relative to the working directory. It imports only names the framework declares
public, so the checks above it fail the moment one of them is renamed without
saying so.
"""

from sqlalchemy import Column, DateTime, Integer, String

from keepup import KeepupSettings, create_app
from keepup.auth.identity import ExternalIdentity, IdentityProvider
from keepup.db import DatabaseManagerV2
from keepup.plugins.base import BasePlugin
from keepup.security import DEFAULT_CONTENT_SECURITY_POLICY
from keepup.tables import NOW, auto_id, ensure_tables, table

#: The application's own table, declared rather than written as DDL.
notes = table(
    "consumer_note",
    auto_id(),
    Column("body", String(200), nullable=False),
    Column("owner_id", Integer),
    Column("created_at", DateTime, server_default=NOW),
)


def ensure_schema():
    """What the plugin does at start-up: create what is missing."""
    ensure_tables(notes)


class NotesPlugin(BasePlugin):
    """A plugin with a route of its own, as an application ships one."""

    def __init__(self, config=None):
        super().__init__("notes", "Notes", config)

    async def initialize(self) -> bool:
        ensure_schema()
        return True

    def get_api_routes(self):
        return [
            {"path": "/api/notes", "methods": ["GET"], "handler": self.list_notes},
        ]

    async def list_notes(self, current_user: dict = None):
        rows = DatabaseManagerV2.execute("SELECT id, body FROM consumer_note", {})
        return {"notes": [dict(row) for row in rows]}


class DirectoryProvider(IdentityProvider):
    """Somebody else's identity system, in the shape a provider plugin has."""

    async def verify_token(self, token):
        return ExternalIdentity(subject=token)

    async def verify_password(self, username, password):
        raise NotImplementedError("this application signs in through the panel only")


def build_app():
    """The application, built from its settings and nothing of ours."""
    settings = KeepupSettings(
        title="Consumer",
        project_name="consumer",
        plugins_dir=None,
        plugin_manager=None,
        static_mounts=(),
        content_security_policy=DEFAULT_CONTENT_SECURITY_POLICY,
    )
    return create_app(settings)
