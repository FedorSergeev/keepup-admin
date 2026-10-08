# Capabilities out of the kernel

**Companion to** [plugin_constructor.md](plugin_constructor.md), which holds the
contract. This document holds the evidence for the ten capabilities that leave
the kernel in 0.4.0: where their code is today, what they own, how they are
coupled to the kernel and to each other, what has to become an interface, and
what it costs. Every number and every path below was read from the tree at
0.3.0 (`b4d7dc4`), not estimated from memory.

A capability card ends with **task** -- the `keepup` backlog item that carries
it out. The backlog itself lives in `ci/keepup/backlog.json`, which is build
data and not committed; the specification is.

---

## Which release

The split follows the dependency graph (specification section 12, decision 19):
0.4.0 must produce a base bundle with no database library, and that is what
decides which capabilities move first -- not how big or how interesting they are.

| Card | Capability | Release | Tasks |
|---|---|---|---|
| -- | kernel, kinds, profiles, contribution points, subject and right | **0.4.0** | 100, 101, 102, 103, 119, 120 |
| 2 | the abstraction `keepup-db` | **0.4.0** | 106 |
| 3, 4 | the drivers `keepup-postgres`, `keepup-sqlite` | **0.4.0** | 107, 108 |
| 1 | users -- entangled with auth, and the `users.role` mirror goes with them | **0.4.0** | 105, 83 |
| 7 | event audit -- `audit.py` and `events.py` declare tables and import SQLAlchemy | **0.4.0** | 111 |
| 10 | the UI -- `themes.py` declares a table and imports SQLAlchemy | **0.4.0** | 104 |
| -- | `auth` -- `panel_session`, `login_throttle` and `user_roles` cannot live without SQLAlchemy | **0.4.0** | 116 |
| 6 | integration log -- cheap, and the first capability through the new mechanics | **0.4.0** | 110 |
| 8 | system metrics -- the second, and the one that needs the two route keys | **0.4.0** | 112 |
| -- | packaging of what moved; the shims for one release | **0.4.0** | 114, 115 |
| 5 | background tasks | 0.5.0 | 109 |
| 9 | module management | 0.5.0 | 113 |
| 11 | the cluster | 0.5.0 | 117 |
| -- | log shipping to a collector | 0.5.0 | 118 |
| -- | a base distribution with no third-party dependency at all | 0.5.0 | 121 |
| -- | removal of the shims and of `disable_http_server` | 0.5.0 | 122 |

The six modules whose SQLAlchemy import decides four of those rows -- `audit.py`,
`events.py` (with `events_api.py` and `admin_trail.py`), `themes.py`,
`auth/panel_session.py`, `auth/login_throttle.py`, `auth/user_roles.py` -- and
the dependency graph they produce are in specification section 4.9. What is left
in 0.5.0 is what neither declares a table nor imports a database library: the
tasks, the catalogue, the cluster and the log shipping, plus the removals.

---

## 0. What every capability hits

Three walls stand in front of all ten, and they are contracts rather than
capabilities. They are built once, in 0.4.0, before any card below is attempted.

**(a) A plugin route can only answer JSON, and is always audited.** Core routes
are imperative `@app.get` decorators inside `register_*_routes(app)`
(`factory.py:592-607`), not route dictionaries; a plugin route is a dictionary
(`plugins/routes.py:395-473`) and its wrapper returns the handler's value, which
`IncomingRequestLogger.end_request` stores as data (`plugins/routes.py:356-360`).
There is no key for a response type and no key for "do not audit me". The first
makes Prometheus text impossible for `keepup-metrics`; the second would write a
row into `incoming_requests` for every scrape of every replica. Both keys are
specified in `plugin_constructor.md` section 4.3.

**(b) The framework's tables are a hard-coded tuple.** `CORE_TABLES`
(`schema.py:229-233`, twelve tables in foreign-key order) is created by one
`tables.ensure_tables(*CORE_TABLES)` (`schema.py:269`) inside `init_db`, which
the application calls itself. A plugin creates its own tables in `initialize()`
(`plugins/base.py:70`), which works but is invisible to the framework: nothing
can report which tables a plugin owns, and nothing can create them in an order
the foreign keys accept. This becomes the `tables` contribution point.

**(c) Framework sections are package data, not a declaration.** The eight
sections live in `sections.json` inside the package (`modules.py:320`,
`pyproject.toml:145`), and `handlerFunction`, `routePath` and `sectionId` exist
only there -- `frontend_modules` (`schema.py:99-114`) has no such columns. A
plugin cannot contribute a section at all except by being named in the
application's `config/modules.json`. **Decided:** the columns are added to the
table and the declaration is imported into the database at start
(`plugin_constructor.md` section 9.1). This becomes the `sections` contribution
point.

