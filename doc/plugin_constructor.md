# The plugin constructor

**Status:** specification for keepup 0.4.0. Proposed; reviewed with the release
plan in `ci/keepup/backlog.json` (task keepup-100). The companion document
[capabilities-out-of-the-kernel.md](capabilities-out-of-the-kernel.md) carries
the per-capability extraction cards.

**Where this document lives.** It is the first document under `doc/` in the
framework's own repository. Until now every `doc/*.md` a keepup task named lived
in the applications repository (`ecom_admin/doc/`), because the framework was a
submodule of it; the framework's own repository carried no `doc/` at all. From
0.4.0 the documents that describe the framework travel with the framework, and
the `doc` field of a `keepup` task names a path in this repository. The older
documents stay where they are until they are moved, one at a time, by the task
that touches their subject.

**The claim this document makes concrete:** under 0.4.0 an application is a set
of plugins over a kernel that knows nothing about the application and nothing
about the capabilities it ships. The same constructor gives a headless
microservice, a full admin panel, or a process that serves no HTTP at all --
because users, the database, the panel and the rest are plugins, exactly the way
an application's own plugin already is.

---

## 1. Why, and what has to change for it

Under 0.3.0 the framework *is* its capability set. `create_app()`
(`keepup/factory.py:488`) imports twenty-eight modules, mounts seven static
directories plus its own shell, adds six middlewares, registers twelve families
of routes itself -- the panel page, the module catalogue, OIDC, metrics, locks,
the scheduler, the cluster registry, themes, sign-in, the API documentation and
the plugin admin -- and starts ten background tasks plus a scheduler and a
cluster controller from its lifespan (`factory.py:238-368`). A plugin may add
routes, sockets, tables and panel sections; it may not add a capability, replace
one, or be absent in a way the kernel has to cope with.

The consequences are three:

1. **A deployment cannot be smaller than the framework.** An application that
   wants an HTTP API over one table still gets the panel, the audit, the
   metrics collector, the cluster registry and the scheduler's routes, and
   carries their tables, their admin surface and their attack surface. The one
   exception is `disable_http_server` (`keepup/factory.py:501`), and it proves
   the point: it returns *a different application* -- a metrics endpoint and a
   raw request log, without a lifespan at all, so no plugin is loaded, no
   scheduler runs, no replica registers and the signing key is not even
   demanded (`factory.py:439-485`). It is the existing precedent for "an
   application without the full HTTP surface", and it breaks exactly what
   0.4.0 wants to keep.
2. **A deployment cannot be other than HTTP.** A server that must listen on
   gRPC only has nowhere to stand: plugins contribute HTTP routes and
   WebSockets, and every lifecycle hook is reached through an ASGI lifespan.
3. **A capability cannot be released on its own.** `keepup.users`, the panel
   section, the audit table and the metrics collector share one version number,
   one issue tracker and one repository, so a fix to one of them is a release of
   all of them -- and an application pinned to the framework cannot take the fix
   without taking everything else.

0.4.0 answers all three with one change: the kernel keeps a settings object, a
plugin lifecycle, a service registry and the transport seam; everything else
arrives as a plugin.

## 2. What exists today

The starting point, so that nothing below is a claim about code that was not
read.

| Piece | Where | What it does today |
|---|---|---|
| `create_app(settings)` | `keepup/factory.py:488` | the whole assembly: settings, identity provider, FastAPI, mounts, middleware, twelve `register_*` calls |
| `_build_lifespan` | `keepup/factory.py:238` | starts six background tasks, the event manager, the notification bus, `on_startup`, the scheduler, the cluster controller; and stops them in reverse |
| `_create_stripped_app` | `keepup/factory.py:439` | the "no HTTP server" application: `/metrics` and a raw-request log |
| `KeepupSettings` | `keepup/settings.py:109` | 40 fields: identity, CORS, CSP, upload limits, plugin manager, front end, themes, logging, tables, OIDC, identity provider, policies, lifecycle, scheduling, `disable_http_server` |
| `BasePlugin` | `keepup/plugins/base.py:39` | `initialize()`, `post_construct()`, `get_api_routes()`, `get_websocket_routes()`, `get_handlers()`, `cleanup()`; a class name derived as `f"{plugin_id.capitalize()}Plugin"` |
| `PluginManager` | `keepup/plugins/base.py:106` | loads a module per plugin id from `plugins_dir`, records one outcome per declared plugin, initialises in priority order |
| declaration and enablement | `keepup/plugins/registry.py:68`, `keepup/plugins/enablement.py:80` | `config/modules.json` (`plugins[]` with `id`, `name`, `enabled`, `priority`, `config`), an administrator's override in the database, `PLUGINS_ENABLE` / `PLUGINS_DISABLE` |
| route data | `keepup/plugins/routes.py` | a route dict becomes a FastAPI endpoint, with the request mask, the body limit, the sign-in check and the audit |
| panel catalogue | `keepup/modules.py`, `keepup/sections.json` | eight built-in sections and their role grants are seeded into the database at start-up |
| shell | `keepup/static/index_new.html`, `keepup/static/js/main_new.js`, `keepup/static/css/main_new.css` | the panel page, navigation built at `main_new.js:1027`, chrome CSS |
| what an application imports | measured over the applications that stand beside this package | `keepup.tables` (the most used), `keepup.db.DatabaseManagerV2`, `keepup.integrations.log_external_request`, `keepup.locks`, `keepup.plugins.base`, `keepup.auth.*`, `keepup.themes`, `keepup.metrics*`, `keepup.schema.init_db`, `keepup.notification_bus`, `keepup.events.emit_event`, `keepup.instance` |

The last row is the compatibility surface: any capability that leaves the kernel
still has to answer to those names for at least one release.

## 3. The kernel after 0.4.0

The kernel is a new internal package, `keepup/kernel/`, and it is deliberately
small enough to read in one sitting:

| Module | Responsibility |
|---|---|
| `kernel/runtime.py` | `create_runtime(settings)` -- the constructor: catalogue, loader, services, lifecycle, transports |
| `kernel/descriptor.py` | `PluginDescriptor` -- who a plugin is, what it needs, what it provides, what it contributes |
| `kernel/catalogue.py` | composing the declaration: built-in defaults, the application's file, the environment |
| `kernel/loader.py` | finding and loading a plugin: entry point, plugins directory, outcome recording |
| `kernel/services.py` | the service registry: `provide`/`require`, versions, resolution order |
| `kernel/contributions.py` | collecting what plugins contribute and handing it to the registrars |
| `kernel/lifecycle.py` | the ordered start-up and shutdown of one runtime |
| `kernel/transports.py` | the transport contract, and the runtime's list of servers |

What stays in the kernel on purpose:

- **the settings object** (`keepup/settings.py`) -- it is the application's half
  of the contract and the one thing that must not be re-exported from a plugin;
- **the service contracts** (`keepup/kernel/contracts.py`) -- the names and the
  shapes of what plugins provide, so that the kernel can require a datasource
  without depending on one (section 4.9);
- **`keepup/security.py`**, the default CSP string, because it is a value a
  settings default carries;
- **the plugin contract itself** (`keepup/plugins/base.py` and its
  `__all__`) -- unchanged, because applications subclass it today;
- **the public entry points** `keepup.create_app`, `keepup.KeepupSettings`,
  `keepup.StaticMount` (`keepup/__init__.py:29`), the first two as lazy shims
  into `keepup-http` and `keepup-db` respectively.

What leaves the kernel and why:

- every `register_*` call in `factory.py:592-607` becomes a contribution of the
  plugin that owns the capability;
- the middleware stack (`factory.py:545-590`) belongs to the HTTP transport,
  not to the kernel: CORS, headers, body limit and versioning mean nothing to a
  runtime that serves no HTTP;
- the six background tasks in the lifespan belong to the plugins that own them
  (metrics retention to metrics, the audit buffer to audit, the event retention
  to events, the socket sweep to auth, the cluster controller to the cluster
  plugin);
- `sections.json` and the shell mount (`factory.py:534`) belong to the UI and
  management plugins;
- **the library-backed pieces**: `db.py`, `schema.py`, `tables.py` and
  `positional_sql.py` go to `keepup-db` (SQLAlchemy), the routes and the ASGI
  assembly go to `keepup-http` (FastAPI, Starlette), and `sections.json` to the
  UI -- which is what section 4.9 means by a base bundle with no libraries.

`create_app(settings)` remains, and remains what an application calls. It
becomes a lazy shim: it imports `keepup-http`, builds the runtime, and returns
the transport's ASGI application -- which is what it already promises to every
caller (`keepup/factory.py:488`, `tests/consumer/application.py:83`). A
deployment that installs no HTTP transport uses `create_runtime(settings)`
instead and never loads an ASGI framework at all.

### 3.1 The order of the start-up

The order is part of the contract, because two of the steps exist for reasons
that are invisible in code:

1. `apply_settings` -- the application's values reach the parts of the kernel
   that hold configuration.
2. **resolve the required set** and load it -- built-in catalogue, the
   application's file, the environment. The administrator's overrides are not
   readable yet, because they live in the database (section 4.8).
3. **register services** -- every plugin publishes what it provides. Cheap,
   synchronous, no I/O, order-independent.
4. `on_startup` -- the application's own hook, before any plugin initialises.
5. **initialise in requirement order** -- a plugin may now use the services it
   required; a plugin whose requirement was never provided is not initialised
   and its dependents are not either.
6. **create tables** through the data source service, and import the section
   catalogue (section 9.1).
7. **resolve the optional set** with the administrator's overrides, then load,
   register, initialise and mount it.
8. mount contributions -- routes, sockets, sections, jobs. Nothing registered
   here can be unregistered, which is why the catalogue snapshot is the truth
   about what runs (`keepup/plugins/enablement.py:149`); this is the freeze.
9. `post_construct` -- self-checks that need a live server, on one replica or
   all, as today (`keepup/plugins/registry.py:155`).
10. serve -- each enabled transport starts its server.
11. shutdown -- the reverse: transports, `cleanup()` per plugin in reverse
    order, `on_shutdown`.

Section 4.8 states the same order with what each phase may read; section 4.7
says which kind of plugin may refuse to come up. Step 3 existing as its own
phase is the structural addition that lets plugin A use plugin B's connection
pool without A and B being ordered by number by hand.

### 3.2 Per-runtime state, not process state

Five modules hold their configuration in module-level globals that
`create_app` overwrites: `web.configure` (`web.py:104`), `modules.configure`
(`modules.py:47`), `plugins/admin.configure` (`plugins/admin.py:51`),
`auth/routes.configure` (`auth/routes.py:296`) and
`auth/dependencies.configure_panel_gate` (`dependencies.py:521`). Two
applications built in one process therefore share the second one's values,
which is a trap the tests already know about (`tests/conftest.py` returns the
module path between applications). A constructor that is told everything by
settings has to hold that everything per runtime: in 0.4.0 these become fields
of the runtime and are passed to the plugin that needs them, and a plugin
reaches its own configuration through `self.config`, never through a module
global. Where that is expensive, the contract is stated instead of implied:
one application per process, enforced by a check rather than by convention.

