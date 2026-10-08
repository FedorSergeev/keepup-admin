# The service catalogue

Every name a plugin may `provide` or `require`, what it means, and who answers
it. `plugin_constructor.md` section 4.4 states the rules; this file is the list.
It is the framework's own document: a name that is not here is a change to the
specification, not a private agreement between two plugins.

**The rules, once:**

1. **A name is a noun the kernel owns.** `services.provide("datasource", ...)`
   is a promise about the shape below, not about a class.
2. **A version is a contract, not a release.** `datasource>=1` means this
   document's `datasource` shape; a provider that breaks it bumps the major
   number. The convention is that a service's major version is the major version
   of the distribution that publishes the contract (`keepup-db` 1.x provides
   `datasource>=1`).
3. **A consumer declares it and asks in `initialize()`**, never in `__init__`
   (section 4.4). A missing requirement is the outcome `unsatisfied`, and the
   report names what was missing.
4. **No plugin imports another plugin.** A plugin imports the kernel (the
   contract module, the settings, the plugin base) and holds the *instance* the
   registry gave it. Two exceptions are named in section "Kernel, not a
   service" below.
5. **A required service has exactly one provider enabled** (section 4.9);
   several providers are refused at start, naming them.

## Services

| Name | V | Provided by | The shape | Asked for by |
|---|---|---|---|---|
| `datasource` | 1 | `keepup-db` | `execute`, `execute_one`, `execute_commit`, `execute_many`, `get_session()`, `raw_connection()`, `dialect`, `pool_status()`, `dispose()`, `ensure_tables(*declared)`, `ensure_columns(...)` | everything that stores anything: users, audit, events, metrics, integration log, tasks, cluster, modules, and the kernel's own table creation in phase 4 |
| `datasource_driver` | 1 | `keepup-postgres`, `keepup-sqlite` | `connection_url()`, `engine_params()`, `dialect`, `last_insert_id(connection, table)`, `column_catalogue(connection, table)`, `supports_returning`, and the `LISTEN`/`NOTIFY` backend when the database has one | `keepup-db` only -- nothing else may know a driver's name |
| `notify_transport` | 1 | `keepup-postgres` | `publish(channel, payload)`, `subscribe(channel, callback)`, `start()`, `stop()` | the kernel's notification bus; `keepup-cluster`, which cannot work without delivery between replicas |
| `plugin_decisions` | 1 | `keepup-modules` | `read() -> {plugin_id: bool}`, `write(plugin_id, enabled, changed_by)`, `clear(plugin_id)` | the kernel, in phase 5 of the bootstrap order (section 4.8); the panel |
| `catalogue` | 1 | `keepup-modules` | `declare(entries)`, `modules_for_roles(roles)`, `all_modules()`, `update_role_modules(role, ids)`, `set_active(module_id, bool)` | `keepup-ui` (it renders the navigation), the kernel's `/api/modules` |
| `auth` | 1 | `keepup-auth` | `current_subject(call)`, `verify_password(username, password)`, `verify_token(token)`, `open_session(subject)`, `revoke(session)`, `csrf_for(session)` | every route that needs to know who is calling, through the call of section 5.1 -- and the eleven kernel modules that import `get_current_admin` today (section 6.0) |
| `permissions` | 1 | `keepup-auth` | `check(subject, AccessRequest) -> bool`, `declared()` | route registration (the `permission` key) and the wrapper |
| `users` | 1 | `keepup-users` | `by_id`, `by_username`, `create`, `block`, `roles_of`, `set_roles`, `system_user()` | audit, the integration log, the admin trail, and any capability that stores a `user_id` |
| `scheduler` | 1 | `keepup-tasks` | `register(job_id, callable, trigger, lock=None)`, `pause()`, `resume()`, `jobs()` | metrics, audit, the integration log, the cluster (a stopped replica pauses the scheduler) |
| `locks` | 1 | `keepup-tasks` | `acquire(name, max_time)`, `release(name)`, `distributed_lock(name)`, `stats()` | audit, events, metrics retention, and the plugin runtime's one-replica `post_construct` |
| `audit` | 1 | `keepup-audit` | `record(call, outcome)`, `redact(body)`, `retention_days()` | the route wrapper, whichever transport it belongs to |
| `events` | 1 | `keepup-audit` | `emit(type, payload)`, `declared_types()` | the kernel's admin trail, applications, other plugins |
| `metrics` | 1 | `keepup-metrics` | `collect(samples)`, `collectors()` | plugins with numbers of their own |
| `integration_log` | 1 | `keepup-integration-log` | `record(host, endpoint, method, outcome, duration, request, response)` | applications calling outside, and plugins that do it for them |
| `ui` | 1 | `keepup-ui` | `sections()`, `shell()`, `theme()` | `keepup-modules` (the catalogue knows what the panel can render), the kernel's `/` |
| `cluster` | 1 | `keepup-cluster` | `members()`, `stop(replica)`, `start(replica)`, `restart(replica)` | the metrics panel (the replica list), the cluster section |

## Kernel, not a service

Three things every plugin touches are part of the kernel and are **imported**,
not resolved. They are contracts without an implementation to replace:

- **`keepup.instance`** -- who this replica is (`get_instance_id`,
  `get_instance_name`). A replica's identity is not a capability; it is a fact
  about the process.
- **the notification bus** -- `get_notification_bus()`, `publish`, `subscribe`.
  The kernel owns the abstraction and the in-process fallback; the delivery
  between processes is the `notify_transport` service of section 4.9. A
  deployment whose database cannot carry a message keeps a bus that only talks
  to itself, and says so.
- **`keepup.tables`** -- the declaration DSL. It is published by the kernel
  (`from keepup import tables`), implemented in `keepup-db`, requires SQLAlchemy,
  and raises an error naming `keepup-db` when that is not installed. One import
  path for applications and plugins alike; a plugin that declares tables writes
  the same line an application writes and declares
  `requires=("datasource>=1",)`, because the tables are created by the data
  source in foreign-key order (phase 4 of section 4.8).

The distinction is the same one section 4.9 draws everywhere: a *name that can
have several implementations* is a service; a *fact about the process* is the
kernel.