**And one import stands before even these.** Eleven kernel modules take the
current administrator from `keepup.auth.dependencies.get_current_admin`:
`metrics_api`, `modules`, `locks`, `scheduler`, `cluster`, `events_api`,
`themes`, `web`, `api_docs`, `plugins/admin`, `plugins/routes`. The subject of a
request and the right attached to it are extracted into a service first
(`plugin_constructor.md` section 6.0); until that is done, moving `users` or
`auth` breaks all eleven at once. **Task 119.**

**Two decisions taken after this document was first written** (2026-10-08):
`auth` is a plugin (task 116), and the catalogue's navigation fields become
columns (tasks 113 and 104). Both are recorded in section 12 of the
specification.

---

## 1. User management

**Where it is.** `auth/` -- 5 699 lines over 32 files, plus
`static/modules/js/users.js` (898) and `users.css` (277). The feature itself:
`auth/user_routes.py` 313 (`UserResponse:42`, `UserRolesUpdate:58`,
`_checked_single_role:99`, `notify_account_blocked:128`,
`register_user_routes:143`), `auth/dependencies.py` 556 (`get_user_by_username:64`,
`get_user_by_id:75`, `create_user:160`, `authenticate:207`,
`issue_session_token:243`, `request_token:282`), `auth/user_roles.py` 376 (the
set of roles: `UnknownRole:59`, `AdministratorKept:70`, `normalise:133`,
`ensure_an_administrator_remains:172`, `has_role:275`, `set_roles:311`,
`fill_from_mirror:352`), `auth/seed_accounts.py` 352, `auth/panel_session.py`
386 (`cookie_names:69`, `open_session:130`, `revoke:167`, `csrf_for:226`,
`CsrfCookieRefresh`), `auth/login_throttle.py` 181.

**Owns.** Eight tables: `USERS` (`schema.py:45-66`), `USER_ROLES` (`:167-179`),
`USER_PERMISSIONS` (`:196-211`), `EXTERNAL_ROLE_MAPPINGS` (`:213-226`),
`ROLE_MODULES` (`:181-191`, shared with the catalogue), `AUTH_SESSION`
(`panel_session.py:110-120`), `LOGIN_ATTEMPTS` (`login_throttle.py:52-57`).
Fifteen routes: the admin surface (`user_routes.py:148,165,227,231,237,246,270`)
and the whole sign-in group (`auth/routes.py:309-470`). One section, `users`.

**Coupling.** Inward: `user_routes.py:20-21` (panel_session, user_roles), `:28`
(db); `routes.py:26-44`; `dependencies.py:24-36,54`. Outward: the eleven modules
that take `get_current_admin`. Two structural knots:
`schema.py:285-296` seeds the first administrator and the system user **inside
`init_db`**, so moving `users` breaks the first start unless the schema grows a
hook; and `integrations.py:26` imports `seed_accounts.SYSTEM_USERNAME`, so the
system user is referenced by a capability that is supposed to be independent of
this one.

**The front end has drifted.** `users.js` calls five routes that do not exist:
`POST /api/admin/users/{id}/generate-password` (`:513`),
`POST .../reset-password` (`:613`), `POST .../{action}` for block and unblock
(`:673`) and `GET .../blocks` (`:702`). Only `PUT .../password`
(`user_routes.py:165`), `PATCH /{id}` (`:270`) and `GET`/`PUT .../roles`
(`:237,:246`) are real. The task is the extraction *and* the reconciliation.

**What must become a contract.** The subject and the right (section 0 above);
the users table as a target for other capabilities' foreign keys
(`INTEGRATION_LOGS.user_id`, `schema.py:81`); a schema hook for the first
account; a middleware contribution for `CsrfCookieRefresh` and the cookie names;
the `role_modules` join, which the catalogue also reads.

**Effort: HIGH.** Three obstacles: the eleven reverse edges; the seeding inside
`init_db`; and `users.role`, the deprecated mirror that goes away in this same
release (task 83), so the extraction and the removal have to move together.
**Task 105.**

---

## 2. Data source: the abstraction (`keepup-db`)