### 3.3 Two seams that do not exist today

`KeepupSettings.plugins_dir` (`settings.py:170`) is declared and never read:
the directory lives in the `PluginManager` the application builds
(`plugins/base.py:109`). In 0.4.0 it is the kernel that builds the manager, and
the field is what names the directory -- or the field goes.

`app_tables`, `extra_setup` and `plugins_config_path` are declared in settings
and read by `schema.init_db`, which the application calls itself with its own
arguments (`schema.py:245`); `create_app` never reads them. The framework
therefore documents settings it does not honour, and a deployment that passes
one set of values to `init_db` and another to `create_app` gets a database that
disagrees with the application. In 0.4.0 the runtime reads them, and `init_db`
becomes a step of the runtime rather than a call the application has to
remember.

### 3.4 The failure policy is not honest yet

`initialize_plugins` wraps loading, initialisation and route registration in one
`try/except Exception` (`plugins/registry.py:76`, `121-122`). A malformed route
declaration raises `ValueError` inside `register_plugin_routes`
(`plugins/routes.py:408-429`), which that handler swallows: the plugin's HTTP
surface is registered halfway and the only trace is a log line. 0.4.0 separated
the phases, so each failure is attributable -- a plugin, its descriptor, its
configuration or one route of it -- and each appears in the outcome report
rather than in a message that names nothing.

## 4. The plugin contract

### 4.1 A plugin is a class and a descriptor

A plugin is still a Python module with one class named
`f"{plugin_id.capitalize()}Plugin"`, loaded from the application's plugin
directory -- that is how every application plugin is written today
(`tests/consumer/application.py:43`). What a 0.4.0 plugin gains is a
**descriptor**: a declarative answer to "who are you, what do you need, what do
you provide, what do you contribute", which the kernel needs before it may
call anything.

```python
@dataclass(frozen=True)
class PluginDescriptor:
    """Who a plugin is, before any of its code runs."""

    id: str                  # unique; the file name for a directory plugin
    name: str                # what the panel shows
    version: str             # the plugin's own version, not the framework's
    distribution: str        # the distribution it travels in, for `pip install`
    priority: int = 0        # lower initialises first; ties keep declaration order
    requires: tuple[str, ...] = ()          # hard: without it the plugin does not run
    wants: tuple[str, ...] = ()             # soft: it runs, and it is worse; the report says so
    provides: tuple[str, ...] = ()          # service names this plugin publishes
    contributions: tuple[str, ...] = ()     # kinds it contributes: routes, sections, tables, jobs, transport, ...
    default_enabled: bool = False           # what an absent `enabled` flag means
    config_schema: dict = field(default_factory=dict)   # JSON-schema-shaped, for validation
```

The first six fields are what the catalogue, the enablement rules and the
service resolver read; the last three are what an administrator sees and what
the validation of `modules.json` uses. A descriptor is a class attribute, not a
constructor argument, so the kernel can read it without instantiating a plugin
whose dependencies are absent:

```python
class UsersPlugin(BasePlugin):
    descriptor = PluginDescriptor(
        id="users", name="Users", version="1.0.0",
        distribution="keepup-users",
        provides=("users>=1",), requires=("datasource>=1", "auth>=1"),
        wants=("events>=1",),          # the admin trail is nicer with an event log
        contributions=("routes", "sections", "tables"),
        default_enabled=True,
    )
```

**`requires` and `wants` are different promises, and the difference is
performance.** A hard requirement is a contract: without it the plugin must not
run, because running would mean an audit that does not record or a panel that
shows a replica list that is not true. A soft one is a preference: the plugin
works, less well, and the plugin report carries a `degraded` mark naming what
was missing. Without the distinction every capability ends up requiring every
other one -- which is how a constructor turns back into a monolith -- or
silently degrading, which is how a panel starts lying.

A plugin without a descriptor is a 0.3.0 plugin and is accepted: the kernel
derives an id from the file name and treats it as `requires=()`,
`provides=()`, `default_enabled=False`. That is the compatibility rule of
section 8 -- not a separate loader.

### 4.2 The lifecycle

| Call | When | May it fail the start? |
|---|---|---|
| `register(registry)` | phase 4, synchronous | no: a bad service name is logged and the plugin does not load |
| `initialize()` | phase 6, awaited, in priority order | no: a false or an exception is an outcome, the rest still come up (`plugins/base.py:172`) |
| `post_construct()` | phase 8, after the server is up | no: run in its own thread with a timeout (`plugins/registry.py:130`) |
| `cleanup()` | shutdown | no: exceptions are logged, the next plugin is still cleaned up |

`get_api_routes()`, `get_websocket_routes()` and `get_handlers()` keep their
signatures and their meaning. A plugin that returns routes but does not declare
`contributions=("routes",)` is not refused -- the declaration is for the
administrator and the validation, not a second gate.

### 4.3 The contribution points

The list is closed in 0.4.0 and each point has exactly one consumer in the
kernel, so a new kind of contribution is a deliberate change rather than a new
convention:

| Kind | What the plugin returns | Consumed by | Guard |
|---|---|---|---|
| `routes` | `get_api_routes()` | the HTTP transport | `plugins/routes.py` |
| `sockets` | `get_websocket_routes()` | the HTTP transport | `plugins/routes.py` |
| `sections` | `get_panel_sections()` -- catalogue entries, with `icon` | the UI plugin's catalogue | section 9 |
| `tables` | `ensure_schema()` (a declared table set) | the data source service | `keepup/tables.py` |
| `jobs` | `get_scheduled_jobs()` -- `(job_id, callable, trigger)` | the tasks plugin's scheduler | `scheduler.py` |
| `events` | `get_event_sinks()` -- callables for audit and event records | the audit plugin | `events.py` |
| `metrics` | `get_metric_collectors()` -- callables returning samples | the metrics plugin | `metrics.py` |
| `middleware` | `get_middleware()` -- ordered ASGI middleware | the HTTP transport | section 5.2 |
| `transport` | `TransportPlugin.serve(runtime)` | the kernel lifecycle | section 5 |
| `settings` | `get_settings_defaults()` -- the plugin's own defaults | the settings layer | section 4.6 |
| `permissions` | `get_route_permissions()` -- the `permission` names a route may ask for | `auth/identity/access.py` | existing |

Two route keys are added in 0.4.0, and both are forced by a capability that
cannot otherwise become a plugin:

- **`response_media_type`** (and the `response_class` behind it). A plugin route
  can only answer JSON today: the wrapper returns the handler's value and
  `IncomingRequestLogger.end_request` stores it as data
  (`plugins/routes.py:356-360`). Prometheus text -- what `keepup-metrics` owes
  its `/metrics` -- is not expressible, and neither is a file, a stream or a
  redirect. The HTTP adapter reads the key; a non-HTTP transport ignores it.
- **`audit: False`**. Every plugin route is wrapped in `log_api_request`
  (`plugins/routes.py:324-329`), so a Prometheus scrape every fifteen seconds
  would write a row into `incoming_requests` for every scrape of every replica.
  A route that must not be audited says so, and the reason it says so is
  recorded next to the key -- the default stays "audited", because a route that
  quietly leaves the audit is exactly what the audit exists to prevent.

Both keys are part of the closed list: they are read in one place, documented in
AGENTS.md, and checked by the request-mask and route-registration tests.

Table declarations do not go through a route at all: today the framework's own
tables are a hard-coded tuple (`schema.py:229-233`, created by one
`ensure_tables(*CORE_TABLES)` at `schema.py:269`) and a plugin creates its own in
`initialize()` (`plugins/base.py:70`, as `tests/consumer/application.py:38`
does). 0.4.0 keeps that and only makes it a declared contribution, so that the
plugin report can say which tables a plugin owns and the data source can create
them in foreign-key order.

### 4.4 Services: how plugins call each other

Today a plugin reaches another plugin through
`plugin_manager.get_plugin(id).get_handlers()`. That works for an unordered set
and fails as soon as the caller does not know what it is calling: a capability
that may be provided by PostgreSQL, by SQLite or by an application's own
backend is not one plugin id.

A **service** is a named, versioned interface:

```python
# The provider publishes the interface (one small module it owns):
class DataSource(Protocol):
    async def execute(self, statement: str, params: Mapping) -> list[Row]: ...
    def session(self) -> ContextManager[Session]: ...
    @property
    def dialect(self) -> str: ...

# The provider registers:
def register(self, services):
    services.provide("datasource", self, version="1.0",
                     interface="keepup_db.contract:DataSource")

# The consumer declares and asks:
descriptor = PluginDescriptor(..., requires=("datasource>=1",))
def initialize(self):
    self.db = self.services.require("datasource")
```

Rules, all of them checkable:

- **A service name is a noun the framework owns**, listed in
  `doc/service-catalogue.md` as it appears; an unlisted name is a change to
  this document.
- **A version is a contract, not a release.** `datasource>=1` means the
  interface of section 4.9, and a provider that breaks it bumps the major
  number.
- **`require()` is called in `initialize`**, never in `__init__`: the
  registration phase may not depend on another plugin's registration phase.
- **A missing requirement is `unsatisfied`**, not an exception: the plugin does
  not initialise, its dependents do not either, and both appear in
  `/api/admin/plugins` with the name of what was missing. A deployment where
  half the panel is gone because one plugin was switched off must say so.
- **Plugins never import each other.** The interface module is the only import,
  and the interface module holds no implementation.

**How a provider hands the instance over.** The provider chooses; the consumer
cannot tell the difference, because it only ever holds the name:

```python
services.provide("datasource", self)                        # ready now
services.provide("datasource", build_pool, lazy=True)       # built on first require()
services.provide("datasource", PoolProvider(), eager=True)  # built in register(), torn down in cleanup()
```

- **An instance** is what a plugin that owns its own life publishes: it
  instantiates itself, opens what it needs, runs its own `post_construct` and
  registers the finished object. The kernel knows nothing about it beyond the
  name, the version and the order -- which is exactly the case of "keepup itself
  never learns what is behind the name", and it is the default.
- **A lazy factory** is what a plugin publishes when building the object is
  expensive or must not happen before something else is ready: a database pool
  must not be opened before the deployment's configuration has been validated.
  `require()` calls it once, caches the result, and a second `require()`
  returns the same object.
- **An eager provider** is built during `register()` by the kernel calling the
  object's own `start()`, and stopped in `cleanup()`; it exists so that a
  provider which *is* a resource -- a pool, a socket, a subscriber -- has a
  place to be opened and closed without inventing a plugin of its own.

The kernel never names a concrete class in any of the three. That is the whole
point: replacing the data source, the audit sink or the transport is a change
in the catalogue, not a change in the kernel, and the plugin report says what
was resolved and what was not.

### 4.5 Configuration

Three layers, highest first, and no fourth:

1. `PLUGINS_ENABLE` / `PLUGINS_DISABLE` -- the deployment protects its own stand
   (`plugins/enablement.py:80`, unchanged).
