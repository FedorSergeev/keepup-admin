# Working on keepup: a guide for coding agents

This file is for an AI agent (or a person) asked to build an application on
keepup, write plugins for one, or change a fork of the framework. Read it whole
before writing code: most mistakes it prevents are silent -- a plugin that never
loads, a table that differs between databases, a panel section that breaks
another one's styles.

`tests/agent_guide_tests.py` checks that every module, setting and route key
named here exists. If you rename one, update this file in the same change.

## What keepup is

keepup (`pip install keepup-admin`, `import keepup`) is a framework over FastAPI
for admin panels and the services behind them. An **application** is a small
package that calls `keepup.create_app(KeepupSettings(...))` and ships:

- **plugins** -- Python classes that declare API routes, WebSocket routes and
  tables;
- **panel sections** -- JavaScript modules shown in the panel shell;
- **configuration** -- `config/modules.json` (plugins, sections, roles),
  `config/auth.yaml`, database settings.

The framework ships the panel shell (`static/`), its own sections
(`sections.json`: users, panel sections, cluster, metrics, ...), sign-in,
roles, audit, metrics, the cluster registry and the plugin runtime.

## The rules that are not negotiable

1. **The dependency points one way.** An application imports `keepup`; keepup
   never imports an application, not even inside a function. Anything the
   framework cannot know arrives through `KeepupSettings`.
2. **An application does not edit the framework.** keepup is a released
   dependency. If a change in it is the right answer, it is a separate change to
   keepup, released as a new version -- not a patch smuggled into an application.
3. **Everything a person reads is English**: section markup, the strings a route
   answers with, notifications. Code comments may be in any language the project
   keeps.
4. **Tables are declared, never written as DDL.** No hand-written
   `CREATE TABLE` or `ALTER TABLE ADD COLUMN`; see "Tables" below.
5. **Routes are data.** No FastAPI decorators in plugins; see "Plugins".
6. **Paths to code resolve from the package, paths to data from the working
   directory.** Compute a plugins directory from `__file__`; read
   `config/modules.json` relative to the process's working directory, which is
   the application's root.
7. **A test goes with every change**, named `*_tests.py` and run by path.

## Assembling an application

```python
from keepup import KeepupSettings, create_app

settings = KeepupSettings(
    title="My panel",
    project_name="my-panel",
    plugins_dir=PLUGINS_DIR,          # absolute, from this package's location
    plugins_config_path="config/modules.json",
    app_tables=create_my_tables,      # the application's own tables, if any
)
app = create_app(settings)
```

Settings worth knowing: `cors_origins`, `security_headers`,
`content_security_policy`, `csp_report_only`, `metrics_public`,
`max_upload_bytes`, `static_dir`, `static_mounts`, `client_page` (the page at
`/`), `built_in_themes`, `gated_pages`, `audit_redaction`, `password_rule`,
`oidc`, `identity_provider`, `openapi_url`, `openapi_public`, `public_config`, `notification_channel`, `on_startup`, `on_shutdown`,
`extra_setup`, `remote_log_url`, `profile`, `disable_http_server`, and `performance` --
the database pool, the audit buffer and the metrics interval in one object. Each is documented in
`keepup/settings.py`; a value you would have to edit inside keepup belongs in a
setting instead.

## Plugins

A plugin is a module `<plugin_id>.py` in the plugins directory with one class:

```python
from keepup.plugins.base import BasePlugin

class ReportsPlugin(BasePlugin):          # plugin_id "reports"
    def __init__(self, config):
        super().__init__("reports", "Reports", config)

    async def initialize(self) -> bool:   # create tables, warm caches; False = failed
        ensure_schema()
        return True

    def get_handlers(self):               # named callables for other plugins
        return {}

    def get_api_routes(self):
        return [
            {"path": "/api/reports", "methods": ["GET"], "handler": self.list},
            {"path": "/api/reports", "methods": ["POST"], "handler": self.create},
            {"path": "/api/public/reports/count", "methods": ["GET"],
             "handler": self.count, "require_auth": False},
        ]

    async def list(self, current_user: dict = None, status: str = None): ...
    async def create(self, request: dict = None, current_user: dict = None): ...
```

- **The class name is derived**: `f"{plugin_id.capitalize()}Plugin"`. The id
  `integration_logs` needs `Integration_logsPlugin`. A wrong name does not raise:
  the plugin is logged as not found and never loads.
- **Declared in `config/modules.json`** under `plugins[]`: `id`, `name`,
  `enabled`, `priority` (lower first), `config`. A plugin runs if its `enabled`
  is true or `PLUGINS_ENABLE` names it; `PLUGINS_DISABLE` wins over both. A
  role's `plugins` list is visibility, not enablement. `GET /api/admin/plugins`
  shows each plugin's outcome and the reason it failed.