**There is no such feature.** `datasource|data_source|dataSource` appears
nowhere in the tree: no table, no route, no section. What exists is
`db.py` (602): `DatabaseConfig` (`:90-224`) with `db_type`, host, port, name,
user, password, `db_path`, `pool_size=5`, `pool_max_overflow=10`,
`pool_timeout=30`, `pool_recycle=3600`, `pool_ping_after_idle=10` (`:104-110`),
`apply_pool:113`, `_load_from_env:132`, `_load_from_file:149`
(`config/postgres.properties`), `is_postgres:212`, `is_sqlite:215`; a
process-wide singleton (`db_config = DatabaseConfig()`, `:595`); and
`DatabaseManagerV2` (`:228-592`), a classmethod singleton with `get_session:246`,
the `*_async` wrappers `:303-322`, `test_connection:324`, `raw_connection:343`,
`execute:378`, `execute_commit:404`, `get_pool_status:490`, `dispose:504`.

**Owns.** Nothing. `PerformanceSettings` (`settings.py:78-105`) is applied in
`factory.apply_performance` (`factory.py:138-154`), which is the only place an
application's pool values reach the engine. `get_pool_status()` has exactly one
caller, and it is a load-test stand (`tests/load/stand_app.py:46`).

**Coupling.** `db_config` is imported by `schema.py:23`, `modules.py:24`,
`notification_bus.py:43`, `audit.py:29`, `factory.py:145`, and lazily by
`themes.py:235,277,317,366`. `DatabaseManagerV2` is imported by 25+ modules,
including `tables.py:108,185,242,258,303` -- the schema engine itself goes to
the database through `from keepup import db as _db` (`tables.py:41`). That is a
cycle: schema depends on the driver, the driver depends on the schema's
identifier check (`db.py:432,458`).

**What must become a contract.** A public manager interface (`execute*`,
`get_session`, `raw_connection`, `get_pool_status`, `dispose`) as an instance
rather than a classmethod singleton; a source registry instead of a module
singleton; a DDL contract so `tables.py` receives a manager rather than
importing one; and a route and section for the pool, which have to be written
rather than moved. **Decided** (specification section 4.9): this is the
distribution `keepup-db`, it carries `tables.py` and `positional_sql.py` with it
(and therefore SQLAlchemy), it provides the service `datasource`, and it
requires a second service, `datasource_driver`, which the concrete backend
provides. It knows no driver name, holds no DSN and opens nothing by itself.

**Effort: MEDIUM as a refactor, GREENFIELD as a feature.** Obstacles: the
singletons are process-wide while the framework states it supports two
applications in one process (`factory.py:1-18`); `tables.py` -> `db` cycle;
`PerformanceSettings` lives in the kernel's settings, so pool configuration is
part of the constructor's contract today. **Task 106.**

---

## 3. PostgreSQL driver (`keepup-postgres`)

**Where it is.** There is no file. PostgreSQL is the `is_postgres()` branch,
scattered over ten files: `db.py` (14 occurrences of a dialect branch),
`themes.py` 13, `tables.py` 13, `schema.py` 10, `audit.py` 7, `events.py` 2,
`modules.py` 2, `auth/panel_session.py` 2, `notification_bus.py` 1,
`auth/login_throttle.py` 1. Key points in `db.py`: a hard-named driver
(`postgresql+psycopg2://`, `:186`), engine parameters (`:194-203`),
`SELECT version()` (`:329`), `information_schema.columns` (`:423-426`),
`ADD COLUMN IF NOT EXISTS` (`:462-464`), `SELECT lastval()` (`:530`),
`RETURNING` (`:552`).

**Owns.** No tables of its own; it owns the *shape* of all seventeen.

**Coupling.** `psycopg2-binary>=2.9` is a mandatory dependency
(`pyproject.toml:55`), so a SQLite deployment installs the PostgreSQL driver.
`asyncpg` is optional (`pyproject.toml:103`) and used by the replica bus
(`notification_bus.py:331`) -- PostgreSQL specificity has already leaked out of
"the database backend" into coordination between replicas. `tables.py:65`
`_DIALECTS` names exactly `{"postgres", "sqlite"}`; `tables.py:68 dialect()`
reads `db_config`. `schema.py:236-242 _hook_types()` is the only dialect
interface that reaches an application.

**What must become a contract.** An open dialect registry (`_DIALECTS`), a way
for a backend to declare itself, and `per_dialect`/`ddl_if`
(`tables.py:112-139`) named as the interface it already is.

**Effort: HIGH.** It is the adapter to the abstraction of card 2 -- it provides
`datasource_driver`, not `datasource` -- and it owns the `LISTEN`/`NOTIFY`
backend the replica bus uses (`notification_bus.py:331`), which is why
`asyncpg` arrives with this distribution. And **it cannot be extracted without
SQLite**: the two are the
halves of one branch, the registry lists exactly those two, and thirteen test
files build on SQLite. **Task 107**, with 108.