2. the administrator's override, kept in the database (`plugins/admin.py`,
   unchanged: the environment wins over the panel, and an override the
   environment would overrule is refused at the point of writing).
3. the application's catalogue file, `KeepupSettings.plugins_config_path` --
   `config/modules.json`, unchanged in shape: `plugins[]` with `id`, `name`,
   `enabled`, `priority`, `config`, plus what 0.4.0 adds (`version` is checked
   against the descriptor when both are present; an unknown key is a warning,
   not a stop).

The layers are not consulted at the same time, and finding that out late is how
a framework ends up unable to start: layer 2 lives in the database, and the
database may itself be a plugin. Resolution is therefore **two-phase**, and
section 4.8 states the order. In phase one only layers 1 and 3 exist.

The **built-in catalogue** is the fourth piece of a different kind: a file the
framework ships (`keepup/plugins/builtin.json`) that declares the framework's
own plugins with their defaults. The application's file and the built-in
catalogue are *merged*, the application's entry winning field by field -- so
`{"id": "metrics", "enabled": false}` in an application's file is how a
deployment says "not here", and `{"id": "metrics", "config": {...}}` is how it
is configured, without the application having to restate a plugin's name, its
priority and its dependencies. A plugin that is declared nowhere and installed
anyway does not run: the catalogue is the list of what this deployment offers.