- **Route keys**: `path`, `methods`, `handler`, `require_auth` (default true),
  `include_in_schema`, `is_upload`, `raw_request`, `max_body_bytes` (the
  largest body the route accepts, or None for none; a route that reads its own
  body -- `is_upload`, `raw_request` -- should say, since the application's
  general limit does not reach it), and `params` -- the request
  mask (`keepup/plugins/route_mask.py`: per parameter a type, `required`,
  `in`, `choices`, `min`, `max`, `max_length`, `pattern`), and `permission` --
  the right a caller must hold, checked before the handler
  (`keepup/auth/identity/access.py`; see "Somebody else's identity system"),
  `audit` (default true; a route that says false is not written to the
  incoming-request audit -- a scrape every fifteen seconds is not an audit), and
  `response_media_type` (what a non-JSON answer is; a handler may also return a
  `Response` itself).
- **Handlers take plain arguments**, not FastAPI objects: path and query values
  by name, `current_user` when signed in, and `request` -- the JSON body -- for
  POST, PUT and PATCH. Raise `fastapi.HTTPException` to refuse.
- **WebSocket routes**: `get_websocket_routes()` returns `{"path", "handler"}`
  and `require_auth: True` to have the framework sign the socket in.
- **Plugins call each other** through `plugin_manager.get_plugin(id).get_handlers()`,
  never by importing each other's modules.
- `post_construct()` runs once the server is up, for self-checks that need it.

## The kernel

`keepup.kernel` is the constructor a plugin is loaded by: a **descriptor** a
plugin declares as a class attribute (id, kind, its `requires` and `wants`, what
it `provides`, what it contributes), the **catalogue** it is declared in -- the
framework's `plugins/builtin.json`, the application's `plugins_config_path` file
and a **profile** named in `KeepupSettings.profile` -- the **service registry**
one plugin publishes into and another requires from, and the **lifecycle** that
resolves and runs them in two phases. A plugin of kind `required` or `transport`
is enabled by being installed; an `optional` one is enabled by the file, by
`PLUGINS_ENABLE` or by an administrator, and a required one cannot be switched
off at all. `keepup.kernel.create_runtime(settings)` builds one. The contract is
`doc/plugin_constructor.md`; the service names a plugin may use are
`doc/service-catalogue.md`. 0.3.0 plugins need no descriptor and keep working.

## Tables

One declaration serves SQLite and PostgreSQL (`keepup/tables.py`):

```python
from sqlalchemy import Column, DateTime, Integer, String, Index
from keepup import tables

reports = tables.table(
    "report",
    tables.auto_id(),
    Column("title", String(200), nullable=False),
    Column("owner_id", Integer),
    Column("created_at", DateTime, server_default=tables.NOW),
    Index("idx_report_owner", "owner_id"),
)

def ensure_schema():
    tables.ensure_tables(reports)
```

- `ensure_tables` creates what is missing and adds declared columns an existing
  table lacks: a new column is just a new `Column`. There are no migrations.
- Dialect differences go into the declaration: `with_variant`,
  `tables.per_dialect(postgres=..., sqlite=...)`, `ddl_if`.
- Foreign keys: `tables.foreign_key(...)`. A column on a table another module
  owns: `tables.ensure_columns(...)` -- never a second declaration of the table.
- Queries: `keepup.db.DatabaseManagerV2` with **named** parameters
  (`:id`) and `get_session()` for transactions; `raw_connection()` when code
  needs a driver cursor. There is no other way in: the legacy
  `DatabaseManager` was removed in 0.2.0.

## Panel sections and themes

A section is a JavaScript file plus an entry in `config/modules.json`
`modules[]`: `id`, `name`, `js`, `css`, `initFunction`, `handlerFunction`,
`routePath`, `sectionId`, `config`, `adminOnly`; and the roles that may see it.
At start-up the framework copies sections and grants that the database lacks
(`keepup/modules.py`), and never overwrites what an administrator changed.

- **The theme owns the chrome**: sidebar, navigation, header. A section styles
  only what is inside its own section element. Never style `#sidebar`,
  `.nav-item`, `.main-content` from a section.
- **A section puts no JavaScript in its markup.** The panel is served with a
  Content-Security-Policy (`keepup/security.py`) that refuses an inline handler,
  so a section registers what its buttons do with
  `KeepupActions.register({'users.edit': (element, event) => ...})` and names
  the action in the markup -- `data-action="users.edit"`, with the values the
  handler needs in `data-*` attributes. Inline handlers in an application's own
  sections keep working only while its policy is sent as a report
  (`csp_report_only`).
- **The narrow layout is the `is-narrow` class**, not a media query. Tables opt
  into the shared helper with `class="responsive-table"`; grids are
  `grid-cols-1 md:grid-cols-N`.