---

## 4. SQLite driver (`keepup-sqlite`)

**Where it is.** The same ten files, the other half of every branch. `db.py:189`
(`sqlite:///{db_path}`), `:204-209` (pool parameters, plus
`connect_args={'check_same_thread': False}` because `execute_async` moves
synchronous calls into a thread, `:305-322`), `:332` `sqlite_version()`,
`:422-435` `PRAGMA table_info(...)` guarded by `tables.identifier`, `:468-475`
`ALTER TABLE ... ADD COLUMN` in a try with a catalogue fallback, `:535,540,562`
`last_insert_rowid()`. The DDL differences: unique *indexes* instead of named
constraints (`schema.py:177-178,209-210,224-225`), `Boolean().with_variant(
Integer(), "sqlite")` and `per_dialect(postgres="FALSE", sqlite="0")`
(`themes.py:42-44`), the mirrored unique index for the audit
(`audit.py:411-414`).

**Owns.** No tables; the default data path `data/keepup_app.db` (`db.py:100`)
and its overrides (`DB_PATH` `:138`, `db.path` `:168`).

**Coupling.** The inverse of card 3. `db.py:12` calls SQLite "for development
only", but thirteen test files and the local stand run on it, so its behaviour
is a test dependency of every other card.

**What must become a contract.** The same as card 3, plus: declared
`connect_args`; a declared default data path instead of one in the kernel
(`db.py:100` against the rule that paths to data come from the working
directory); a "how to ask the catalogue" contract (`PRAGMA` takes no
parameters); and an honest statement of which pool settings are ignored
(`pool_max_overflow` and `pool_recycle` mean nothing here, `:204-209`).

**Effort: HIGH**, with card 3. **Task 108.**

---

## 5. Background tasks

**Where it is.** `scheduler.py` 124 (`scheduler = None:45`, `JOB_DEFAULTS:55-59`,
`new_scheduler:62`, `init_scheduler:67`, `register_scheduler_routes:77`),
`locks.py` 436 (`DatabaseLock:36`, `_cleanup_stale_locks:73`, `acquire:46`,
`distributed_lock:191`, `with_distributed_lock:231`, `lock_stats:280`,
`register_lock_routes:304`), `static/modules/js/background_tasks.js` 678 and its
CSS 232.

**Owns.** `DISTRIBUTED_LOCKS` (`schema.py:36-43`). Six routes, all of which
exist and all of which the front end calls: `/api/admin/scheduler/jobs`,
`.../jobs/{id}/trigger`, `.../scheduler/stats`, `/api/admin/locks`, `DELETE
/api/admin/locks/{name}`, `/api/admin/locks/stats`. One section,
`background_tasks`.

**What is actually broken.**

- **The scheduler holds zero jobs.** `add_job` appears nowhere in the package
  outside a test. `factory.py:308-310` logs an empty job list at every start,
  and the section always shows an empty table.
- **The real background work is not in the scheduler.** Six endless loops are
  `asyncio.create_task` in the lifespan (`factory.py:253-279`: metrics, metrics
  retention, audit buffer, audit retention, event retention, socket sessions)
  plus `ReplicaController.launch()` (`:317`). An administrator can neither see
  nor trigger nor stop any of them. `cluster.py:342-343` explains why the
  heartbeat is not a job: a stopped replica pauses the scheduler.
- **Intervals are code.** `metrics.py:46` (15s),
  `metrics_retention.py:48` (1h), `audit.py:333` (1h), `events.py:373` (1h);
  only `metrics_interval` is configurable.
- **The buttons do nothing.** `restartScheduler()` says "not implemented yet"
  (`background_tasks.js:592-599`); `triggerAllJobs()` iterates an empty list;
  `forceCleanLocks()` deletes every lock one by one, outside a transaction.
- **`/api/admin/locks/stats` exists and no section reads it** (`locks.py:416`).

**Coupling.** `scheduler.py:32` -> `get_current_admin`; `locks.py:19-23` ->
`user_roles`, `dependencies`, `db`, `instance`, `roles`; `cluster.py:501-511`
pauses and resumes the scheduler when a replica stops; `locks.py:384-389` writes
a row into `system_metrics`, another capability's table. Consumers of the lock:
`audit.py:375,379`, `events.py:417,421`, `metrics_retention.py:358,363`,
`plugins/registry.py:165-166` -- four capabilities and the plugin runtime.