**Under all three layers there is a profile**: a named patch merged into the
catalogue before the layers are read (`KeepupSettings.profile`, or `profiles` in
the application's file). `{"profile": "metrics-only"}` is a deployment that
enables the HTTP transport and the metrics plugin and nothing else, whatever the
application's file declares; `disable_http_server` is the deprecated name of
that one profile (section 5). A profile is data, so it is inspectable in the
same report as everything else, and it cannot introduce a plugin that is not
installed, enable a required service twice, or switch off a required plugin --
the checks of sections 4.7 and 4.9 apply to the merged result, not to the file.

Per-plugin configuration is validated against `descriptor.config_schema` before
`initialize()`. A missing required key, a wrong type or an unknown key is
reported in the plugin's outcome, and the plugin does not run -- the same
posture as a malformed `identity_provider` section, for the same reason: a
capability configured halfway is worse than one that is absent.

### 4.6 What does not change

- `BasePlugin` and its method names, `PluginManager`, the outcome constants and
  `keepup.plugins.registry`'s `__all__` -- the published address
  (`plugins/registry.py:49`).
- Routes are data; handlers take plain arguments; the request mask, the body
  limit, `require_auth`, `permission`, `raw_request`, `is_upload` are as they
  are (`plugins/routes.py`).
- Every part of section 2's compatibility row in section 2. An application that
  changed nothing runs on 0.4.0 with `keepup-admin[panel]` installed.

### 4.7 Kinds of plugin

"Everything is a plugin" is true, and it does not make all plugins alike. The
release scope named ten capabilities and a UI; but a data source is not a
metrics collector, and treating them the same is how a framework ends up either
unable to start or unable to be switched off. Three kinds, declared in the
descriptor and enforced by the kernel:

```python
class PluginDescriptor:
    ...
    kind: str = "optional"       # "required" | "optional" | "transport"
    needs: tuple[str, ...] = ()  # kinds of service that must exist before this runs
```

| Kind | May be switched off? | Declared where | If it is missing |
|---|---|---|---|
| **required** | no | the application's `requirements` (the distribution) *and* the catalogue | the start stops, with the name of the distribution to install |
| **optional** | yes -- by the file, the environment or the administrator | the catalogue | the deployment runs without it; its routes and its section are absent |
| **transport** | yes, but at least one must be running unless the deployment is a worker on purpose | the catalogue | a process with no transport starts the lifecycle and serves nothing -- a worker replica, which is a declared deployment, not a mistake |

A plugin of kind `required` **or** `transport` is enabled by being installed:
installing `keepup-postgres` is how a deployment says "this database", and
installing `keepup-http` is how it says "serve HTTP". The application's file may
still switch either off, because the file is the composition root; the panel may
not, and neither may `PLUGINS_DISABLE` -- naming a required plugin there stops the
start, and naming a transport one stops it too, because a stand whose web server
did not come up must not look like one that did.

**Required plugins are libraries, and the comparison is exact.** `keepup-db-
postgres` is in the application's `requirements` the way `psycopg2` is; without
it there is no database, and without a database there is no panel, no section
list, no audit and no administrator's decision about plugins -- nothing to look
at. A required plugin therefore:

- **cannot be disabled by an administrator**: the panel's plugin list shows it
  with the decision locked and the reason (`plugins/admin.py`'s
  `_refuse_if_the_deployment_decides`, `:106-120`, already refuses the same way
  for a plugin the environment names);
- **cannot be disabled by the environment either**: `PLUGINS_DISABLE` naming a
  required plugin stops the start with a message that says so, because a stand
  that cannot come up for a reason nobody reads in the log is worse than one
  that refuses to come up at all;
- **satisfies the requirement closure of everything enabled**: if metrics
  requires `datasource` and the data source is a required plugin that is
  enabled, metrics is satisfiable; if a required plugin is absent, every
  dependent is `unsatisfied` *and* the start stops -- not one or the other;
- **declares a `check()`** that answers whether the deployment can work at all
  (a connection opens, a schema version is readable). Its failure is a start
  failure, and its message names the plugin.

The distinction is not a security boundary and is not a substitute for
`requires`: `required` says "this deployment is not a deployment without it",
`requires` says "this plugin is not a plugin without that service". Both are
checked, and both appear in the plugin report.

### 4.8 The bootstrap order, when the database is itself a plugin

The administrator's decision about plugins lives in a table
(`plugin_overrides`, `schema.py:121-127`), and the table lives in the database,
and the database is a plugin. Resolved naively, that is a circle; resolved the
way 0.3.0 resolves it, it is invisible -- `read_plugin_overrides()`
(`plugins/admin.py:65-78`) swallows a database error and returns nothing, so a
stand whose database is down silently forgets every administrator decision and
comes up with the file's answers.

The order in 0.4.0 is therefore explicit, and each phase says what it may read:

| # | Phase | May read | May not read |
|---|---|---|---|
| 1 | **resolve the required set** from the built-in catalogue, the application's file and the environment | files, environment | the database |
| 2 | load and `register()` the required plugins -- the data source among them | files, environment | the database |
| 3 | `initialize()` them, in requirement order; a required plugin's `check()` runs here | their own configuration | the database of *another* plugin |
| 4 | create the framework's tables and the required plugins' tables through the `datasource` service | the database | the administrator's decisions |
| 5 | **re-resolve the optional set**: the same file, now with the administrator's overrides, read through the `plugin_decisions` service | the database | -- |
| 6 | load, `register()`, `initialize()` and mount the optional plugins | the database, the services | -- |
| 7 | read the section catalogue and import it into the database (section 9.1) | the database | -- |
| 8 | start the transports | -- | -- |

Three consequences worth stating plainly:

- **A deployment with no database at all is not a deployment.** Phase 4 is
  where a missing required data source stops the start. What *is* supported is a
  deployment with no optional plugins -- and a worker with no transport.
- **`init_db()` stops being something the application must remember.** Today the
  application calls it before `create_app` and passes `app_tables`,
  `extra_setup` and `plugins_config_path` itself, while `create_app` reads none
  of them (`schema.py:245`, `factory.py:488`; specification section 3.3). In
  0.4.0 phase 4 is that call, driven by the settings and by the `tables`
  contributions; `init_db()` stays as a public function for an application that
  wants to run it itself, and running it twice is harmless because every step of
  it is idempotent (`schema.py:323-331`).
- **The plugin report shows both resolutions.** A row says not only *what* runs
  but *when it was decided*: `source: "config"` and `source: "env"` are phase 1,
  `source: "panel"` is phase 5. The existing `Resolution`/`status_report`
  (`plugins/enablement.py:80,138`) already carries the source; the release adds
  the phase, and the panel shows a plugin that is enabled but not running
  because it was decided too late for the catalogue snapshot -- the
  `pending_restart` mark does this today (`enablement.py:176`).

### 4.9 The base bundle carries no libraries

The rule the packaging follows is stronger than "the driver is a plugin": **the
base distribution, `keepup-admin`, depends on nothing but the standard
library.** Not SQLAlchemy, not FastAPI, not psycopg2. Every third-party library
arrives with the plugin whose subject it is:

| Library | Arrives with | Why not earlier |
|---|---|---|
| `sqlalchemy` | `keepup-db` | it is the declaration DSL and the pool, not the kernel |
| `psycopg2-binary` | `keepup-postgres` | a driver is one implementation of one deployment |
| `asyncpg` | `keepup-postgres` | the replica bus's PostgreSQL backend is part of the same implementation (`notification_bus.py:331`) |
| `fastapi`, `starlette`, `python-multipart` | `keepup-http` | a deployment that serves gRPC must not install an HTTP framework |
| `bcrypt`, `pyjwt[crypto]` | `keepup-auth` | passwords and tokens are a capability |
| `httpx` | `keepup-auth` (OIDC), `keepup-log-shipping` | both talk to somebody else's service |
| `psutil`, `prometheus-client` | `keepup-metrics` | |
| `apscheduler` | `keepup-tasks` | |
| `requests` | `keepup-log-shipping` | |
| `pydantic`, `pyyaml` | the plugin that validates its own configuration | |
| `bcrypt`, `psycopg2`, `prometheus_client`, `psutil` today | `pyproject.toml:54-74` | all four are installed today whether or not the deployment uses them |

**Where the libraries sit, as a graph.** The base depends on nothing; the
abstraction depends on SQLAlchemy; a driver depends on the abstraction and its
own client; a capability depends on the abstraction. What that means for the six
modules that cannot work without a database is that they are **not part of the
base bundle at all**:

| Module | Why it cannot stay | Travels to |
|---|---|---|
| `audit.py` | declares `incoming_requests`, imports SQLAlchemy | `keepup-audit` |
| `events.py` (with `events_api.py`, `admin_trail.py`) | declares `app_events`, imports SQLAlchemy | `keepup-audit` |
| `themes.py` (with the page routes of `web.py`) | declares `visual_themes`, imports SQLAlchemy | `keepup-ui` |
| `auth/panel_session.py` | declares `auth_session`, imports SQLAlchemy | `keepup-auth` |
| `auth/login_throttle.py` | declares `login_attempts`, imports SQLAlchemy | `keepup-auth` |
| `auth/user_roles.py` | imports SQLAlchemy (`text as sql_text`, `:33`) | `keepup-auth` with `keepup-users` |

The last row is the one a first reading of the tree misses: `user_roles.py` does
not declare a table, it uses the dialect's `text()`, and it is therefore just as
unable to live in a bundle without SQLAlchemy. `db.py`, `schema.py`, `tables.py`
and `positional_sql.py` travel to `keepup-db` for the same reason, and they are
the abstraction itself.

```
keepup-admin          the standard library, and nothing else
  |- keepup-db            sqlalchemy          the DSL, the pool, the dialect registry
  |    |- keepup-postgres     psycopg2, asyncpg
  |    \- keepup-sqlite       (stdlib sqlite3)
  |- keepup-audit         (audit.py, events.py, events_api.py, admin_trail.py)
  |- keepup-auth          bcrypt, pyjwt       (panel_session, login_throttle, user_roles, ...)
  |- keepup-users
  |- keepup-ui            (themes.py, web.py, the shell)
  |- keepup-http          fastapi, starlette
  |- keepup-metrics       psutil, prometheus-client
  \- ...
```

An arrow means "may be reached at run time through a service", never "is
imported by the base". A capability declares its own tables with the DSL
(`from keepup import tables`, the kernel's shim into `keepup-db`) and the data
source creates them in phase 4 of section 4.8; nothing else knows them. **No
staging arrangement is needed**: an earlier draft had the framework's tables
living in `keepup-db` for one release, and with these six modules leaving the
base there is nothing left for the abstraction to know about the framework.

An application therefore writes what it means:

```
keepup-admin
keepup-postgres
```

and gets PostgreSQL, because `keepup-postgres` depends on `keepup-db` and
`keepup-db` depends on SQLAlchemy. The application never names the abstraction:
it names the thing it wants, and the chain is the packaging system's job.

**Two layers, two services.** The abstraction must not know the driver, so the
database is two plugins and two service names:

| Service | Provided by | What it is | What it is not |
|---|---|---|---|
| `datasource` | `keepup-db` | the tables DSL, the pool and session manager, named parameters, transactions, `ensure_tables`/`ensure_columns`, the dialect registry, the creation of the declared tables in foreign-key order | it never names a driver, holds no DSN and opens nothing by itself |
| `datasource_driver` | `keepup-postgres`, `keepup-sqlite` | the connection string, the engine parameters, the dialect, the `last-insert-id`/`RETURNING` behaviour, the `PRAGMA`/catalogue question, the `LISTEN`/`NOTIFY` backend | it never creates a table and never owns a pool |

`keepup-db` declares `requires=("datasource_driver>=1",)` and its `check()`
fails when no driver registered one, so a deployment that installed the
abstraction and forgot the driver stops at start with a message naming
`keepup-postgres` -- not with a pool that exists and cannot connect.

**A required plugin is enabled by being installed.** The catalogue is still the
list of what a deployment offers, but for `required` plugins the distribution is
the declaration: a plugin found through the `keepup.plugins` entry point and of
kind `required` is enabled unless the application's file says otherwise. That is
what makes the two-line install work with no configuration file at all. In
exchange, **exactly one provider per required service may be enabled**: with
both `keepup-postgres` and `keepup-sqlite` enabled and no choice made, the start
stops and names both, because a deployment that silently picked one would hide
which database it is actually running on. The choice is one line in the
application's catalogue:

```json
{"plugins": [{"id": "sqlite", "enabled": false}]}
```

**A required service may be provided by the application, and that is a
feature.** Replacing `keepup-postgres` with a gate to a database of one's own is
the escape hatch the architecture exists for -- and it is also the answer to a
performance question 0.3.0 cannot answer at all: today every query of the
framework goes through SQLAlchemy and, for the asynchronous wrappers, through
`asyncio.to_thread` (`db.py:305-322`), a thread hop per statement. A driver that
speaks `asyncpg` natively is a legitimate reason to write one. Three rules keep
it a deployment decision instead of a hole:

- **The substitution happens before the start, in code and in the catalogue.**
  The administrator's panel decides whether an *optional* plugin runs; it never
  chooses a provider. A panel that could point the application at another
  database would turn "an administrator of the panel" into "somebody who reads
  every row of it", which is a privilege escalation dressed as configurability.
- **A provider of a required service must be an installed distribution**
  (an entry point of group `keepup.plugins`), not a file in `plugins_dir`. A
  driver is the one component that sees every row and every secret; it has to be
  a thing `pip freeze`, a lock file and an audit can name. A bare `.py` plugged
  into the plugin directory is fine for an optional capability and is refused
  for a required one, with the reason in the report.
- **Exactly one provider stays exactly one provider.** The substitution replaces
  the default provider; it does not sit beside it. Two enabled providers of
  `datasource` stop the start and name both (above).

A provider that lies is the residual risk, and it cannot be resolved by the
framework -- but it can be made visible, which is why the administrator's
decisions are written to the process log as well as into `plugin_overrides`
(section 4.8): a datasource that suppresses rows cannot suppress the record of
what was decided, because that record leaves through a different door.

**The kernel's own use of the database is not a database use.** The kernel owns
no table: `plugin_overrides` -- the administrator's decision read in phase 5 of
section 4.8 -- moves to the plugin that owns plugin management, and the kernel
asks for it through a service (`plugin_decisions`, provided by
`keepup-modules`, which requires `datasource`). That is why the kernel can be
library-free and still have a phase-5 read -- and why a deployment with no
`keepup-modules` has no administrator's decisions to read, which is a state the
report states rather than hides. Two failures are therefore distinguished, and
the distinction is the point of section 4.8:

- **no `datasource` installed at all** -- a declared deployment (a worker, a
  gRPC-only service): phases 4, 5 and 7 do not run, files and the environment
  are the only source of decisions, and the plugin report says so in a row of
  its own rather than by silence;
- **a `datasource` installed and unreachable** -- the required plugin's `check()`
  fails and the start stops. This is the case 0.3.0 hid
  (`plugins/admin.py:65-78` swallowed the error).

**The kernel owns the interfaces, the plugins own the implementations.** A
service name and its shape are the kernel's (`keepup/kernel/contracts.py`, plain
`Protocol`s and dataclasses); `keepup-db` implements them without importing
anything of the kernel beyond the name it publishes, and the consumer never
imports the provider. That is what keeps `keepup-admin` at zero dependencies
while still letting the kernel require a datasource: it requires a *name*, not a
package.

## 5. Transports: HTTP is a plugin too

A transport is a plugin that contributes a server:

```python
class TransportPlugin(BasePlugin):
    """A way into the application that is not a route."""

    async def serve(self, runtime) -> None:
        """Start serving; return when the server has stopped."""
```

0.4.0 delivers:

- **`keepup-http`** -- the HTTP transport, and the owner of everything HTTP
  about the panel and the API: the ASGI application, the middleware stack,
  the static mounts, `/metrics` exposure. It is what `create_app()` returns.
  On by default in the built-in catalogue, because every current application
  expects a web application.
- **`keepup-runtime`** (no transport) -- `create_runtime(settings)` returns the
  kernel; `runtime.start()` / `await runtime.run_forever()` run the lifecycle
  without opening a port. This is the worker replica, and it is what makes
  "microservice without UI" a deployment rather than a stripped panel.
- **a reference transport in the tests** -- a plugin that opens a TCP socket,
  speaks a three-line protocol, and answers one request through a handler
  another plugin contributed. It is not a product; it is the proof that the
  seam is real and that the kernel does not know HTTP.

A real **gRPC transport** is deliberately not in 0.4.0: it needs the seam to be
in use by something that is not HTTP first, and the reference transport is
cheaper than a dependency on `grpcio`. It is a 0.5.0 candidate and an
application may ship one in 0.4.0 without waiting for us -- which is the point
of the section.

### 5.1 The call a transport makes

A transport does not need routes; it needs to invoke a handler. Today the only
way to do that is `create_wrapper` (`plugins/routes.py:271`), which is bound to
FastAPI: it takes a `Request`, signs the caller in through
`Depends(get_panel_user)` (`routes.py:382`), reads the body from the request
(`routes.py:347`) and builds the permission check from
`request.method`/`url.path`/`path_params`
(`auth/identity/access.py:96-101`).

The route data are already transport-free -- `Mask.admit` knows nothing of
FastAPI (`plugins/route_mask.py:14-18`), and `describe_routes`
(`route_mask.py:319`) exists for a proxy -- so 0.4.0 adds one interface between
the two:

```python
@dataclass(frozen=True)
class Call:
    """One invocation of a declared route, whatever carried it."""

    route: RouteSpec          # path, methods, handler, mask, permission, auth
    params: Mapping[str, Any] # already admitted by the mask
    actor: Actor | None       # who is calling, or None
    body: Any                 # parsed body, or an open stream for is_upload
    source: str               # "http", "grpc", "cli", "test"
```

- the HTTP transport fills it from a `Request`;
- a gRPC transport fills it from metadata and the message;
- a CLI command fills it from `argv`;
- `plugins/routes.py` keeps `create_wrapper` and becomes the HTTP *adapter* of
  this interface rather than the only way in.

The permission check moves onto the `Call`, because
`access.check(user, AccessRequest(...))` already takes exactly the four fields
the call carries. `raw_request` and `is_upload` stay as they are, expressed as
`body` being the untouched request or an open stream.

That interface is what makes the reference transport of section 5 a day of work
instead of a rewrite, and it is the reason the gRPC transport can wait.

### 5.2 Middleware, and where it is contributed

Middleware is not a kernel concern: it is a stack of ASGI layers around an HTTP
application, and a runtime that serves no HTTP has none of it. Today the stack
is six layers added in a deliberate order in `create_app` (`factory.py:545-590`),
and the comments there say why each position matters -- the version middleware
is added last so it runs first and every layer below it sees the registered path
(`factory.py:583`), the body limit sits inside it so it can match a route
(`factory.py:575`), and the stopped-replica gate sits inside the version
middleware for the same reason (`factory.py:569`).

0.4.0 keeps the layering but not the ownership: **the HTTP transport owns the
stack, and a plugin contributes a layer to it** with an explicit order.

```python
def get_middleware(self) -> list[MiddlewareSpec]:
    """Layers this plugin adds around the HTTP application."""
    return [MiddlewareSpec(order=30, factory=StoppedReplicaGate)]
```

- **Ascending order, outermost first.** The transport sorts every contribution
  by `order` and installs them so that the lowest is the outermost layer -- in
  Starlette's terms, added last. A contribution with the same order as another
  keeps catalogue order, so the result never depends on dictionary iteration.
- **The stack the framework ships today, as orders**: HTTPS redirect 5, API
  versioning 10, body limit 20, the stopped-replica gate 30, the CSRF cookie
  refresh 40, security headers 50, CORS 60. The migration is therefore a
  re-statement, not a re-design: each layer moves to the plugin that owns its
  policy -- versioning, body limit, headers and CORS stay with `keepup-http`;
  the CSRF refresh goes with `keepup-auth`; the gate goes with
  `keepup-cluster`.
- **A layer that must see the registered path declares that**, and the transport
  is the one that knows how a path becomes registered; a contribution never
  reaches into routing itself.
- **With no HTTP transport there is no middleware**, which is the honest
  statement of what a gRPC-only process needs.

The order is asserted by a test rather than by a comment: the body limit must
stay inside the version middleware, and the gate must stay inside both -- the
two failures the comments of 0.3.0 were written to prevent.

`KeepupSettings.disable_http_server` has no meaning left in this architecture:
a deployment that enables no HTTP transport serves no HTTP, which is what the
flag was trying to say. Removing it outright would break the standing of every
deployment that sets it -- and, worse, would take away the one thing those
deployments exist for: a process whose only job is to answer Prometheus. So the
flag is not deleted and not silently reinterpreted; it becomes a **profile**.

A profile is a named patch merged into the catalogue before anything else reads
it (section 4.5), and 0.4.0 ships one: `metrics-only`, which enables exactly the
HTTP transport and the metrics plugin and nothing else, and turns on the
security headers the stripped application never sent at all (`factory.py:439-485`
adds no CSP, no `X-Frame-Options`, no `X-Content-Type-Options`). A deployment
that sets the old variable gets that profile, a deprecation warning naming the
three catalogue lines that replace it, and a better deployment than it had. The
name goes away in 0.5.0.

The general form is worth more than the migration: a profile is how one
codebase serves several deployments -- a dev stand with everything on, a worker
with no transport, a metrics-only process, a customer stand with one capability
switched off -- without a second configuration format. It is the one Spring
idea this design takes deliberately (section 14), because it is a merge over
data the catalogue already holds rather than a container.

## 6. The capabilities that leave the kernel

Ten capabilities leave, and the rule for each is the same: the code moves into a
plugin package, the kernel stops importing it, and an application keeps the old
import path through a shim module in the kernel that imports the plugin lazily
(section 8).

### 6.0 The contract that has to come first

One import holds the kernel together, and it is not a capability:

```python
from keepup.auth.dependencies import get_current_admin
```

Eleven modules of the kernel depend on it -- `metrics_api`, `modules`, `locks`,
`scheduler`, `cluster`, `events_api`, `themes`, `web`, `api_docs`,
`plugins/admin` and `plugins/routes` -- and `keepup.auth.dependencies` is not a
module that describes a caller; it is where the caller is *found*, with the
session cookie, the token, the panel gate and the external provider behind it.
While the subject of a request is a private import from `auth`, moving `users`
or `auth` breaks all eleven at once, and no capability can leave.

So the first thing 0.4.0 extracts is not a capability but the **contract of the
subject and the right**:

- a service, `auth`, that answers "who is calling" -- the session, the token, the
  provider -- without the caller importing the module that finds them;
- the existing `AccessRequest`/`IdentityProvider` (`auth/identity/contract.py`)
  promoted from an internal shape to the published interface, with `permissions`
  deciding and the `permissions` contribution point naming what a route may ask
  for;
- the subject carried on the `Call` of section 5.1, so that a non-HTTP transport
  hands over the same answer the HTTP adapter does;
- the eleven reverse edges removed, one by one, with the guard test that fails
  when a twelfth appears.

`locks` writes a row into `system_metrics` (`locks.py:384-389`), `cluster` pauses
and resumes the scheduler when a replica stops (`cluster.py:501-511`) and
`metrics_api` asks `cluster` for the list of replicas: three examples of the
same problem in miniature, and the reason the service registry of section 4.4 is
not decoration.

### 6.1 The ten

| # | Capability | Today | Becomes | Provides |
|---|---|---|---|---|
| 1 | user management | `auth/user_routes.py`, `auth/seed_accounts.py`, `auth/user_roles.py`, the `users` section | `keepup-users` | `users` |
| 2 | data source | `db.py` (`DatabaseManagerV2`, `db_config`), `schema.py`'s table creation, `tables.py`, `positional_sql.py` | `keepup-db` | `datasource` |
| 3 | PostgreSQL backend | the `postgresql` branch of `db.py`, `tables.py`, `locks.py`; the `LISTEN`/`NOTIFY` backend of `notification_bus.py` | `keepup-postgres` | `datasource_driver` |
| 4 | SQLite backend | the `sqlite` branch of the same three | `keepup-sqlite` | `datasource_driver` |
| 5 | background tasks | `scheduler.py`, `locks.py`, `register_scheduler_routes`, `register_lock_routes`, the `background_tasks` section | `keepup-tasks` | `scheduler`, `locks` |
| 6 | integration log | `integrations.py`, the `integration_logs` section, the integration-log tables | `keepup-integration-log` | `integration_log` |
| 7 | event audit | `audit.py`, `events.py`, `events_api.py`, `admin_trail.py`, the `event_manager` section | `keepup-audit` | `audit`, `events` |
| 8 | system metrics | `metrics.py`, `metrics_api.py`, `metrics_retention.py`, the `metrics` section | `keepup-metrics` | `metrics` |
| 9 | module management | `modules.py`, `plugins/admin.py`, the `modules_management` section, `plugin_overrides` | `keepup-modules` | `catalogue`, `plugin_decisions` |
| 10 | the UI | `static/` (shell, section JS/CSS), `web.py`, `themes.py`, the panel page, the static mounts | `keepup-ui` | `ui` |

Three of the ten are not what their name suggests, and the release plan has to
say so rather than discover it halfway:

- **"data source management" does not exist.** There is no datasource table, no
  route, no section and no occurrence of the word in the tree: there is
  `db.py`'s process-wide `DatabaseConfig` singleton (`db.py:90-224`,
  instantiated at `db.py:595`) and the pool in `DatabaseManagerV2`, whose only
  outward surfaces are `/api/admin/health` and a `get_pool_status()`
  (`db.py:490`) that exactly one caller uses, a load-test stand. This one is
  created, not moved.