- **The header corner** shows whether the user is an administrator unless the
  application puts its own badge there: `window.AppHeader.setBadge({text, title,
  tone})`, `window.AppHeader.clearBadge()`.
- Build section markup with text, not with values spliced into HTML: escape
  anything that came from data.

## Roles

A user holds a **set** of roles (`user_roles`, `keepup/auth/user_roles.py`), and
the sections and plugins of every role in it are glued together — each one once.
A role comes into being as a grant in the section catalogue; the framework's own
two are `ADMIN` and `CLIENT`.

- **Ask with `has_role(user, ROLE_ADMIN)`**, never `user["role"] == ROLE_ADMIN`:
  `users.role` is a deprecated mirror of the set (`ADMIN` when held, otherwise
  the first role granted) and goes away in 0.3.0. In the panel it is
  `window.AppRoles.has(user, role)`.
- **Nobody holds no role.** An empty set is refused on write and read as the one
  role the mirror names; access is closed by blocking the account.
- `keepup.modules.get_modules_for_roles()` is the read for a user;
  `get_modules_for_role()` is still there for one role.

## Somebody else's identity system

When the application is installed inside a larger system that already knows
who everybody is and what they may do, a **provider plugin** puts that system
behind keepup. Subclass `keepup.auth.identity.IdentityProvider` and override
what the system can answer:

```python
from keepup.auth.identity import ExternalIdentity, IdentityProvider, IdentityRejected

class CorpProvider(IdentityProvider):
    async def verify_token(self, token):              # the system's tokens
        ...                                           # -> ExternalIdentity(subject=...)
    async def verify_password(self, username, password):  # its passwords, for the panel
        ...
    async def decide(self, identity, user, request):  # its rights -> True / False
        ...
```

- **Two ways to say no**: raise `IdentityRejected` when the system answered no,
  `ProviderUnavailable` when it did not answer (a 503, not a 401). Anything else
  raised counts as unavailable.
- **Named by the deployment**, not by the application: an `identity_provider`
  section in the authentication file (`AUTH_CONFIG_PATH`) with `name`,
  `plugin` (`module:Class` or an entry point of the group
  "keepup.identity_providers"), `settings` (`${ENV}` substituted) and the modes. A mistake there
  stops the start. `KeepupSettings.identity_provider` does the same from code;
  both at once stop the start.
- **Accounts stay here**: somebody the provider vouches for is matched to an
  account by (provider name, subject), and their roles follow `role_mapping`
  on every request. A route receives them as usual, with `authenticated_by`
  and `identity` added.
- **A right** is a route's `permission` key or `Depends(require_permission("x"))`.
  Who decides is the deployment's `authorization`: `local` (ADMIN, a
  `user_permissions` row, or the identity's own permissions) or `provider`.

See `doc/external_identity_provider.md` in the repository this package comes from.

## Several replicas

The framework is built to run as several processes over one database:
`keepup.locks` (`distributed_lock`, `with_distributed_lock`) serialises jobs,
`keepup.cluster` registers replicas and carries commands to them,
`keepup.notification_bus` passes messages between replicas, and
`keepup.scheduler` holds the one scheduler. Anything a plugin keeps in process
memory is per replica: use the database or the bus for what all replicas must see.
There is no leader election: a scheduled job that must run once takes a lock.

## Checking your work

```bash
python .github/scripts/dependency_graph.py  # who depends on whom, and what installs what
python -m pytest tests/ -q                 # the framework's own suite
python -m pytest path/to/your_tests.py -v  # an application's test, by path
tests/security_audit_tests/run_security_audit.sh  # the security audit, with a report
```

The security audit builds applications on this tree -- with and without
plugins, OIDC, an identity provider, stripped, behind TLS -- and knocks on
every route they register. A route that answers without a sign-in must be in
`PUBLIC_ROUTES` of `tests/security_audit_tests/route_sweep_tests.py`, with the reason;
adding one there is a decision, not a fix for a red check.

Tests are named `*_tests.py`; a test that needs the browser runs the JavaScript under `node` against a stand-in document -- see `tests/header_badge_tests.py`.

## Glossary

- **Application** -- a package built on keepup: its plugins, sections and config.
- **Plugin** -- a `BasePlugin` subclass loaded from the plugins directory.
- **Route** -- a dictionary a plugin returns; the framework turns it into an endpoint.
- **Request mask** -- a route's `params`: what it accepts, checked before the handler.
- **Section** -- a panel page: a JS module, its catalogue entry and role grants.
- **Shell** -- the panel page and `main_new.js` the framework ships.
- **Theme** -- a shell page and its stylesheet; owns the chrome and the tokens.
- **Catalogue** -- the database's copy of sections and grants, seeded from `modules.json`.
- **Replica** -- one process of the application; several share one database.
- **Stand** -- a deployed environment of an application.