**What must become a contract.** A job registry ("register a named, scheduled
task with an interval, an overlap policy and a lock"), into which the six
`asyncio` loops also appear, or the panel keeps lying; the scheduler as a
service rather than a module global (`scheduler.py:45`); a lock service for the
kernel; and a way for a route to be exempt from auditing so the job endpoints do
not audit themselves.

**Effort: MEDIUM**, but it touches `cluster`, `metrics`, `audit`, `events` and
the plugin runtime. **Task 109.**

---

## 6. Integration log

**Where it is.** `integrations.py` 223 -- `IntegrationLogger:29`,
`log_request:32`, `get_logs:71`, `get_logs_count:88`, `_filters:102`,
`log_external_request(host, endpoint):121`, `extract_user_id_from_args:175`,
`get_system_user_id:204`; bodies truncated to 10 000 characters (`:50-54`), a
second near-identical INSERT inside the decorator (`:154-165`) truncated to
1 000. The table: `INTEGRATION_LOGS` (`schema.py:68-85`, eighteen columns, a
foreign key to `users`, three indexes). The front end:
`integration_logs.js` 794 and `integration_logs.css` 32.

**Owns.** One table. **Zero routes.**

**The section is dead.** `integration_logs.js` calls
`GET /api/integration-logs` (`:300`), `GET /api/integration-logs/{id}` (`:487`),
`GET /api/integration-logs/stats` (`:598,622`) and
`POST /api/integration-logs/cleanup` (`:711`). No Python file registers any of
them, `integrations.py` does not import FastAPI at all, and `factory.py` does
not import `integrations` -- this is the one capability the kernel does not know
exists. The table has no retention either, unlike `incoming_requests`,
`app_events` and `system_metrics`, so it grows without bound.

**Coupling.** `:21` db, `:22` `get_user_by_id`/`get_user_by_username`, `:26`
`seed_accounts.SYSTEM_USERNAME` (through `get_system_user_id`). No reverse
edges.

**Not to be confused with log shipping.** `log_shipping.py` (695) sends the
application's *own* log to a collector; the integration log records the calls
the application makes *outside*. Different direction, different owner, different
card (see below).

**What must become a contract.** Four routes, written rather than moved; the
foreign key to `users`; the "who is the user of a background call" question; and
a retention policy.

**Effort: LOW-MEDIUM.** Obstacle: the first half of the work is a bug fix for a
section that has never worked. **Task 110.**

---

## 7. Event audit

**Two features with similar names, and they must not be merged.**

**(i) The application's event log.** `events.py` 506 (`APP_EVENTS:41-56`,
JSONB on PostgreSQL and Text on SQLite, `EventManager:61`,
`DEFAULT_EVENTS_RETENTION_DAYS:371`, `purge_old_events:403`,
`events_retention_background:411`, `emit_event:433`, `get_events_paginated:461`,
`init_event_manager:482`, and a `__getattr__` shim at `:496-506` for
`_MOVED_TO_API`), `events_api.py` 236 (six routes:
`POST`/`GET /api/events`, `/api/events/types`, `/api/events/instances`,
`DELETE /api/events/cleanup`, `/api/events/stats`; `register_event_api_routes:66`),
`admin_trail.py` 60, `static/modules/js/event_manager.js` 1 006 and its CSS 385.
One section, `event_manager`.

**(ii) The audit of incoming requests.** `audit.py` 486
(`INCOMING_REQUESTS:392-419`, two unique constraints and three check
constraints, four indexes; `IncomingRequestLogger:156`, buffer constants
`:56-57,132-137`, `configure:118`, `retention_days:346`,
`purge_old_requests:351`, `audit_retention_background:367`,
`init_incoming_requests_table:421`, `background_buffer_flusher:427`,
`log_api_request:447`, the redaction helpers `:64,80`). **No routes at all** --
it is written from the route wrapper (`plugins/routes.py:24,329,336,358`), the
table is created at `factory.py:261`, the flusher and retention start at
`:271,274`, and `/api/admin/health` reports the buffer size (`web.py:20,193`).

**Coupling.** `admin_trail.py:22` writes through `events`, so the kernel's own
trail of administrative decisions depends on a capability that is leaving;
`admin_trail` is called from `modules.py:22`, `themes.py:11`, `events_api.py:17`
and `plugins/admin.py:26`. `events.py:417` takes a distributed lock.
`events.py:502` and `events_api.py:17` form an intentional import cycle
dissolved by the `__getattr__` shim. Retention runs as an `asyncio` task from
the kernel (`factory.py:276`). The routes are registered inside the lifespan
(`factory.py:287`), not in `create_app`.