- **the integration log has no read side.** `integrations.py` (223 lines)
  imports no FastAPI at all and registers nothing, while
  `static/modules/js/integration_logs.js` (794 lines) calls
  `/api/integration-logs`, `/{id}`, `/stats` and `/cleanup`, none of which
  exists in any Python file. The section is dead in 0.3.0 -- the release both
  extracts the capability and finishes it.
- **the "background tasks" section manages nothing.** The scheduler is created
  (`scheduler.py:67`, `factory.py:301`) and started, and the framework registers
  zero jobs in it: `add_job` appears nowhere outside a test. All six background
  loops are `asyncio.create_task` in the lifespan
  (`factory.py:253-279`) and are invisible to the panel, and the section's
  "restart scheduler" is a stub. The release gives the capability a registry
  that reflects what actually runs.

Two more facts that decide the order of the work:

- **`tables.py` imports `db` (`tables.py:41`) and holds the dialect registry
  that lists exactly two dialects (`tables.py:65`).** The schema DSL cannot
  move to a *driver* -- it moves to the abstraction, `keepup-db` (section
  4.9) -- and the two drivers cannot be extracted one at a time:
  they are the two halves of one branch, and the registry has to open to a third
  first. `psycopg2-binary` is a mandatory dependency today
  (`pyproject.toml:55`), taken even by a SQLite deployment, and
  `notification_bus.py:331` reaches for `asyncpg` directly -- PostgreSQL's
  specificity has already leaked outside "the database backend".
- **`handlerFunction`, `routePath` and `sectionId` live only in
  `sections.json`; the `frontend_modules` table has no such columns**
  (`schema.py:99-114`). The truth about a section is therefore split between a
  file inside the package and the database, which is why a plugin cannot
  contribute a section today. Decided: the columns are added and the file is
  imported into the database at start -- section 9.1.

Three capabilities the release scope did not name, which have to be decided
rather than left in place by accident, because they are in the same position:

| Capability | Today | Decision |
|---|---|---|
| sign-in and sessions | `auth/routes.py`, `auth/panel_session.py`, `auth/dependencies.py`, `auth/providers/`, `auth/signing_key.py` | **Decided: `keepup-auth` is a plugin** (task 116), after the subject-and-right contract of section 6.0 (task 119) removes the eleven kernel imports of `get_current_admin`. The kernel registers sign-in unconditionally today (`factory.py:605`) for a good reason -- a panel nobody can sign into is not a panel -- but that reason belongs to `keepup-ui`, which requires `auth`, not to the kernel. |
| the cluster of replicas | `cluster.py`, the `cluster` section, `CLUSTER_MEMBERS`/`CLUSTER_COMMANDS` | **Decided: `keepup-cluster` is a plugin** (task 117), and it is a plugin *over the kernel's infrastructure*, not a kernel capability: `instance` (who am I) and the notification bus (how replicas talk) stay in the kernel as abstractions, the concrete cross-replica delivery is a service the database driver provides, and the cluster plugin requires it. The replica registry, the commands, the state machine, the routes and the section move out; the stopped-replica gate becomes a `middleware` contribution to the HTTP transport. |
| log shipping to a collector | `log_shipping.py`, `logging_setup.py` | `keepup-log-shipping`. It is observability, not the integration log: the integration log records *calls the application makes outside*, shipping sends *this application's own log* to a collector. |

Every one of the ten is in 0.4.0's scope. The three rows below the line are
proposed additions to it; leaving `auth` in the kernel in particular would mean
the kernel still knows about users, passwords and OIDC.

Where the code those plugins replace lives today, how tightly it is coupled and
what it costs to move are in
[capabilities-out-of-the-kernel.md](capabilities-out-of-the-kernel.md).

## 7. Three deployments from one constructor

The three shapes the release is judged by, written the way an application
writes them.

### 7.1 A microservice without a UI

```python
# app/main.py
from keepup import KeepupSettings, create_app

settings = KeepupSettings(
    title="Pricing API",
    project_name="pricing",
    plugins_dir=str(Path(__file__).parent / "plugins"),   # paths to code, from code
    plugins_config_path="config/modules.json",            # paths to data, from the cwd
    static_mounts=(),                                     # nothing to serve
    built_in_themes=(),
    catalogue={"plugins": [                                # what this deployment is
        {"id": "http", "enabled": True},
        {"id": "postgres", "enabled": True, "config": {"dsn_env": "DATABASE_URL"}},
        {"id": "audit", "enabled": False},                 # no event log here
        {"id": "ui", "enabled": False},
        {"id": "pricing", "enabled": True},
    ]},
)
app = create_app(settings)
```

What is *not* in this process: the panel shell, the section catalogue, the
metrics collector, the scheduler, the cluster registry, the theme service, the
integration log. Not switched off -- absent, because the plugins that bring
them are not in the catalogue. What is in it: an HTTP transport, a PostgreSQL
data source, the audit (off here, on by default), and the application's own
plugin.

### 7.2 A full admin panel

```python
app = create_app(KeepupSettings(
    title="ServerShare",
    project_name="servershare",
    plugins_dir=PLUGINS_DIR,
    plugins_config_path="config/modules.json",   # the application's plugins on top
    built_in_themes=(("servershare", "theme.html", "ServerShare", "/images/logo.svg"),),
))
# and in the environment: keepup-admin[panel], which is the ten plugins.
```

The ten built-in plugins are declared by `keepup/plugins/builtin.json`, so the
application's file carries its own plugins only, and the panel it gets is the
panel it has today: users, sections, cluster, metrics, themes, events,
background tasks, the integration log.

### 7.3 A process that serves no HTTP