**What must become a contract.** A write interface for the kernel's own trail; a
`declared_event_types`-style hook is already the model (`settings.py:235`); the
audit write as a service, so a non-HTTP transport can audit too; retention as a
scheduled task of card 5.

**Effort: MEDIUM-HIGH.** Obstacles: the kernel's trail; the lock; the
events/events_api cycle; the registration inside the lifespan. **Task 111.**

---

## 8. System metrics

**Where it is.** `metrics.py` 587 (`SystemMetricsCollector:95`,
`DEFAULT_INTERVAL_SECONDS=15:46`, `ERROR_BACKOFF_SECONDS:30:48`,
`_registry_names:80`, `update_metrics_background:540`, `__getattr__` shim
`:573-587`), `metrics_api.py` 277 (`PANEL_METRICS:47`,
`HISTORY_METRICS:54`, `PANEL_HISTORY_HOURS=24:59`,
`FRESH_WINDOW_MINUTES=5:63`, `fresh_since:66`, `get_historical_metrics:116`,
`register_metrics_routes:146` -- `/metrics` at `:160,168`, the panel's summary
and history at `:175,220`, the replica routes at `:235,268`),
`metrics_retention.py` 377 (`TABLE="system_metrics":31`,
`LOCK_NAME="metrics_retention":32`, `DEFAULT_DETAILED_HOURS=48:40`,
`DEFAULT_RETENTION_DAYS=30:42`, `purge_expired:216`, `thin_old:242`,
`run_sweep:308`, `metrics_retention_background:345`), `metrics.js` 928 and its
CSS 114.

**Owns.** `SYSTEM_METRICS` (`schema.py:87-97`), written by `metrics.py:403` and
also by `locks.py:384-389`; six routes plus a second copy of `/metrics` inside
`_create_stripped_app` (`factory.py:481-483`). One section, `metrics`.
Dependencies: `psutil` (`pyproject.toml:71`) and `prometheus-client` (`:72`),
imported nowhere else except that stripped app.

**Why it cannot move as it stands.** A plugin route cannot answer with a
non-JSON body (section 0a), so Prometheus text is not expressible; every plugin
route is audited, so a scrape every fifteen seconds would write an audit row;
`prometheus_client.REGISTRY` is process-global while two applications per
process are claimed; `metrics_api.py:276` asks `cluster` for the replica list;
and `metrics_retention.py:26-27` imports both `metrics` and `metrics_api`, a
triangle.

**What must become a contract.** The two route keys of section 0a; a metrics
contribution point for other plugins' collectors; retention as a scheduled task
of card 5; and the replica list -- **decided**: metrics requires `datasource`
and only `wants` `cluster`, deriving the replicas from
`system_metrics.app_instance` within the freshness window and marking itself
`degraded` when the registry is absent (specification section 12, decision 16).
No bus subscription, no `notify_transport`.

**Effort: MEDIUM. Task 112.**

---

## 9. Module management

**Two subsystems under one section, already drifting apart.**

**(i) The catalogue of panel sections.** `modules.py` 688 (`configure:47`,
`ModuleCreate:61`, `ModuleUpdate:74`, `get_modules_for_roles:128`,
`_sections_changed:155`, `create_or_update_module:171`, `_EDITABLE:252`,
`update_role_modules:262`, `import_modules_from_json:296`, `FRAMEWORK_SECTIONS:320`,
`sync_framework_sections:343`, `sync_new_modules_from_json:420`,
`get_modules_from_json_fallback:468`, `register_module_routes:498`),
`modules.js` 1 127 and its CSS 188. Tables: `FRONTEND_MODULES`
(`schema.py:99-114`), `ROLE_MODULES` (`:181-191`). Ten routes; one section,
`modules_management`, `adminOnly`.

**(ii) Deciding which backend plugins run.** `plugins/admin.py` 271
(`configure:51`, `read_plugin_overrides:65`, `write_plugin_override:81`,
`_refuse_if_the_deployment_decides:106`, `get_plugins_status:165`,
`set_plugin_enabled:188`, `register_plugin_admin_routes:225`), with
`PLUGIN_OVERRIDES` (`schema.py:121-127`). Five routes, one of which
(`GET /api/admin/plugins`) `modules.js:673` reads from the same section.

**Coupling.** `modules.py:20-24` -> `get_current_admin`, `user_roles`,
`admin_trail`, `cache`, `logging_setup`, `db`. `get_modules_for_roles`
(`modules.py:128`) is read on every request for a user's roles through the
replica cache, which is a hot path of the kernel. Section changes are
invalidated and published between replicas (`cache.attach_to_bus`,
`factory.py:379`). `sections.json` is package data; a plugin contributes a
section only by being named in the application's `config/modules.json`.