```python
from keepup import create_runtime, KeepupSettings

runtime = create_runtime(KeepupSettings(
    title="Billing worker",
    project_name="billing",
    catalogue={"plugins": [
        {"id": "postgres", "enabled": True},
        {"id": "tasks", "enabled": True, "config": {"jobs": [...]}},
        {"id": "audit", "enabled": True},
    ]},
))
asyncio.run(runtime.run_forever())     # the lifecycle, and nothing on a port
```

A gRPC server is this, plus a transport plugin. The kernel does not know which
one it is running, and that is the whole test of whether the change worked.

## 8. Compatibility: 0.3.0 applications on 0.4.0

The rule: **in 0.4.0 nothing an application imports today stops working.** The
capability moves; the name stays, as a shim in the kernel that imports the
plugin package lazily and says which extra to install if it is not there.

| Old name | After 0.4.0 |
|---|---|
| `keepup.KeepupSettings`, `keepup.StaticMount` | unchanged, kernel |
| `keepup.create_app` | shim -> `keepup_http` (it returns the HTTP transport's ASGI application) |
| `keepup.settings`, `keepup.security`, `keepup.plugins.base`, `keepup.plugins.registry`, `keepup.plugins.enablement`, `keepup.plugins.route_mask` | unchanged, kernel |
| `keepup.tables` | shim -> `keepup_db.tables`; the DSL is SQLAlchemy, and SQLAlchemy now arrives with `keepup-db` |
| `keepup.db.DatabaseManagerV2`, `keepup.db.db_config` | shim -> `keepup_db` |
| `keepup.schema.init_db`, `keepup.schema.FRONTEND_MODULES` | shim -> `keepup_db` and `keepup_modules` |
| `keepup.locks.*`, `keepup.scheduler.*` | shim -> `keepup_tasks` |
| `keepup.integrations.log_external_request` | shim -> `keepup_integration_log` |
| `keepup.audit.*`, `keepup.events.emit_event`, `keepup.admin_trail` | shim -> `keepup_audit` |
| `keepup.metrics*` | shim -> `keepup_metrics` |
| `keepup.modules.*`, `keepup.plugins.admin` | shim -> `keepup_modules` |
| `keepup.themes.*`, `keepup.web.*` | shim -> `keepup_ui` |
| `keepup.auth.*` | shim -> `keepup_auth` |
| `keepup.cluster.*`, `keepup.notification_bus`, `keepup.instance` | unchanged in 0.4.0; decided with the cluster row of section 6 |
| `keepup.factory.create_app`, `require_signing_key` | unchanged |

Distribution rule: `keepup-admin` (the kernel) depends on nothing at all -- the
standard library and no more (section 4.9); an application names what it wants
(`keepup-postgres`, `keepup-http`, `keepup-ui`) and the chain is the packaging
system's job; `keepup-admin[panel]` is the shortcut for the whole set and
reproduces today's install. An application that installs the framework the way
it does today either installs `[panel]` or takes a loud `ImportError` naming the
distribution to add -- never a half-working import, and never a silent absence
of a library the import needed.

Removals are scheduled, not implied: the shims and
`KeepupSettings.disable_http_server` are documented as deprecated in 0.4.0 and
removed in 0.5.0, in one list, in the CHANGELOG.

Two behaviours change in ways an application can notice, and both are announced
in the upgrade notes:

- a plugin's `initialize()` may now see a requirement missing, which is an
  outcome (`unsatisfied`) rather than an exception -- a plugin that used to
  crash the start now does not;
- `sections.json`'s catalogue stops being the whole truth: a section belongs to
  the plugin that contributes it, and a deployment that switched the plugin off
  loses the section, as it should have from the beginning.

## 9. The UI as a plugin, and the icon in the collapsed sidebar

The panel shell is a capability, and under 0.4.0 it is `keepup-ui`'s: the shell
files, the static mounts (`factory.py:520-542`), the theme service
(`themes.py`), the `/` page (`web.py`) and the section catalogue that renders
navigation.

The sidebar has an icon defect that is worth reading exactly, because it is
three defects and the fix is one mechanism.

**(a) The live navigation is not built from the catalogue at all.**
`updateNavigationWithModules()` (`static/js/main_new.js:1027`) is the only
function that would build the navigation from the catalogue -- and it is never
called: there is no call site. The navigation that exists is built by seven
independent copies, one per section, each with its own hard-coded glyph:
`addUsersNavigation()` (`static/modules/js/users.js:120`, `users`),
`addModulesNavigation()` (`modules.js:66`, `grid`), `addClusterNavigation()`
(`cluster.js:64`, `server`), `addMetricsNavigation()` (`metrics.js:97`,
`bar-chart-2`), `addThemesNavigation()` (`themes.js:562`, `layout`),
`addAuditNavigation()` (`event_manager.js:857`) and the two inline builders of
`background_tasks.js:80` and `integration_logs.js:55` (`clock`, `activity`).

**(b) The word that stays in the collapsed rail is one missing class.** The
rule that hides the label is:

```css
:root:not(.is-narrow) .sidebar-collapsed .nav-text { display: none; }
```

(`static/css/main_new.css:237-239`.) Seven of the eight builders write
`<span class="nav-text">`. `users.js:132` writes:

```js
<i data-feather="users" class="w-5 h-5 mr-3"></i>
<span>Users</span>
```

-- no `nav-text`, so the rule does not reach it, and the collapsed sidebar keeps
the word "Users" beside the glyph. That is the reported defect, and it is a
symptom: a per-section hand-written builder is how one section came to differ
from the other seven.

**(c) The CSS that spaces the icon never applies.** `feather.replace()` removes
the `data-feather` attribute and replaces the `<i>` element with an
`<svg class="feather ...">`. The rules `.nav-item i`
(`main_new.css:221-224`) and `.sidebar-collapsed .nav-item i` (`:251-253`) are
element selectors for an element that no longer exists after the first render,
so the icon gap is applied to nothing -- and the four copies of
`updateNavItemForCollapsedState` that do `navItem.querySelector('i')`
(`modules.js:109`, `background_tasks.js:124`, `integration_logs.js:753`,
`themes.js:602`) operate on `null`.

The release fixes the mechanism, not the three symptoms:

- a catalogue entry gains an **`icon`** field -- a Feather icon name -- and the
  shell renders the icon *and* the label for every section from that one place;
- the seven hand-written builders and the dead `updateNavigationWithModules()`
  go: the shell builds the navigation from `/api/modules`, which is what it was
  always meant to do, and `keepup-ui` contributes it;
- the label is always rendered with the class the CSS matches, and the icon is
  looked up by the class `feather.replace()` leaves rather than by tag name;
- the icons of the built-in sections come from the catalogue: users `users`,
  panel sections `grid`, cluster `server`, metrics `bar-chart-2`, themes
  `layout`, system events `list`, background tasks `clock`, integration log
  `activity` -- the glyphs in use today, moved from the code into the data;
- a section that declares no icon is rendered with a neutral glyph (`square`),
  never with its name alone: the collapsed navigation has one shape, whatever
  arrives in the catalogue;
- an unknown icon name is not fatal: the shell falls back to the neutral glyph
  and logs once.

`keepup-ui` also stops being reachable from the kernel: a deployment without it
has no `/`, no `/keepup-static` and no section catalogue -- which is the point of
7.1, and which is why the users section's icon is a UI matter and not an
`auth` one.

### 9.1 The section catalogue: the file declares, the database remembers

`handlerFunction`, `routePath` and `sectionId` exist only in
`keepup/sections.json`; the `frontend_modules` table (`schema.py:99-114`) has no
such columns. So a section added from the panel cannot carry a handler or a
route, a plugin cannot declare a section at all except by being named in the
application's file, and the truth about one section is split between a file
inside the package and a row in a database. **Decision: the columns are added to
the table**, and the file becomes the declaration that is imported into the
database -- not the place the panel reads.

The catalogue entry gains, beside what the table already has:

| Field | Column | Why it has to be data |
|---|---|---|
| `icon` | `icon` | section 9: the collapsed navigation is rendered from the catalogue |
| `handlerFunction` | `handler_function` | a section that is not the default one needs its own show function |
| `routePath` | `route_path` | an address a section can be linked to (`/selfcare/modules/themes`) |
| `sectionId` | `section_id` | the element a section renders into |
| `adminOnly` | `admin_only` | today it survives only inside the `config` JSON blob |
| `order` | `nav_order` | the navigation is sorted by name today (`modules.py:151-152`), so its order changes when a section is renamed |
| -- | `declared_by` | which plugin (or which file) declared this section -- what makes removal and a read-time filter possible |

**Who owns which field** is the rule that keeps an administrator's work from
being overwritten, and it is the same rule the current sync already follows for
the two fields it owns (`sync_framework_sections`, `modules.py:343-417`:
re-points `js_path`/`css_path`/`init_function`, inserts everything else and
never touches `is_active` or a grant):

| Field | Owner | On every start |
|---|---|---|
| `id`, `declared_by` | the declaring plugin | inserted if missing, never renamed |
| `js_path`, `css_path`, `init_function`, `icon`, `handler_function`, `route_path`, `section_id`, `admin_only`, `nav_order` | the declaring plugin -- they are code, and the code knows where its files are | overwritten |
| `name`, `description` | the plugin's default, the administrator's afterwards | inserted if missing, then left alone |
| `is_active`, the role grants | the administrator | inserted if missing, then left alone |

**The import happens once per start, in phase 7 of section 4.8** -- after the
data source is up and after the optional set is known, because it is the union
of what the running plugins declare plus the framework's own `sections.json`
plus the application's `config/modules.json`. Every statement is idempotent, so
a start that imports twice writes once, and a start that cannot reach the
database is not a start at all (phase 4 stops it).

**A section whose plugin is not running is not in the panel.** The read filters
on `declared_by` being a plugin that initialised, rather than deleting the row:
the grants an administrator made survive a plugin being switched off for an
afternoon and come back with it. This is the behaviour section 6.1 says is
missing today -- `modules.js` keeps showing the section of a plugin that was
disabled.

**Where the file is still read.** `get_modules_from_json_fallback`
(`modules.py:468-495`) already answers `GET /api/modules` from the application's
file when the database cannot answer. It stays, now reading the merged
declaration, and it is what makes the panel say something rather than nothing on
a stand whose catalogue table is unreadable -- with the panel's sections marked
read-only, since grants and visibility have no home without the database.

## 10. The rules that hold the constructor together

Every one of these is a test, not a convention, and 0.4.0 adds or extends them:

1. **The dependency points one way.** Today's version of the rule is about
   applications (`keepup` never imports an application). 0.4.0 adds the inner
   one: **the kernel never imports a capability plugin** -- new
   `tests/kernel_purity_tests.py` reads `keepup/kernel/**` and fails on any
   import of a capability package, in a function too, in the spirit of the
   boundary test the applications already run.
2. **A plugin is loaded, not imported.** No capability module appears in the
   kernel's import graph, and the import of `keepup` does not pull one in --
   checked by running a fresh interpreter and inspecting `sys.modules`, as
   `tests/agent_guide_tests.py` and the consumer tests already do for other
   names.
3. **The catalogue is the truth about what runs**, and the panel's report, the
   cluster picture and the log line all read it, so they cannot disagree
   (`plugins/enablement.py:149`).
4. **Tables are declared, never DDL** (`keepup/tables.py`), including inside a
   plugin package.
5. **A capability package ships no application-specific name** -- no product
   name, no address, no default secret; `tests/distribution_tests.py` extends to
   the new packages.
6. **Every capability plugin has a test that runs it alone** -- the plugin, a
   temporary database and one route -- so that "it works with everything
   installed" stops being the only thing that is checked.
7. **The three deployments of section 7 are tests.** The microservice and the
   no-transport runtime are built in the suite and asserted to expose exactly
   the routes of section 7.1 and no `/`, no `/api/admin/users`, no
   `/api/metrics`.

## 11. Repository and distribution layout

One repository, several distributions, because a plugin's version is its own:

```
keepup-admin/                     the repository
  keepup/                         the kernel distribution: keepup-admin
    kernel/                       the constructor, and the service contracts
    plugins/                      the plugin contract and the loader
    settings.py, security.py
    factory.py, db.py, tables.py, metrics.py, ...   lazy shims (section 8)
  packages/
    keepup-http/                  pyproject.toml + keepup_http/   (fastapi, starlette)
    keepup-ui/                    ... + static/ + sections
    keepup-auth/                  sign-in, sessions, OIDC, identity (bcrypt, pyjwt)
    keepup-users/                 accounts, roles, the users section
    keepup-db/                    keepup_db/  -- the abstraction (sqlalchemy)
    keepup-postgres/              keepup_postgres/  -- the driver (psycopg2, asyncpg)
    keepup-sqlite/                keepup_sqlite/    -- the driver
    keepup-tasks/                 scheduler + locks (apscheduler)
    keepup-integration-log/
    keepup-audit/
    keepup-metrics/               (psutil, prometheus-client)
    keepup-modules/               catalogue + plugin decisions
    keepup-cluster/               if the open question of section 12 says yes
    keepup-log-shipping/          (requests)
  tests/                          the framework's own suite
  openspec/                       the change records
```

The dependency arrows are the whole story of section 4.9:

```
keepup-admin        (standard library only)
   ^
   |  requires the names, never the packages
   |
keepup-db           (sqlalchemy)          keepup-http (fastapi)
   ^                                          ^
   |                                          |
keepup-postgres     (psycopg2)             keepup-ui (nothing)
keepup-sqlite       (nothing)
```

- **Discovery is a Python entry point**, group `keepup.plugins`:
  `users = keepup_users:UsersPlugin`. An application's own plugins keep coming
  from `plugins_dir`, which stays first: an application's plugin shadows a
  built-in one of the same id (and says so in the log).
- **A distribution declares its own plugin** in `keepup/plugins/builtin.json`,
  which ships with the kernel and names plugins without importing them, so the
  catalogue is readable with nothing installed. A `required` plugin found
  through the entry point is enabled by being installed (section 4.9).
- **Versioning**: each package carries its own version, and a service's major
  version is the major version of the distribution that publishes its contract
  (`keepup-db` 1.x provides `datasource>=1`). The kernel's `requires` are on
  service *interfaces*, so a capability may be released on any schedule.
- **Release plumbing**: one tag releases the set; a package whose version did
  not change is not republished. `ci/keepup/` gains a `packages` section naming
  what was released, and the CHANGELOG gains one entry per package that changed.
- **Moving on**: a package that grows its own cadence, its own maintainer or its
  own tests moves to its own repository without the kernel changing -- the entry
  point is the seam.

## 12. Decisions taken, and the ones still open

Taken:

1. **The plugin contract is extended, not replaced.** `BasePlugin` and
   `plugins/routes.py` survive 0.4.0 whole; the descriptor, services and
   contributions are additive. A rewrite would have broken every application for
   a benefit the release can have without it.
2. **Services are the only way capabilities call each other.** `get_handlers()`
   stays for the existing case (a plugin that knows which plugin it wants);
   services are for the case where it must not.
3. **The kernel ships no transport.** HTTP is a plugin, and a deployment that
   enables none is a supported deployment, not a degraded one.
4. **Capabilities ship as separate distributions from the same repository**,
   with a meta-extra (`keepup-admin[panel]`) that reproduces today's install
   exactly.
5. **Shims keep the old import paths for one release**; removal is 0.5.0, in a
   published list.
6. **Plugins have kinds** (section 4.7): `required` ones are libraries -- in the
   application's requirements, in the catalogue, impossible to switch off, and
   their absence stops the start; `optional` ones are what the plugin panel
   switches; a `transport` is a plugin too. The bootstrap order of section 4.8
   follows from it: the required set is resolved from files and the environment
   alone, and only after the data source is up does the administrator's own
   decision about the optional set get read.
7. **`auth` is a plugin** (`keepup-auth`, task 116), and what comes before it is
   the subject-and-right contract (`plugin_constructor.md` section 6.0, task
   119): the eleven kernel modules that import `get_current_admin` stop doing
   so, and only then does the sign-in machinery move. `keepup-users` and
   `keepup-ui` declare `auth` in `requires`.
8. **Delegation is not injection and does not become one.** A plugin may publish
   an instance, a lazy factory or an eager provider (section 4.4); the kernel
   never names a concrete class for a service, and no global container is
   introduced (section 14).
9. **The section catalogue lives in the database, declared by files.** The
   columns the file has and the table lacks are added -- `icon`,
   `handler_function`, `route_path`, `section_id`, `admin_only`, `nav_order`,
   `declared_by` -- and the declaration is imported into the database at start
   (section 9.1). The declaring plugin owns the fields that are code; the
   administrator owns visibility, grants, name and description.
10. **The base bundle depends on the standard library only** (section 4.9).
    `keepup-admin` pulls no SQLAlchemy, no FastAPI, no driver and no client; a
    third-party library arrives with the plugin whose subject it is, and the
    kernel owns the *names and shapes* of the services (`kernel/contracts.py`)
    while the plugins own the implementations.
11. **The database is two distributions and two services** (section 4.9):
    `keepup-db` is the abstraction -- the tables DSL, the pool, the dialect
    registry, the `datasource` service -- and `keepup-postgres` / `keepup-sqlite`
    are the drivers, providing `datasource_driver`. An application names only
    the driver it wants; the abstraction arrives as its dependency.
12. **A required plugin is enabled by being installed**, and **exactly one
    provider per required service may be enabled**: two drivers enabled at once
    stop the start and name both, because a deployment that silently picked one
    would hide which database it is running on.
13. **`instance` and the notification bus stay in the kernel as abstractions**,
    and that does not make the cluster a kernel capability (section 4.9). The
    kernel owns who a replica is and how replicas are addressed; the concrete
    delivery between them is a service (`notify_transport`) that the database
    driver provides, because today it is PostgreSQL's `LISTEN`/`NOTIFY`
    (`notification_bus.py:331`). `keepup-cluster` is a plugin that uses both,
    declares `requires=("notify_transport>=1",)` and is `unsatisfied` -- in the
    report, not in the log -- on a deployment whose database cannot carry a
    message between replicas.
14. **Plugins contribute middleware only to a transport** (section 5.2): HTTP
    middleware is meaningless without HTTP, so `keepup-http` applies the
    contributed stack in a declared order and the kernel never sees it. This is
    what lets the stopped-replica gate move with the cluster and the CSRF
    refresh move with `keepup-auth`.

15. **`requires` and `wants` are separate** (section 4.1): a hard requirement
    stops a plugin, a soft one degrades it and the report says so. Decided with
    the metrics question below: without the distinction, either every capability
    requires every other one, or a panel quietly shows half the truth.
16. **Metrics do not require the bus.** `keepup-metrics` requires `datasource`
    and `wants` `cluster`: the replica list in the panel summary is derived from
    `system_metrics.app_instance` -- a column that already exists
    (`schema.py:87-97`) -- within a freshness window that already exists
    (`fresh_since`, `metrics_api.py:63,66`), and the richer registry is used when
    the cluster plugin is there. Three reasons: **performance** -- a
    `LISTEN`/`NOTIFY` subscriber connection per replica, held open and woken for a
    dashboard one administrator opens occasionally, is a connection and a wake-up
    the deployment does not need; **security** -- a long-lived cross-process
    channel that only feeds a view is attack surface bought for nothing; and
    **dependency direction** -- metrics is what tells you a replica has gone, so
    it must not fail to start because the replica registry is absent.
17. **A required service may be provided by the application** -- a driver of
    one's own, an adapter to a database that is neither PostgreSQL nor SQLite --
    under the three rules of section 4.9: decided before the start through the
    catalogue, provided by an installed distribution rather than a file in
    `plugins_dir`, and replacing the default provider rather than joining it.
    The panel never chooses a provider.
18. **`disable_http_server` becomes the `metrics-only` profile** (sections 4.5
    and 5), keeps working for one release under its old name with a deprecation
    warning naming its replacement, and disappears in 0.5.0. The stripped ASGI
    application is gone either way: it loaded no plugins, started no scheduler,
    registered no replica and sent no security header, and a process whose job is
    to answer Prometheus deserves the headers.

19. **The release is staged, and the split follows the dependency graph.**
    0.4.0 lands the constructor and everything the rule "the base bundle has no
    database library" forces out of it: the kernel (101-103, 119, 120), the
    database chain (106-108), the transport with its reference non-HTTP server
    (103), and the four capabilities whose table declarations and library
    imports live inside modules that cannot stay in the base -- audit (111),
    auth (116) with users (105), the UI (104) -- plus the two that prove the
    mechanics cheaply (110 integration log, 112 metrics), the compatibility
    shims (114), the packaging (115) and the mirror that belongs to the users
    module (83). 0.5.0 keeps what is neither a database library nor entangled
    with one: the tasks plugin (109), module management (113), the cluster
    (117), log shipping (118), the removal of the shims and of
    `disable_http_server` (122), and the last step of the dependency story -- a
    base with no third-party library at all, FastAPI included (121).
    **The trade is stated plainly**: 0.4.0 is the large release, because the
    rule it must satisfy is the reason the small release was proposed and the
    rule wins. A base bundle without a database library and a small 0.4.0 cannot
    both be had.

Open, and to be settled before the implementation tasks are taken:

1. **Whether the `metrics-only` deployment should also require `auth`,** so its
   `/metrics` can be closed rather than public. It is a deployment question, not
   a design one -- `metrics_public` already answers it per stand -- but it is
   worth a line in the upgrade note, because the stripped application answered
   `/metrics` to anybody and some stands rely on that.

The three questions this list used to hold -- metrics and the bus, the meaning
of `disable_http_server`, and substituting a required provider -- are decisions
16, 18 and 17 above.

## 13. What "0.4.0 is done" means

The release is the constructor and the base bundle that has no database library
(section 12, decision 19).

1. The three deployments of section 7 are built in the suite and answer as
   written -- including the one that serves no HTTP.
2. The base distribution installs no SQLAlchemy and no driver, and
   `tests/kernel_purity_tests.py` fails on any import of `keepup_db`,
   `keepup_postgres`, `keepup_sqlite`, `keepup_audit`, `keepup_auth`,
   `keepup_users`, `keepup_ui`, `keepup_metrics` or `keepup_integration_log`
   from a kernel module. The six modules of section 4.9's table are not in the
   base: `audit.py`, `events.py`, `events_api.py`, `admin_trail.py`,
   `themes.py`, `auth/panel_session.py`, `auth/login_throttle.py` and
   `auth/user_roles.py`.
3. The database is plugins: `keepup-db` provides `datasource`,
   `keepup-postgres` / `keepup-sqlite` provide `datasource_driver`, every
   capability declares its own tables, and the data source creates them in
   phase 4 through the service.
4. A fresh virtual environment with `keepup-admin` and `keepup-postgres` -- and
   an empty application catalogue file -- starts against PostgreSQL and serves a
   panel: the abstraction arrived as a dependency, the driver was enabled by
   being installed, and no configuration line named either.
5. A deployment that substitutes its own `datasource` starts and is reported
   with the provider's distribution named; a provider that arrived as a file in
   `plugins_dir` is refused, with the reason in the report.
6. Plugins have kinds and the two-phase order holds: a required plugin cannot be
   switched off, two enabled providers of one required service stop the start
   and name both, and a deployment whose database is unreachable stops instead
   of losing the administrator's decisions silently.
7. `auth` is a plugin and a deployment that enables none has no sign-in routes
   and does not demand a signing key at start; `keepup-users` requires it; the
   subject of a request and the right attached to it come from the service, and
   no kernel module imports `auth.dependencies`.
8. The audit is a plugin: the route wrapper records through the `audit` service,
   the kernel's own trail of administrative decisions goes through `events`, and
   a deployment without the plugin keeps working without an audit rather than
   writing one that silently does nothing.
9. The UI is a plugin: a deployment without it has no `/`, no `/keepup-static`
   and no section catalogue; the catalogue's navigation fields are columns,
   the declaration is imported at start, a section whose plugin is not running
   is not in the panel, and the collapsed sidebar shows a distinct icon per
   section with a neutral glyph for a section that declares none.
10. Metrics and the integration log are plugins, each with a test that runs it
    alone; metrics run with no `notify_transport` and no cluster, list the
    replicas seen in `system_metrics` and mark themselves `degraded` naming
    `cluster` as what is missing. The integration log answers the four routes
    its section has always called.
11. The transport is a plugin: `create_runtime(settings)` starts a runtime that
    serves nothing, the reference transport in the suite answers a request
    through another plugin's handler, and the `metrics-only` profile serves
    Prometheus and nothing else with the security headers the stripped
    application never sent -- while the deprecated `disable_http_server`
    reaches the same deployment and warns.
12. Every name in section 8's table that belonged to what moved still resolves
    (`keepup.db`, `keepup.tables`, `keepup.schema`, `keepup.metrics`,
    `keepup.integrations`, `keepup.web`, `keepup.auth`), the shims raise an
    error naming the distribution to install when it is absent, and the
    deprecation list is in the CHANGELOG.
13. The mirror `users.role` is gone from writes, answers and reads, and the
    applications that read it have moved to the set of roles.
14. An application written against 0.3.0 (the repository's own consumer,
    `tests/consumer/application.py`, and one real application) starts on 0.4.0
    with `keepup-admin[panel]` and no code change.
15. The security audit (`tests/security_audit_tests/run_security_audit.sh`) runs
    against the new package set, including the deployment with no HTTP, the
    deployment with no UI and the deployment with a substituted data source.

### 13.1 What 0.5.0 is done means

What is neither a database library nor entangled with one:

1. `keepup-tasks` is a plugin: the job registry is real, the scheduler is a
   service rather than a module global, the six `asyncio` loops that were in the
   kernel's lifespan are scheduled tasks, and the section shows what actually
   runs instead of an empty list.
2. `keepup-modules` is a plugin: it owns the catalogue, the role grants and the
   decisions about plugins (`plugin_overrides`), and it provides `catalogue` and
   `plugin_decisions`.
3. `keepup-cluster` is a plugin over the kernel's infrastructure: with the
   PostgreSQL driver the replicas see each other and the panel shows them; on
   SQLite the same deployment reports the cluster as `unsatisfied` with the
   missing service named, and the stopped-replica gate arrives as a middleware
   contribution.
4. `keepup-log-shipping` is a plugin, and a deployment that names no collector
   installs nothing that could talk to one.
5. The base distribution depends on the standard library only: a fresh virtual
   environment with `keepup-admin` and nothing else imports the kernel and
   builds a runtime, FastAPI included in what is not there, and `keepup.tables`,
   `keepup.create_app` and the other shims raise an error naming the
   distribution to add.
6. The shims and `disable_http_server` are gone, and the public interface no
   longer declares a removed name.

## 14. Why this shape, and not a container## 14. Why this shape, and not a container

The design above is close enough to Spring Boot that the question is fair, and
the honest answer is that it borrows Spring's *problem statement* and almost none
of its machinery. What is wanted -- conditional composition, a lifecycle,
services with contracts, profiles per deployment -- is exactly what a Spring
`ApplicationContext` provides. What is not wanted is a second place where the
application's wiring lives: in Python, `import` is already a container, and a
framework that adds another one gives every reader two answers to "what is
running".

The models that exist, and what they actually solve:

| Model | Where it is used | What it gives | What it costs |
|---|---|---|---|
| **DI container**: beans, conditional wiring, profiles | Spring Boot; NestJS `@Module`/providers/`onModuleInit`; .NET `IHostedService` | declarative composition, conditional beans, scoped lifetimes | the container becomes a runtime concept; wiring is discovered, not read; in Python it fights the module system and the debugger |
| **Application registry**: ordered apps, `ready()`, signals | Django `INSTALLED_APPS` + `AppConfig`; Pyramid `config.include` + `commit()` | ordered start-up, extension hooks, migrations | settings module is global; one app set per process; no service contracts -- extensions reach each other by import |
| **Hook specifications**: named hooks with implementations, ordered | `pluggy` (pytest, tox); `stevedore` (OpenStack) | third parties extend without registration; `tryfirst`/`trylast` ordering; introspection of who implements what | calls, not objects: a hook cannot hand you a connection pool with a typed contract |
| **Service registry / microkernel**: bundles publish capabilities, consumers resolve lazily | OSGi; Eclipse; `importlib.metadata` entry points | replaceable capabilities, versions, lazy resolution | ordering must be declared or it is undefined; version negotiation is manual |
| **Composition root + application factory** | Flask/FastAPI applications; `create_app` in this repository | explicit, testable, no magic; a test builds a different application from different settings | every new optional capability is a new line of code until contributions exist |
| **Ports and adapters** (hexagonal) | broad | contracts at the boundary; the domain does not know its transport | says nothing about who wires whom -- it is a shape, not a resolver |
| **Full DI libraries** | `dependency-injector`, `punq`, `lagom`, `injector` | autowiring, scopes, overrides in tests | an extra concept, usually a global container, mostly enterprise Python |

**The recommendation is the shape this specification already has**: an
application factory (the composition root) plus an ordered plugin registry
(Django's `INSTALLED_APPS` idea, which this package already half has in
`enablement`/`Priority`) plus a small service registry (OSGi's idea, reduced to
`provide`/`require` with a version), plus contribution points that are closer to
`pluggy`'s hook specifications than to beans -- because a contribution here *is*
a call the framework makes (`get_api_routes()`, `get_panel_sections()`), while
cross-plugin objects travel through the registry, where they can carry a type.

Three reasons, in order of weight:

1. **It stays readable.** The catalogue, the outcomes and the service graph are
   data an administrator and the panel can show (`/api/admin/plugins` already
   does, `plugins/enablement.py:138`). A container's wiring is knowable only by
   running it and asking it.
2. **It is additive.** `BasePlugin`, `PluginManager`, routes-as-data, the
   request mask and the outcome report all survive. Spring's model would be a
   rewrite of the plugin contract, a new dependency and a migration for every
   application -- in a release whose point is that applications do not have to
   change.
3. **It is small.** `provide`/`require` with a version and a topological
   initialisation is a few hundred lines with tests; a container that does
   autowiring, scopes and conditional beans is a project of its own, and the
   part of it that pays here -- conditional composition -- is already delivered
   by the catalogue merge and the `required`/`optional` kinds.

Two things worth stealing from the models above and putting in the release, both
cheap and both missing from 0.3.0:

- **`pluggy`-style introspection of contributions**: `GET /api/admin/plugins`
  should say not only that a plugin initialised but what it contributed --
  three routes, one section, two tables, one job. The data is already collected
  when contributions are mounted; showing it is what makes "everything is a
  plugin" legible instead of a claim.
- **Pyramid's `commit()`**: a start-up that has an explicit end, after which the
  registry is frozen. 0.3.0 mounts routes and never unmounts them
  (`plugins/enablement.py:149-155` explains why the start-up snapshot is the
  truth), and 0.4.0 should say so as a contract -- a plugin that wants to
  influence composition does it in `register()`, and after the freeze it can
  only contribute to a running system (a job, an event), not re-shape it.

If a team later wants autowiring *inside* a plugin, `dependency-injector` or
`punq` can be used there without changing this contract: the plugin still
publishes one named service, and how it builds the object behind it is its own
business. That is the boundary to keep.

### What could go wrong, honestly

Four risks, in the order they are likely to bite, with the guard each one needs:

1. **The purity rule rots.** "The kernel imports no capability" is the rule the
   whole design rests on and the easiest one to break in a hurry -- one
   convenient `from keepup_users import ...` inside a function and the boundary
   is gone, invisibly, because nothing fails. The guard is the test of section
   10, rule 1, and it has to run in CI from the first commit that moves code,
   not after the release.
2. **The service contracts grow fat.** `datasource` is deliberately large,
   because a database abstraction is; the next ten services are not allowed to
   be. A service that starts to carry "and also" is becoming the monolith again,
   only now with a version number. The guard is the service catalogue: a shape
   in that file is a promise, and changing it is a document change.
3. **Fourteen distributions are a release-engineering cost.** Version skew,
   fourteen changelogs, fourteen `pip-audit` runs, a CI matrix that grows with
   the set. The architecture is not the cost here -- the release process is. A
   staged option: cut `keepup-db`, `keepup-postgres` and `keepup-sqlite` as
   distributions in 0.4.0, because the dependency isolation is the point there
   and the two-line install depends on it, and publish the panel-side
   capabilities separately only when one of them actually needs its own cadence.
   The entry-point mechanism is the same either way; what is deferred is the
   publishing ceremony.
4. **The report becomes the only place composition is legible.** With wiring
   spread over descriptors, services and profiles, `/api/admin/plugins` is not a
   convenience any more -- it is how anybody, including us, finds out what is
   running. If it is shallow or wrong, debugging gets expensive, so "what did
   this plugin contribute" (above) is part of the release, not a follow-up.