**What must become a contract.** A way for a plugin to declare a section; the
catalogue model as a service (`catalogue`) with roles; the section's navigation
fields as columns -- **decided**: `icon`, `handler_function`, `route_path`,
`section_id`, `admin_only`, `nav_order` and `declared_by` are added to
`frontend_modules`, and the declaration is imported at start with per-field
ownership (specification section 9.1); and a pub/sub contract for the cache
invalidation.

**Effort: MEDIUM. Task 113**, with the UI split of task 104.

---

## 10. The UI itself

**Where it is.** 12 169 lines under `static/`: the shell (`index_new.html` 274,
`index_nebula.html` 281, `main_new.js` 2 076, `main_new.css` 869,
`main_nebula.css` 232, `layout_bootstrap.js` 29, `tailwind.js` 82,
`feather-icons.js` 12) and the eight sections' JS and CSS
(`modules.js` 1 127, `event_manager.js` 1 006, `metrics.js` 928, `users.js` 898,
`integration_logs.js` 794, `themes.js` 683, `background_tasks.js` 678,
`cluster.js` 374 and the CSS beside them). Python: `web.py` 194
(`SHELL_DIR:38`, `DEFAULT_PANEL_PAGE:43`, `static_page:46`, `theme_page:79`,
`register_web_routes:118`), `themes.py` 547 (`VISUAL_THEMES:30`,
`ConfigService:47`, `register_theme_routes:479`), `security.py` 54
(`DEFAULT_CONTENT_SECURITY_POLICY:43-54`), `api_versions.py` 100, `api_docs.py`
76, `body_limit.py` 156, `cache.py` 169.

**Owns.** `VISUAL_THEMES` (`themes.py:30-45`); thirteen routes across `web.py`
(`/`, `/selfcare`, `/selfcare/modules/{path}`, `/api/version`, `/favicon.ico`,
`/api/health`, `/api/versions`, `/api/admin/health`) and `themes.py`
(`/api/theme/brand`, `/themes`, `POST /themes/{id}/activate`,
`POST /themes/create`, `DELETE /themes/{id}`); two sections (`themes`, and
`cluster`, the only one carrying the full navigation keys).

**Coupling.** The shell is package data and is mounted unconditionally
(`factory.py:534-542`); `web.py:38 SHELL_DIR` points at it, so nothing renders
without it. `themes.py:366-367` lazily imports `web.static_page` -- themes and
web import each other. Middleware chrome -- CORS, CSP and security headers, the
CSRF cookie refresh, the stopped-replica gate, the body limit, API versioning,
HTTPS redirect -- is kernel (`factory.py:545-590`). `pyproject.toml:136-150`
declares every asset as package data.

**The navigation defect is three defects.** `users.js:132` renders
`<span>Users</span>` without `nav-text` -- the only one of eight sections that
does -- so the collapsed rule (`main_new.css:237-239`) misses it and the word
stays in the 64px rail. `updateNavigationWithModules()` (`main_new.js:1027-1051`)
has no call site at all: live navigation comes from eight independent
`addXNavigation()` copies, each with its own hard-coded `data-feather`, and the
catalogue has no `icon` field. And `.nav-item i` (`main_new.css:221,251`) never
matches after `feather.replace()` swaps the `<i>` for an `<svg>`, which also
nulls `navItem.querySelector('i')` in four duplicated
`updateNavItemForCollapsedState` functions.

**What must become a contract.** A source of the shell (plugin or application);
a section contribution with an icon; the catalogue's navigation fields; and a
middleware contribution, so a UI plugin adds chrome without owning the
constructor.

**Effort: HIGH. Task 104.**

---

## 11. The cluster (`keepup-cluster`)

**Where it is.** `cluster.py` 643 (`ReplicaController`, `register_cluster_routes`
at `:633`, the state machine, the stopped-replica gate), `cluster.js` 374, the
section `cluster` in `sections.json` -- the only section that carries the full
navigation keys -- and two tables, `CLUSTER_MEMBERS` (`schema.py:133`) and
`CLUSTER_COMMANDS` (`:149`).

**What stays in the kernel.** `instance.py` 46 (who this replica is) and the
notification bus (`notification_bus.py` 441) -- the abstraction and the
in-process fallback. **Decision** (specification section 12): both are kernel
infrastructure, and that does not make the cluster a kernel capability. The
concrete delivery between replicas is a service: `notify_transport`, provided by
the database driver, because today it is PostgreSQL's `LISTEN`/`NOTIFY`
(`notification_bus.py:331`) and `asyncpg` therefore arrives with
`keepup-postgres`.

**What moves.** The registry, the commands, the replica state machine, the
routes, the section, and the gate -- which becomes a `middleware` contribution
to the HTTP transport (specification section 5.2), because a runtime that serves
no HTTP has no gate to install. The plugin declares
`requires=("notify_transport>=1",)`, so on a deployment whose database cannot
carry a message between replicas it is `unsatisfied` and the report says so,
instead of a registry that exists and never hears from anybody.

**Coupling.** `cluster.py:335-336` imports the scheduler and pauses/resumes it
when a replica is stopped (`:501-511`) -- the busiest edge, and the reason the
cluster card comes after the tasks card. `metrics_api.py:276` asks the cluster
for the replica list; **decided**: that edge becomes a soft requirement --
`keepup-metrics` `wants` `cluster` and falls back to the instances it has seen in
`system_metrics` (specification section 12, decision 16), so metrics never fails
to start for want of a registry it is supposed to report on. `cluster.py:342-343`
explains why the heartbeat is not a scheduler job: a stopped replica pauses the
scheduler, and a heartbeat inside it would go with it.

**Effort: MEDIUM.** Few lines of coupling, one middleware contribution, one
service, and a section that already knows its own address.

---

## Beyond the ten

| Capability | Today | Decision | Task |
|---|---|---|---|
| Sign-in, sessions, tokens, OIDC, external identity | `auth/routes.py` 514, `panel_session.py` 386, `dependencies.py` 556, `providers/`, `signing_key.py` 138, `oidc.py` 255, `oidc_policy.py` 122, `oidc_routes.py` 265, `identity/` 1 016; tables `AUTH_SESSION`, `LOGIN_ATTEMPTS` | **Decided: a plugin** (`keepup-auth`), with the subject-and-right contract extracted first (task 119) so the eleven reverse edges go first | 116 (contract: 119) |
| Log shipping to a collector | `log_shipping.py` 695, `logging_setup.py` 144; no tables, no routes; four settings fields; `requests` | `keepup-log-shipping`, essentially as it stands -- it is the one capability already parameterised through settings | 118 |

## Kinds, soft requirements, and why the data source is not a metrics collector

Not every plugin in this document is equal, and the release says so in the
descriptor (`plugin_constructor.md` section 4.7). The ones the cards above call
**entangled** are not merely hard to move -- they are the ones without which
nothing can be looked at: the data source above all. A stand with no
`keepup-postgres` has no database, so it has no `plugin_overrides` table, so
it has no administrator's decision about plugins, so it has no panel and
nothing to inspect. The release therefore splits plugins into **required**
(libraries: in the application's requirements, in the catalogue, impossible to
switch off, and their absence stops the start), **optional** (what the plugin
panel switches) and **transport**, and the start-up order of section 4.8 follows
from it: the required set is resolved from files and the environment alone, and
the administrator's decision is read only after the data source is up.

The catalogue cards (2, 3, 4 and 9) are the required ones. Everything else in
this document is optional or a transport, which is what makes a deployment with
no UI and no metrics a real deployment rather than a stripped one.

**Hard and soft requirements are different fields** (`requires` and `wants`,
specification section 4.1): a hard requirement stops a plugin that cannot be
correct -- an audit that cannot record is not an audit -- and a soft one degrades
it, with the report naming what was missing. The cards above use both: metrics
`wants` the cluster rather than requiring it, the integration log `requires`
`datasource` and `wants` `users` (a log without user attribution is worse, not
broken), and the cluster `requires` `notify_transport` because a registry that
cannot hear from another replica is a lie.

**A required service may be replaced by the application** -- the escape hatch
for a database the two drivers do not cover, and for the thread hop every
asynchronous query pays today (`db.py:305-322`) -- under the three rules of
specification section 4.9: through the catalogue, from an installed
distribution, replacing the default rather than joining it.

## The order the evidence asks for

1. **The subject and the right** (task 119) and **the contribution points**
   (task 102), on top of the kernel of task 101. Nothing else can start.
2. **The cheap and the self-contained**: integration log (110), metrics (112),
   background tasks (109), module management (113), data source (106).
3. **The entangled**: users (105), the two database drivers together
   (107, 108), event audit (111), the cluster (117, card 11 above), the UI (104).
4. **Throughout**: the compatibility shims (114) land with the first capability
   that moves, and the packages (115) are cut once the set is stable.

The number in brackets is the backlog task; the reasoning behind each position
is in the card above it.
