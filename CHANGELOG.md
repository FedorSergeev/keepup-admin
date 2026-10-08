# Changelog

Notable changes to `keepup-admin`.

## 0.4.0 — unreleased

The release that turns the framework into a constructor: a kernel that knows
nothing about the capabilities it ships, and capabilities that arrive as
plugins. This entry grows as the tasks of 0.4.0 land.

### Added

- **The seventh import comes off the debt list (keepup-127).** `cluster.py` -- the
  replica registry, the commands between replicas and the routes an administrator
  reads them by -- took the manager at module level in thirteen functions' worth
  of use. The import now sits in each of those functions, and the module can be
  imported by a stand with no database. Seven of the nine modules are paid
  (retention, metrics retention, integrations, web, positional_sql,
  notification_bus, cluster); two remain: locks and the metrics pair with the
  section catalogue and the plugin decisions.

- **The sixth import comes off the debt list (keepup-127).** `notification_bus.py`
  took the manager and the database configuration at module level and used them
  in three functions, so the imports moved into them. Its own checks patched the
  manager as an attribute of that module -- a patch point that exists only while
  the module imports it -- and they now patch `keepup.db.DatabaseManagerV2`,
  which is where the name lives. That is the general consequence of a lazy
  import and it is worth saying plainly: a module that no longer takes a name at
  import time no longer offers it as an attribute, so anything patching it
  through the module has to follow the name. Six of the nine modules are paid;
  three remain (cluster, locks, metrics with its API, the section catalogue and
  the plugin decisions).

- **The fifth import comes off the debt list (keepup-127).** `positional_sql.py`
  took the manager at module level and used it in three functions -- the
  returning-insert helper, the batch executor and the raw-connection reader --
  so the import moved into each of them. Five of the nine modules this task owns
  are paid (retention, metrics retention, integrations, web, positional_sql) and
  four remain: the cluster, locks, metrics with its API, the section catalogue,
  the notification bus and the plugin decisions.

- **The fourth import comes off the debt list (keepup-127).** `web.py` took the
  database manager at module level and used it in the two health checks, each
  inside its route handler; the import moved into them, and the module can be
  imported by a stand with no database. Four of the nine modules keepup-127 owns
  are paid -- retention, metrics retention, integrations, web -- and five remain
  (cluster, locks, metrics, the metrics API, the section catalogue, the
  notification bus, the plugin decisions and the positional-SQL helper, of which
  the first four are the next ones).

- **The import debt is two jobs, and one list hid that (keepup-127, keepup-124).**
  Twenty-one of the modules that load a database while being imported declare
  tables or take SQLAlchemy directly, so no lazy import can help them: their
  answer is that they move into their capability's distribution with their
  declarations, which is keepup-124's work, and the check now names the
  distribution for each. Nine reach the database only inside functions -- the
  cluster, locks, metrics, the metrics API, the section catalogue, the
  notification bus, the plugin decisions, the positional-SQL helper and the
  pages -- and those are what keepup-127 owns, one module at a time, with the
  entry struck off in the same change. Keeping the two in one list made the task
  look unclosable when it was only misattributed.

- **The third import comes off the debt list (keepup-127).** `integrations.py`
  took the database manager at module level and used it in four places, all of
  them inside functions; the import moved into each of them and the module can be
  imported by a stand with no database. Thirty modules still load a database
  while being imported, down from thirty-three, and every payment is claimed in
  the same change that makes it -- the check refuses an entry that no longer
  needs the database.

- **The second import comes off the debt list (keepup-127).**
  `metrics_retention.py` took the database manager at module level and used it in
  three functions; the import moved into them, and the module can be imported by
  a stand with no database. The list of modules that still load a database while
  being imported is down to thirty-one, and striking an entry off remains the
  shape of the work: the check refuses one that no longer needs the database.

- **The first import comes off the debt list (keepup-127).** The list of modules
  that load a database while being imported has thirty-three names on it, and the
  only way to pay that debt is one module at a time with the entry struck off in
  the same change. `retention.py` is the first: it takes the manager in exactly
  one place, inside the function that sweeps, so the import moved there and the
  module can now be imported by a stand that has no database at all.

- **The import debt is measured, not estimated (keepup-127).** `doc/distributions.md`
  named four modules whose import pulls the database in; the check written from
  that list found thirty-three. That is the real size of the release's promise
  that the base carries no database library: while importing any of them loads
  the database, a stand without storage cannot be assembled and there is nowhere
  to move the module to. The thirty-three are now named, each with its reason, in
  a list that may only shrink -- a module outside it fails the check, and so does
  an entry that no longer takes the database, which is the work itself
  (keepup-127, keepup-124). The kernel is on neither side of the list: it takes
  no database in any module, which is what made the constructor possible.

- **What a second attempt at moving the module found (keepup-124, keepup-125,
  keepup-127).** The move of `keepup.db` was attempted again with the two
  compatibility findings fixed, and the full suite named the remaining eight
  failures -- which is the useful result, because they are not about the module
  being moved. Four of them (`events_split_tests.py`,
  `log_shipping_split_tests.py`, `metrics_split_tests.py`, `themes_tests.py`) say
  something real: `keepup.events`, `keepup.log_shipping`, `keepup.metrics` and
  `keepup.themes` import the manager at module level, so with the module moved,
  importing any of them loads the database distribution -- and a release whose
  point is that the base carries no database library cannot have an import that
  reaches for one. Those imports move inside the functions that use them
  (keepup-127). The other four are checks that read the moved module by path and
  follow the name into its new home, which belongs to the move itself
  (keepup-125). `doc/distributions.md` carries the classification, and a check
  holds it to naming all three tasks.

- **A stand-in answers for the declaration of the module it replaced
  (keepup-125).** The first attempt at moving `keepup.db` was refused by the
  public-interface check, and the reason was not the module's interface: the
  compatibility layer's stand-in was created empty, so a check that reads
  `module.__all__` to learn what an application may take found a module declaring
  nothing and reported every name taken from `keepup.db` as undeclared. The
  stand-in now answers for the moved module's `__all__` while keeping its own
  docstring -- it has something to say the moved module did not -- and the check
  that reads declarations sees an interface rather than an absence.

- **A stand-in imports nothing, and a distribution re-exports what is declared
  (keepup-126).** Two of the reasons the first attempt at moving `keepup.db`
  failed were about laziness. Installing a stand-in imports nothing -- the
  destination is reached when an attribute is asked for, and only then -- which
  is what `keepup.themes` and the metrics API depend on when they refuse to touch
  a database on import. And a distribution's `__init__` re-exports what its
  module declares (`__all__`) instead of looping over `dir(module)`: a loop reads
  every attribute a module happens to hold, which is a way to import what nobody
  asked for and to open a database during an import. Both rules are checked, so
  the next attempt at the move starts from them instead of rediscovering them.

- **A stand-in answers for a module, not only for a package (keepup-124).** The
  first attempt at moving `db.py` into `keepup-db` failed at test collection, and
  the failure was worth keeping: the compatibility layer stood in for a *package*
  while what moves is a *module*. The old module exported things the
  distribution's `__init__` does not re-export -- its private helpers, its
  `__file__` -- so code that used them stopped working. A stand-in now answers
  for a module: dunders come from the target, a name the package does not
  re-export is looked up in the modules inside the distribution (already imported
  or discovered), and a name that is nowhere gives a refusal naming the name and
  its new home. The move of `db.py` itself is the next step, and the shim is
  ready for it now rather than after it.

- **The base declares no distribution, and a moved name says what to install
  (keepup-124).** The order written down a moment ago had the base re-export from
  a distribution and declare it as a dependency, which cannot work: every
  distribution depends on `keepup-admin` for the kernel it is loaded by, so the
  reverse edge would point both ways and neither could be installed alone. A name
  that moved is therefore removed from the base rather than re-exported, and the
  compatibility layer answers for it -- and when the capability is not installed,
  it says which one to install (`keepup.db moved to keepup_db: install
  keepup-db`) instead of leaving an application to wonder about a missing module.
  A check holds the base to depending on no distribution at all.

- **The order a move happens in is written down (keepup-124).** The first attempt
  at moving a declaration into a distribution was reverted rather than patched,
  and the reason is worth keeping: the `packages/*` directories are on the suite's
  import path, which is a test arrangement and not an installation, so a fresh
  `pip install keepup-admin` would import a module that is not there. Moving a
  capability's code and taking its library out of the base are therefore one
  change, not two -- the code moves, the old name re-exports from the new home,
  the base declares the distribution it re-exports from, and the compatibility
  layer and the debt check are updated in the same change. `doc/distributions.md`
  states the three conditions, and a check holds the document to them.

- **The distributions are on the import path before they are installed
  (keepup-124).** Moving a declaration into `keepup_db` would otherwise be a
  change that only works after a release, because in the repository a
  distribution is a directory beside the package rather than something pip
  installed. The suite is told where the nine of them are, and a check holds each
  one to importing by its own name and carrying the version of the release --
  which is what makes the move itself checkable as it happens.

- **The constructor keeps no library a capability must carry (keepup-124).** The
  release promises that `keepup-admin` plus a driver is a deployment that talks
  to a database and carries no library it does not use. The code that needs
  SQLAlchemy, a driver, a password hasher and a JWT still lives in the base in
  0.4.0 and moves in the rest of this task, so what is pinned now -- and must not
  regress while the move happens -- is the boundary: the kernel imports none of
  the four, and imports no capability module either, because storage, sign-in and
  the record are reached through services. Each library is named together with
  the distribution that will take it, and the base's remaining dependency on them
  is checked as a debt that the last slice of this task removes, together with
  the check itself.

- **The retired flag is the profile (keepup-123, first slice).**
  `disable_http_server` asked for a stand that serves `/metrics` and a
  raw-request log and nothing else, and it was a second way of assembling an
  application -- past the catalogue and the profiles, and the one place where
  what a deployment consists of was not data. The flag now maps onto the
  `metrics-only` profile of the built-in catalogue, warns once that it is retired
  and names the profile, and an explicitly named profile wins; without the flag
  nothing is said. The stripped application is unchanged for this release, and
  the flag itself goes away in keepup-122. Assembling `create_app` itself through
  the runtime is the rest of keepup-123.

- **Every table has an owner, and the map is checked (keepup-124).** A
  declaration leaves the kernel's `schema.py` together with the module that keeps
  it, so the move needs a map that says where each one goes -- and a map nobody
  checks drifts within a week. `doc/table-ownership.md` names the distribution
  that will hold each declared table, including the three whose capability is
  0.5.0 work (`keepup-tasks`, `keepup-cluster`, `keepup-modules`) and the four
  that have already moved next to their code: sessions and login attempts with
  `keepup-auth`, incoming requests and application events with `keepup-audit`. A
  check fails when a table is declared without an owner, when the map names a
  table that does not exist, when an owner is neither a distribution nor named
  later work, and when a table that has moved is still declared in the kernel.

- **Nobody decides by the role mirror (keepup-83).** `users.role` is the
  deprecated mirror of the set in `user_roles`: ADMIN when it is held, otherwise
  the first role granted. It is written in one transaction with the set and is
  still carried in the payloads so an older panel keeps working -- and it is read
  by nothing, which is the part worth guarding, because `user["role"] ==
  ROLE_ADMIN` is the mistake that hides a second role. A check now refuses any
  comparison against the mirror anywhere in the framework, refuses a mention of
  it outside a named list with a reason, and pins the decision to the set even
  when the mirror disagrees. No code changed: the check records what is already
  true, so that it stays true until the mirror goes away in keepup-122.

- **The abstraction becomes a plugin, and storage finally has a name
  (keepup-124, first slice).** The shapes and the two names were in place
  (keepup-106) and the drivers answered `spec()` (keepup-107, 108), but nothing
  published `datasource`: a capability could only reach storage by importing the
  manager, and the catalogue declared a `db` plugin whose file did not exist.
  `builtin/db.py` is that plugin -- kind `required`, it requires
  `datasource_driver` and provides `datasource` -- and `DatabaseSource` is the
  framework's manager wearing the abstraction's shape: run a statement, run one
  that changes something, create what a capability declared, add a column, open a
  session, hand over a raw cursor, report the pool, close. The dialect comes from
  the driver rather than from the abstraction, and the abstraction owns no table
  of the framework's: a table belongs to the capability that keeps it. Moving
  `db.py` itself into `packages/keepup-db` and taking SQLAlchemy out of the base
  package is the rest of keepup-124.

- **The compatibility layer (keepup-114).** 0.4.0 moves code out of the base
  package and into the distributions beside it, and an application that imports
  an old name has to keep working for one release -- and has to be told where
  the name went, because a silent shim is how an application ends up pinned to a
  release it cannot leave. `keepup/compat.py` holds the map from the old import
  path to its new home, warns once per process, and stands in for a module that
  is no longer there with one that answers exactly like the new one. Nothing is
  redirected while the module is still part of the package: in 0.4.0 the map
  waits, and keepup-124 is the change that makes it act.

- **The distribution layout, and the graph that points one way (keepup-115).**
  Every capability is a plugin and answers for its own, but they all still live
  in one package: the base declares SQLAlchemy, psycopg2-binary, bcrypt and
  PyJWT, so a deployment that wanted a socket and a metrics endpoint installed a
  PostgreSQL driver and a password library as well. `packages/` now holds the
  nine distributions -- `keepup-db`, the two drivers, `keepup-auth`,
  `keepup-users`, `keepup-ui`, `keepup-audit`, `keepup-metrics` and
  `keepup-integration-log` -- each with its own metadata, its dependencies and
  its plugin named in the `keepup.plugins` entry-point group the loader reads.
  The graph points one way: a driver depends on the abstraction, the abstraction
  chooses no database, and no capability depends on another -- they reach each
  other through services. `doc/distributions.md` states what each brings and
  what the base stops carrying in keepup-124, which is where the code and the
  libraries move.

- **The panel becomes a capability (keepup-104, first half).** The shell, its
  sections and its theme chrome are what a deployment shows people, and they
  were the kernel's -- served, catalogued and themed by modules every deployment
  carried, including the ones whose only purpose was a socket. `builtin/ui.py`
  claims them, owns the theme table, declares its contributions and publishes
  the `ui` service: the sections other capabilities contributed, what the
  catalogue granted a role, and the themes a deployment keeps. Storage is a
  *want* rather than a requirement -- a panel whose sections come from code is
  served with no database at all, which is the deployment the specification
  calls a server that is not an admin panel -- and the report marks it
  `degraded` instead of refusing to start. Plugins are now handed the live
  collection of contributions once it is collected, so a capability reads the
  sections of the others without importing them. The shell's routes, its static
  mounts and its CSP middleware travel here in keepup-124.

- **The accounts become a capability (keepup-105, first half).** Accounts, the
  roles they hold and the rights granted are what everything else points at -- a
  session belongs to a user, an audit row is attributed to one, a section is
  shown by role -- and the three tables holding them sit in the kernel's schema,
  so the kernel cannot be assembled without them. `builtin/users.py` claims
  them, declares them once, publishes the `users` service (`roles_of`,
  `has_role`, `count`) so a capability asks who holds a role instead of
  importing the module that reads the table, and contributes the panel's
  accounts section. The declaration and the section's routes move into this
  capability's distribution in keepup-124, and the deprecated `users.role`
  mirror goes away in keepup-83.

- **The sign-in becomes a capability (keepup-116, first half).** Sessions, login
  throttling, roles, OIDC and somebody else's identity system live in the kernel
  and import SQLAlchemy, so a base bundle without a database library cannot be
  assembled while they stay. The kernel already owns the shape and the two names
  (keepup-119); `builtin/auth.py` is a deployment answering behind them: a plugin
  of kind `optional` that requires `datasource`, provides `auth` and
  `permissions`, declares its contributions -- routes, sockets, middleware,
  tables, permissions -- and owns the two tables that answering needs, the
  panel's sessions and the attempts that throttle a guesser. The runtime adopts
  the identity a plugin published, so the panel signs people in with the
  capability rather than with whatever the composition root put there before it.
  The sign-in's routes, its sockets and the CSRF middleware travel into the
  capability's distribution in keepup-124.

- **The audit becomes a service, and the route runtime stops importing it
  (keepup-111).** Three modules of the kernel record what happens -- the incoming
  call, the application's events and what an administrator decided -- and all
  three import SQLAlchemy, so a base bundle without a database library cannot be
  assembled while they stay. The route wrapper no longer imports any of them: it
  opens and closes the record through `keepup/kernel/observability.py`, where a
  deployment puts its recording behind the two names `audit` and `events`, and a
  deployment that registers none serves its calls and writes nothing. `builtin/audit.py`
  is the capability that owns both tables -- `incoming_requests` and
  `app_events` -- declares them once, publishes the two services and hands the
  kernel's own trail an emitter, so the trail of administrative decisions is
  written through a name rather than an import. The event log's routes and its
  panel section travel with the panel (keepup-104), and the three modules move
  into the capability's distribution in keepup-124.

- **System metrics as a capability, and the two route keys it needed
  (keepup-112).** Collecting this replica's numbers, answering Prometheus and
  showing the panel what the fleet is doing were three modules of the kernel
  that every deployment paid for, whether or not it wanted a collector.
  `builtin/metrics.py` owns all three now, and it is the capability that needed
  what keepup-102 added: `/metrics` declares `response_media_type: text/plain`
  and `audit: false`, because a scrape every fifteen seconds per replica is not
  an audit, and a scrape without a registry answers an empty document rather
  than a 500. The panel's replica list comes from the table itself --
  `system_metrics.app_instance` within a freshness window -- so the cluster is a
  soft requirement: metrics report on replicas and do not fail to start for want
  of the registry they report on, and the report marks the plugin `degraded`
  with the names of what is missing. `metrics.collect()` is how another
  capability publishes numbers of its own.

- **The integration log, finished and built from the constructor
  (keepup-110).** Writing to the log has worked for years and showing it never
  did: the panel's section calls `/api/integration-logs`, `/{id}`, `/stats` and
  `/cleanup`, and no module of the framework registered any of them -- the
  section was handed to ADMIN and answered 404. `builtin/integration_logs.py` is
  the first capability assembled entirely from the new mechanics: a descriptor
  (optional, requires `datasource`, wants `users`, provides `integration_log`),
  its table declared once rather than twice, four routes as data with request
  masks and sane bounds, `POST /cleanup` with the retention the table never had,
  and its panel section as a contribution. A check reads the section's own
  JavaScript and fails if it calls a path nothing declares, so the front end and
  the back end cannot drift apart again in silence.

- **The two drivers of one abstraction, and the framework finding its own
  plugins (keepup-107, keepup-108).** A driver is a plugin of kind `required`
  that provides `datasource_driver` and answers the abstraction one question:
  `spec()` -- the dialect, the connection, the engine's parameters, whether
  `INSERT ... RETURNING` answers, and whether the database carries a message
  between replicas. `builtin/postgres.py` and `builtin/sqlite.py` are the two
  the framework ships, each taking what it knows from the deployment's current
  configuration until it travels in a distribution of its own (keepup-124); a
  driver creates no table and owns no pool. Two drivers of one required service
  cannot both answer, so the choice belongs to the catalogue and not to being
  installed: the framework offers both and enables neither, and a deployment
  enables one in a line. SQLite says what it cannot do -- no `RETURNING`, no
  messages between replicas -- in the one place that knows, so a deployment that
  needs a cluster is reported rather than silently deaf. The loader now reads
  the framework's own plugin directory as well, between the installed
  distributions and the application's files, and a runtime can be built without
  any of the framework's plugins when a check wants a deployment of its own.

- **The data source as a service, and tables created by the one who knows the
  dialect (keepup-106).** The kernel stores nothing and knows no database, but a
  capability could only create its own tables in `initialize()` and query
  through a class singleton, so the database could not become a plugin and no
  two declarations could be created in the order their foreign keys need.
  `keepup/kernel/datasource.py` owns the two names -- `datasource`, what every
  capability asks, and `datasource_driver`, what only the abstraction asks --
  and their shapes: `DataSource` (execute, commit, create what was declared,
  close) and a driver that answers one `spec()` (dialect, connection, engine
  parameters, whether `INSERT ... RETURNING` answers, whether the database
  carries a message between replicas). A plugin declares its tables with
  `get_declared_tables()` and never creates one itself; the kernel hands the
  declarations to the service in phase 4 for the required plugins and after the
  optional ones come up, in priority order. A deployment with no data source
  creates nothing: the capabilities that need storage are reported unsatisfied
  with the service named, and the rest of the deployment runs. The service is
  closed with the runtime -- `stop`, `close` or `dispose`. Moving `db.py`,
  `tables.py` and `schema.py` into the `keepup-db` distribution, with the shims
  and `psycopg2` out of the base package, is keepup-124: it needs the
  distribution layout of keepup-115 first.

- **The subject of a call and the right attached to it are the kernel's
  contract (keepup-119).** Eleven modules of the kernel -- the panel's metrics,
  the section catalogue, locks, the scheduler, the cluster, the event API,
  themes, the pages, the API documentation, the plugin admin and the route
  runtime -- used to reach for the current administrator by importing the module
  that finds them, which made the sign-in something every capability depended on
  and nothing could be moved without breaking all eleven at once. The kernel now
  owns the shape of the question (`keepup/kernel/security.py`: `AccessRequest`,
  the `Identity` a deployment puts behind the services, and the names `auth` and
  `permissions`), and the sign-in re-exports that shape, so an application keeps
  importing it where it always did. The route runtime no longer imports the
  sign-in at all: the dependency it signs a caller in with and the checker that
  decides a right come from the deployment, and a deployment with no identity
  system answers 401 instead of failing on an import. `create_app` puts the
  framework's own sign-in behind the two services until `keepup-auth` is a
  plugin of its own and does it in `register()`. A guard
  (`tests/kernel_purity_tests.py`) lists by name the modules that may still
  import the sign-in, with the reason for each: a module outside the list fails,
  and so does an entry that no longer imports it -- the list may only shrink,
  which is what the rest of the release is for.

- **The kinds of plugin, and the three rules that follow from them
  (keepup-120).** A plugin declared nowhere but installed is now offered: this
  is what "a plugin of kind `required` or `transport` is enabled by being
  installed" means, and it was not true -- the catalogue was treated as the only
  source of the composition, so an installed database plugin that an application
  forgot to declare simply did not run. A required plugin may declare a
  `check()`, and the kernel asks it once it is up: a `False` or an exception is
  a start that stops with the reason in the report, because a deployment that
  cannot work is worse than one that does not start. A provider of a service
  some enabled plugin cannot run without must arrive as an installed
  distribution: a file dropped into the plugins directory is refused by name,
  since a driver sees every row and every secret and has to be something
  `pip freeze`, a lock file and an audit can name. The panel may not choose such
  a provider either -- required services are now known from the descriptors of
  everything enabled before the optional plugins are decided, which is the
  moment at which that decision can still be refused. The plugin report carries
  `origin`, so a distribution and a file of the application are told apart.

- **The transport seam: an invocation that is not HTTP, and a carrier that is a
  plugin (keepup-103).** `keepup/kernel/call.py` is one invocation of a declared
  route, knowing nothing about the wire: a `RouteSpec` (path, methods, handler,
  mask, right, keys) and a `Call` (the parameters the transport read, the
  caller, the body, the source). `invoke()` applies the mask, injects the
  caller and the body, checks the right through a checker the transport passes,
  and returns the handler's answer as it is; a refusal of the framework's is a
  `CallError` with a code, which the transport renders in its own vocabulary.
  HTTP becomes an adapter of that invocation -- it reads the request, builds the
  call, maps `CallError` to `HTTPException` and writes the audit -- and
  `accepted_params`/`admit_params` stay public names delegating to the kernel. A
  route's mask is now parsed and matched against the handler's signature when
  contributions are collected: once, at the start, for every transport there
  will ever be, so a declaration nobody can call stops the start instead of
  reaching a handler unchecked. A transport is a plugin of kind `transport`
  (`keepup/kernel/transports.py`): it contributes a server rather than routes,
  `Runtime.serve_all()` serves every enabled one at once, and
  `Runtime.run_forever()` is start, serve, stop -- while a deployment with no
  transport is a worker, which the log says rather than the framework refusing.
  A reference transport -- two lines over a TCP socket, answering through
  another plugin's route -- lives in the suite as the proof the seam is real.

- **Contribution points, and the two route keys a capability needs to become a
  plugin (keepup-102).** A plugin declares what it contributes -- routes,
  sockets, panel sections, tables, jobs, event sinks, metric collectors,
  middleware, a transport, settings defaults, the rights its routes may ask for
  -- and the kernel collects each kind for its one consumer
  (`keepup/kernel/contributions.py`). The list is closed: a new kind is a change
  to the specification. A plugin whose getter raises loses that kind and keeps
  the other ten, and the plugin report carries the reason. Middleware
  contributes with an order -- outermost first -- so a layer that must see the
  registered path says so instead of relying on where it was added. Two route
  keys are added, because without them a capability cannot move out of the
  kernel: `response_media_type`, so a route can answer something that is not
  JSON (Prometheus text, for one), and `audit: false`, so a route that is asked
  by a monitor every few seconds is not written to the incoming-request audit
  once per scrape per replica. The default stays "audited". Loading and binding
  are now separate steps (`load_and_initialize` and `initialize_plugins`): a
  runtime that serves no HTTP initialises its plugins without an application,
  and a route declaration the runtime refuses -- a mask naming a parameter the
  handler does not take -- stops the start where it is found instead of leaving
  part of a plugin's routes quietly unregistered.

- **`keepup.kernel` — the plugin constructor (keepup-101).** A plugin declares a
  *descriptor* as a class attribute: its id, its kind (`required`, `optional` or
  `transport`), the services it `requires` (without which it must not run) and
  `wants` (without which it runs worse, and the report says so), the services it
  `provides`, and what it contributes. The kernel reads the descriptor without
  constructing the plugin, so a plugin whose requirement nobody satisfies is
  reported rather than built. What a deployment offers is a *catalogue*: the
  framework's own `plugins/builtin.json`, the application's
  `plugins_config_path` file (whose entry wins field by field) and a *profile*
  -- a named patch over the catalogue, `KeepupSettings.profile`, of which the
  framework declares `panel` and `metrics-only`. Plugins publish services into a
  registry and ask for them by name and version while initialising, so nothing
  imports anything; a service may be published ready, built lazily on first use,
  or started and stopped with the runtime. The start-up is two-phase, because
  the administrator's decision about plugins lives in a database a plugin may
  provide: the required set is resolved from files and the environment, is
  registered and initialised, and only then is the administrator asked. A plugin
  of kind `required` is enabled by being installed and cannot be switched off --
  not by the panel, and not by `PLUGINS_DISABLE`, which stops the start instead;
  two enabled providers of one required service stop the start and name both. A
  plugin written for 0.3.0 has no descriptor and needs none: it is accepted as
  it is. The state of a runtime belongs to the runtime, not to module globals, so
  two applications in one process resolve, publish and report separately. The
  contract is `doc/plugin_constructor.md`, the service names are
  `doc/service-catalogue.md`, and the extraction plan is
  `doc/capabilities-out-of-the-kernel.md`.

## 0.3.0 — 2026-10-07

A release about the panel's own security: it is served with a
Content-Security-Policy and carries no JavaScript in its markup any more, the
security audit after 0.2 is worked through finding by finding, and the
deployment's catalogue of sections and plugins is read from the file the
application names.

**Upgrading from 0.2.0.** A user now holds a set of roles (below, *Added*):
nothing has to be done for an existing database -- the start fills the sets
from `users.role` -- but code that reads that column reads a deprecated mirror
that goes in 0.4.0, and should move to `roles`. The set was written after
0.2.0 had been tagged and its entry stood under 0.2.0 by mistake; the 0.2.0
wheel does not have it. The panel is also served with a
Content-Security-Policy now (below, *Security*): the framework's own pages and
sections carry no inline handler and no inline script, and an application whose
own theme page or sections still do either converts them the same way or sends
the policy as a report (`csp_report_only=True`) until it has. Over HTTPS the
session cookie is named `__Host-ss_session` rather than `ss_session` (below,
*Security*): an application that reads that cookie by string reads the
published name instead (`keepup.auth.panel_session.cookie_names`).

### Added

- **A pluggable identity provider: somebody else's system behind keepup.**
  For an application installed inside a larger system whose own subsystem
  knows who everybody is and what they may do, and answers through its own
  API. A plugin subclasses `keepup.auth.identity.IdentityProvider` and
  implements what that system can answer: `verify_token` (its tokens),
  `verify_password` (its passwords, for the panel), `decide` (its rights). The
  deployment names the plugin in an `identity_provider` section of the
  authentication file -- `AUTH_CONFIG_PATH`, a configmap -- as `module:Class`
  or an entry point of the `keepup.identity_providers` group, with settings in
  which `${ENV}` is substituted, the modes, the policy for new accounts, a
  role mapping and cache lifetimes; `KeepupSettings.identity_provider` does the
  same from code. A mistake in the section stops the start. With token mode on,
  every route that signs the caller in -- the framework's, the plugins', the
  signed-in sockets -- takes a token the framework does not recognise as its
  own to the provider; the caller becomes the account matched by (provider,
  subject), roles in step with the mapping on every request; an unavailable
  provider is a 503. Such a token is never exchanged for a session here:
  renewal and the move into the cookie answer 400. Without the section nothing
  changes.
- **A route may require a right.** The route key `permission` and the
  dependency `keepup.auth.identity.require_permission(name)`: decided locally
  (an administrator, a `user_permissions` row, a right the provider's identity
  lists) or, with `authorization: provider`, by the provider -- whose silence
  closes the door with a 503. A permission on a route the framework does not
  sign in stops the registration.

- **A user holds a set of roles.** `keepup.auth.user_roles` and the `user_roles`
  table are the truth about who this is, and the panel sections and the plugins
  of every role held are glued together — each section and each plugin once,
  in an order that does not depend on the order of the roles. The
  administrative right is `ADMIN` being in the set: somebody who both rents a
  machine out and rents one, or an administrator looking at what a client
  complains about, no longer has to give up one ability to get the other.
  Roles are granted through `GET`/`PUT /api/admin/users/{id}/roles` and in the
  panel's Users section; an empty set is refused (access is closed by blocking
  the account) and so is a role nothing declares — `role_modules` is joined by
  the exact name, so a role written in another case would look granted and grant
  nothing. `has_role(user, ROLE_ADMIN)` is how code asks. Read
  `keepup.modules.get_modules_for_roles()` where `get_modules_for_role()` took
  one name; the single-name read stays.
- **`keepup.positional_sql`**: `positional()`, `positional_many()`,
  `insert_returning_id()`, `execute_many()` and `raw_connection()` for statements
  written with `?` placeholders. Three applications kept identical copies of
  these; moving to this release, an application imports them from here and
  deletes its copy.

### Security

- **The panel's cookies belong to the host that set them.** Over HTTPS the
  session and CSRF cookies are named `__Host-ss_session` and `__Host-ss_csrf`:
  a browser accepts a `__Host-` cookie only from the exact host, only with
  `Secure`, only with `Path=/`, and never with a `Domain` -- so a sibling
  subdomain, or a hop over plain HTTP, can no longer plant a cookie with the
  name the server reads (audit finding 13, keepup-92). The plain names are
  still read for one release, so a panel signed in before the upgrade stays
  signed in and its value moves to the prefixed name on the next read; over
  plain HTTP the names are unchanged, because a `__Host-` cookie without
  `Secure` is one a browser refuses to keep. The names are published
  (`keepup.auth.panel_session.cookie_names`, `session_token_in`) for an
  application that reads them itself: one that looks up `ss_session` by string
  stops seeing the session on an HTTPS stand and moves to the published names.

- **The panel is served with a Content-Security-Policy.** Every answer now
  carries one, and the policy is written for the panel as the framework ships
  it: `script-src 'self'`, so a script the page did not load itself does not run
  at all. That is the general answer to the stored XSS of keepup-62 -- an
  escaping mistake becomes a broken button rather than a running script -- and
  it is only possible because the framework's own front end no longer puts
  JavaScript in its markup. The shell, both theme pages and the eight framework
  sections (Users, Panel sections, Themes, Cluster, Metrics, Event log,
  Integration logs, Background tasks) register what their buttons do with
  `KeepupActions` and name it in a `data-action` attribute, and the adaptive
  layout bootstrap moved out of the head of every theme page into
  `js/layout_bootstrap.js`, which both pages load. The action is always one the
  section registered: markup can name an action, never a function, and a name
  nothing registered does nothing. `KeepupSettings.content_security_policy`
  replaces the policy
  (`keepup.security.DEFAULT_CONTENT_SECURITY_POLICY` is the default and is
  public so that an application can extend it), `csp_report_only=True` sends it
  as `Content-Security-Policy-Report-Only` and refuses nothing, and `None`
  sends no policy. A policy the response sets itself is kept, as with the other
  security headers. **Upgrading:** an application whose own theme page or
  sections still carry inline handlers is held to the policy from the first
  start after the upgrade -- `csp_report_only=True` is the way to see what it
  would refuse without breaking the panel while its sections are converted.

- **The API schema answers an administrator only.** `/openapi.json` described
  every route, the administrative ones with their parameters, to anybody, and no
  setting switched it off. The schema now requires the administrative right like
  the framework's other administrative routes; the documentation pages stay
  public shells that fetch it with the panel's cookie. `openapi_url` moves it or,
  set to None, removes it with the pages; `openapi_public=True` publishes it on
  purpose. `app.openapi()` in code is unchanged. **Upgrading:** a client that
  fetched the schema without signing in now gets 401.

- **PyJWT at least 2.15, urllib3 at least 2.8.** Ten advisories against PyJWT
  2.13 were published after 2.13 had been made the floor, fixed in 2.14.0, and
  one more against 2.14 the same day, fixed in 2.15.0; three against urllib3
  2.7 are fixed in 2.8.0. The floors follow. An application that pins either
  below its floor raises the pin when it moves to this release -- otherwise the
  installation does not resolve.

- **The panel shell hears only its own origin.** Its `message` handler acted
  on whatever any window posted, so any site the user had open could make the
  panel announce a payment and reload a section. Messages from another origin
  are ignored; the rest are handed on as a `keepup:message` window event for
  sections to listen to.

- **A token without a session is refused.** Tokens from before sessions were
  recorded were accepted until they expired; that transition is over. Every
  token the server issues names a session, one that does not is refused on a
  request, at the exchange for the cookie and by the CSRF check.

- **The first administrator's password stays out of the logs, and the public
  one is retired.** A generated password was written into the start-up log,
  which is also a file and a stream to the collector; it now goes only to the
  process's standard error. `admin123` from earlier builds can no longer sign
  in (403 with what to do), and a start given `ADMIN_INITIAL_PASSWORD`
  replaces it. Upgrading: an administrator still on `admin123` sets the
  variable and restarts, or another administrator changes the password.

- **Answers and logs carry no text the server did not write.** The event-log
  and panel-section routes answered a failure with the exception's text, and
  the request audit recorded it unredacted; they now answer a generic message
  and log the exception, and the audit keeps the exception's class (a refusal
  keeps its reason, which is its answer). `logging_setup.for_log`
  escapes control characters in values written into a log line, so a `%0a`
  in a request path no longer forges a line.

- **Registering with the log collector writes no token to disk.**
  `create_logger_token` wrote the issued token in the clear to
  `config/logger_token.json` and waited for the collector without a limit.
  It now only returns the token -- keep it in the environment and hand it back
  through `configure(remote_token=...)` -- and gives up after
  `REGISTRATION_TIMEOUT`. A `config/logger_token.json` left by an earlier
  version should be deleted once its token is in the environment.

- **The email-domain policy admits only a verified email.**
  `create_if_email_domain` refused only the boolean `email_verified: false`,
  so a missing claim or the string `"false"` let an address the person typed
  be compared with the admitted domains. It now requires `true` (the boolean
  or the string, `oidc_policy.email_is_verified`).

- **The CSRF value belongs to the session.** It was random, survived every
  sign-in and was trusted from its cookie, so whoever could plant a cookie
  chose the value the check accepted. It is now derived from the session
  (`panel_session.csrf_for`) and checked against it: new with every sign-in,
  the same across renewals. `CsrfCookieRefresh` puts the session's value in
  the cookie on any read that carries another, so a panel opened before the
  upgrade keeps renewing. Cookie names are unchanged; applications read them.

- **The lowest versions the package admits are free of known advisories.**
  The ranges let an installation keep PyJWT, python-multipart and requests
  versions with published advisories, and left `cryptography` and `urllib3`
  unbounded. Floors are now `pyjwt>=2.13`, `python-multipart>=0.0.31`,
  `requests>=2.33`, `cryptography>=50.0`, `urllib3>=2.7`, and the security
  workflow audits the floors themselves as well as what resolves today.
  An application pinning any of these lower has to raise its pin.

- **What an administrator changes leaves a trace in the event log.** Purging
  the log, changing panel sections and their grants, themes and plugin
  decisions now each write an event naming the administrator
  (`keepup.admin_trail`). `POST /api/events` refuses the types the application
  declares and the framework's own trail types with 400, and marks what it
  accepts with `event_data.manual`. `POST /api/admin/instances/{id}/restart`
  issues the cluster's restart command -- refused with the cluster's reason
  when it cannot be carried out -- instead of answering "sent" and doing
  nothing.

- **Envelopes on the replicas' bus are sealed.** Any database role that can
  connect may NOTIFY on the application's channel, and an envelope was accepted
  on its shape alone. `encode_envelope` now adds the time sent and an
  HMAC-SHA256 seal under a key derived from the signing secret;
  `decode_envelope` drops an envelope without a valid seal or older than
  `MAX_ENVELOPE_AGE_SECONDS` (300) and strips both fields. Replicas of the
  previous version do not hear the new ones: deploy all replicas together.

- **An open socket ends with its session.** A socket was checked only at the
  handshake, so one opened with a stolen token outlived a logout, a password
  change and a block. Sockets signed in through `authenticate_websocket` (and
  so every `require_auth` route) are now held per replica with their session
  and closed with 1008 once it is revoked: at once on the replica that revoked
  it, on the others when the replicas' bus wakes them, and within
  `KEEPUP_SOCKET_SESSION_RECHECK_SECONDS` (30) at the latest.

- **A token can no longer renew its session past the renewal window.** The
  exchange of a token for the panel cookie renewed the session without the
  window check that refresh makes, so a stolen token lived for ever. Every
  renewal now goes through the check. A token without `exp` or `sub` is
  refused, and a token that names a session is accepted only while that
  session belongs to the account the token names.

- **The guessing limit holds against attempts sent at once, and the time of a
  refusal no longer says which names exist.** An attempt is counted before the
  password is checked (`login_throttle.reserve_attempt`), so parallel attempts
  past the limit are refused without reaching bcrypt; a missing name is checked
  against a stand-in hash of the same cost.

- **A user name can no longer run as a script in the panel.** The Users
  section spliced the name into markup and into inline handlers, so a name
  with markup ran in the session of the administrator who opened the list; the
  integration log view did the same with names, addresses and bodies. Sections
  escape data (`keepupEscapeHtml`, `keepupJsArg` in the shell), the Users list
  binds its buttons by id, notifications show text, and a user name is held to
  letters, digits and `. _ @ + -` (64 at most) wherever an account is created.

- **Bodies the framework parses before the sign-in check are small by
  default.** JSON is parsed before a route's dependencies run, and a parsed
  body takes tens of times its size in memory, so the 256 MiB default let one
  unauthenticated request exhaust a replica. `KeepupSettings.max_json_bytes`
  (2 MiB) is now the default for routes whose body the framework reads;
  `max_upload_bytes` applies only when the application sets it. The limit
  covers every method but GET and HEAD, a chunked body that passes it no
  longer reaches the handler as an empty body, and the stripped mode no
  longer reads a body just to log its size.

### Fixed

- **The catalogue of panel sections comes from the file the application named.**
  `init_db()` seeded `frontend_modules` and the role grants from
  `config/modules.json` whatever path the application had given in
  `plugins_config_path`, while its plugins came up from the file it did name: a
  deployment that keeps its catalogue elsewhere started with the framework's
  eight sections and none of its own, and said nothing. The path now reaches
  `keepup.modules` the way it reaches `keepup.plugins.admin` and
  `initialize_plugins()`; `init_db()` takes it as an argument for an application
  that calls it before `create_app()`; and the start seeds the catalogue from
  the settings again -- off the event loop, and idempotent, so what an
  administrator changed stays as they left it.

- **`keepup.tables.auto_id` and `keepup.tables.NOW` are declared public.** The
  guide has an application write `tables.auto_id()` and `tables.NOW` in a table
  declaration, and neither stood in the module's `__all__`: by the package's own
  rule, an application taking them was depending on a name nobody promised. The
  consumer the public-interface check is run against takes both, which is how
  the omission showed.

- **The panel's plugin report reads the deployment's own catalogue.**
  `GET /api/admin/plugins`, and the routes that change a plugin's decision,
  read `config/modules.json` whatever path the application had named in
  `plugins_config_path`; a deployment that keeps the file elsewhere -- every
  test application, and any stand that named another one -- got a 500 from the
  panel while its plugins came up from the file it did name. The path now
  reaches `keepup.plugins.admin` the way it already reached
  `initialize_plugins()`.

- **The panel page is revalidated like the shell's assets.** `GatedStaticFiles`
  tells the browser to revalidate every script and stylesheet it serves, and the
  page those assets belong to was left to the browser's invented freshness --
  10% of the age of the file, which for a stand that has been up for months is
  days. A page cached before this release carries the inline handlers the
  Content-Security-Policy now refuses, so it is served with `Cache-Control:
  no-cache` too.

- **The security headers cost what they are.** `SecurityHeadersMiddleware` was
  a BaseHTTPMiddleware, which runs every request through a task and a body
  stream of its own; it is plain ASGI now and adds the same headers to the
  start of the response, leaving any the response set itself.

- **A connection is pinged only after lying idle.** The pool pinged the
  database on every checkout, a fifth of what a replica spent on the load
  stand. A connection is pinged at checkout only when it has lain in the pool
  longer than `DB_POOL_PING_AFTER_IDLE` seconds
  (`PerformanceSettings.db_pool_ping_after_idle`, 10 by default; 0 pings every
  time); one failing the ping is replaced as before.

- **A read does not commit a transaction.** `execute()` and `execute_one()`
  wrapped every statement in a transaction and committed it, reads included --
  a round trip for a query that wrote nothing. A plain SELECT runs in
  autocommit now; writes, locking reads and `shared_session()` blocks keep
  their transaction.

- **The session check of a request takes one connection.** It checked the
  session, read the account and its roles as three separate trips to the
  database, each with its own checkout, ping and commit -- half of what a
  replica spent on the load stand. `DatabaseManagerV2.shared_session()` runs a
  block's queries on one session and one connection, and the check uses it.

- **An account without a local password answers the sign-in form with no.**
  The password column of an account an outside identity owns is not a bcrypt
  hash, and checking a password against it raised -- a 500 on the form instead
  of a 401.

- **An application without plugins can be signed into.** The framework
  registered sign-in, sign-out and user management only when the application
  passed a plugin manager, so an application with no plugins got a panel
  nobody could sign into. They are registered always now.

- **A fresh installation reaches PostgreSQL.** The connection address was a
  bare `postgresql://`, which SQLAlchemy 2.1 resolves to psycopg 3; the
  package installs psycopg2, so every fresh installation failed on its first
  query. The driver is named: `postgresql+psycopg2://`.

- **Nothing in the framework decides by the `role` mirror.** The lock
  endpoints refused an administrator whose mirror disagreed with the set, and
  a provider sign-in compared the provider's role with the mirror. Both use
  the set now. The mirror is still written and answered; it goes in 0.4.0.

- **Signing in through a provider and starting up keep the database off the
  loop.** The end of a provider sign-in -- finding or creating the account,
  bringing its roles in step, opening the session -- and the table set-up at
  start-up queried the database on the event loop. They run on a worker
  thread now (`oidc_routes.complete_sign_in`).

- **The database configuration is built once.** `keepup.db` built
  `DatabaseConfig` twice, the second instance replacing the first at import.

- **Creating a theme answers its own id.** On SQLite the id came from a
  separate `SELECT last_insert_rowid()`, which could reach another pooled
  connection and answer another insert's id or 0; a theme created active then
  activated some other row. The insert and the read now share one session.

- **Deleting a theme says why it cannot.** The check compared the theme with
  the cached active theme, whose query selects no id, so it never fired; the
  delete's own condition kept the active theme and the answer could not tell
  "not found" from "active". `ConfigService.deletion_refusal()` reads the
  theme's state by id, and the route answers the reason.

- **The event list answers with the event's data.** It put the event's text,
  parsed as JSON, into `event_data` and did not answer with the stored data at
  all. The data is now in `data`.

- **Panel sections can be created and changed through the API.**
  `POST` and `PUT /api/admin/modules` handed the section to the writer under
  their own field names while it reads the catalogue file's, and answered 400
  to every request. The routes now translate; a change keeps the fields it
  does not name, and `is_active: false` takes the section away.

- **Creating a user creates it once.** `dependencies.create_user` handed the
  new account to the provider again, with the name and password as tuples, and
  the provider called back into it: a second insert failed and was logged on
  every creation.

- **A float parameter of a request mask is a finite number.** `float()` reads
  `"nan"` and `"inf"`, and NaN passed any `min`/`max` -- every comparison
  with it is false. Such values are now refused as not a number.

- **The deployment keeps an administrator.** Neither route that changes roles
  takes `ADMIN` from the administrator making the change or from the last
  active account holding it any more (400 with the reason). The single role of
  `PATCH /api/admin/users/{id}` is now checked against the declared roles and
  stored as declared, like the set -- a name nothing declares is refused.

- **A lock's time is written and judged by one clock.** The holder's time was
  written by the database's `CURRENT_TIMESTAMP` and compared with the
  application's UTC; with a database zone other than UTC a crashed holder's
  lock hung for hours, or a live one was taken over at once. The time written
  is now the application's.

- **Log and audit queues stay bounded while their receiver is away.** Log
  shipping kept records without a bound while the collector was down or no
  token was given, and past a full batch started a thread per record. It now
  keeps nothing without a token, holds at most `REMOTE_MAX_QUEUED` (50 000,
  `configure(max_queued=...)`) dropping the oldest, and wakes its one thread.
  The request audit holds at most `BUFFER_HARD_LIMIT` (10 000) requests, runs
  one flush at a time and pauses requests' flushes for five seconds after a
  failed one. Both report what they dropped.

### Changed

- **`keepup.auth.permissions.require_permission` decides by the same rule** as
  the route key: it read `current_user['permissions']`, which nothing fills in,
  so it refused everybody, administrators included.

- **Signing in and the management of users are separate modules.**
  `keepup.auth.routes` keeps signing in, the panel session, token renewal,
  sign-out and registration; profiles, the administration of accounts and
  roles moved to `keepup.auth.user_routes`. `register_auth_routes` still
  registers both, and the moved names (`UserResponse`, `update_user_profile`,
  ...) still answer from `keepup.auth.routes`, with a DeprecationWarning.

### Deprecated

- `DatabaseManagerV2.execute_commit_with_positional`: a leftover of the
  positional manager removed in 0.2.0. It warns, and goes in the next minor
  release; write the statement with named parameters.
- `DatabaseManagerV2.get_last_insert_rowid()` without the inserting session:
  it opens a new one and may answer another insert's id. Use
  `execute_commit_returning()`.
- `event_data` in the answer of `GET /api/events`: it carries the event's text,
  not its data, and goes in the next major release. Read `data`.
- The shell's own answer to a `payment_success` message (a notification and
  `loadTariffsData()`) is product logic in the framework; it stays for this
  release and is removed in the next minor one. Listen to `keepup:message` in
  the application instead.

## 0.2.0 — 2026-09-27

A release about carrying load on several replicas: one way into the database,
no query on the event loop, caches for what every page asks for, sweeps for
the journals, and correct counting and publishing across replicas.

**Upgrading from 0.1.1.** `keepup.db.DatabaseManager` is gone — move its calls
to `DatabaseManagerV2` (see *Removed*). Names that moved to their own modules
(`keepup.metrics_api`, `keepup.events_api`, `keepup.log_shipping`) still answer
from the old ones with a `DeprecationWarning`. `max_upload_bytes` is enforced
now: a route that takes large bodies through the framework's JSON reading
should declare `max_body_bytes`; routes that read their own body
(`is_upload`, `raw_request`) are not limited unless they declare one.

### Fixed

- **A body whose content type carried a charset was lost entirely.** The header
  was compared as a whole string, so `application/json; charset=utf-8` and
  `Application/JSON` were not JSON — and a media type is case-insensitive and
  carries parameters by the standard, which most clients write by default. Such
  a client lost its body on every request and read the answer as a missing
  field. The media type is now parsed rather than compared.
- **An unreadable body no longer looks like an absent one.** `{}` meant three
  things at once — there was none, the type was not JSON, or it arrived and
  could not be read — and a handler that cannot tell them apart answers about a
  field the client did send. A body declared as JSON that does not parse is now
  a warning naming the route, the method, the content type, the length and the
  reason. The body itself is never written: passwords go through these routes.
  A request with no body stays silent, and the handler is still handed `{}` and
  decides for itself — refusing instead would have turned hundreds of routes of
  every application into refusals in one release.
- A media type with a `+json` suffix is still not read — a route declares what
  it accepts — but it is named in the log instead of vanishing quietly.

- **The notification bus no longer loses envelopes published at once.** It
  published on the connection it listens on, and asyncpg runs one operation at
  a time there: the second of two simultaneous envelopes failed with "another
  operation is in progress" and was dropped without a trace. It publishes
  through the pool now — `pg_notify` needs no `LISTEN` — so publishing also
  works while the listener reconnects (`can_publish`), and `is_running` turns
  true only once the listener is attached.
- **A task holding a `distributed_lock` could not be cancelled while the lock
  was winding down its renewal.** The exit awaited the renewal task under
  `except CancelledError: pass`, which swallowed the holder's own cancellation
  too: a background loop went back to its sleep, and a shutdown waited for it
  forever. The holder's cancellation now propagates, and the lock is released
  either way.
- Event statistics (`/api/events/stats`) read rows by position, which a
  PostgreSQL row does not have; they are read by column name now.

### Added

- A project page: `docs/index.html`, one static page for GitHub Pages to serve
  from this branch. Until now there was no address to give somebody who has not
  heard of the package — the README speaks to a reader already in the
  repository, and the index page needs the name `keepup-admin` to be found at
  all. What the page repeats after the metadata is checked against it, not kept
  in step by hand. `[project.urls]` names four places instead of two, so a
  reader who arrives from the index has somewhere to go.
- `DatabaseManagerV2.raw_connection()` — a driver connection out of the pool,
  for code written against a cursor (the application's table hook receives one
  such cursor); `close()` hands it back.
- `AuthProvider.lookup_user(username)` — the synchronous read of an account,
  for callers that cannot await; the local provider reads its table. The
  default keeps a provider that only has `get_user_info()` working.
- The plugin route key `max_body_bytes`: the largest body the route accepts,
  or `None` for no limit.
- `keepup.scheduler.new_scheduler()` and `JOB_DEFAULTS`, and
  `KeepupSettings.scheduler_job_defaults` to override them.
- `BasePlugin.post_construct_once_per_cluster` and
  `post_construct_quiet_seconds`: a plugin whose post_construct is the
  deployment's work rather than the replica's runs it on one replica of a
  rollout.
- `keepup.cache` — per-replica caches with a lifetime, dropped locally at once
  and on the other replicas over the notification bus; and
  `PerformanceSettings.catalogue_cache_seconds`.
- `DatabaseManagerV2.execute_async()`, `execute_one_async()`,
  `execute_commit_async()`, `execute_many_async()`,
  `execute_commit_returning_async()` — the same queries, awaitable: the call
  runs in a worker thread.
- `DatabaseManagerV2.test_connection()` — the health check's answer (whether
  the database responds, which one, its version), which never raises.

### Changed

- **`users.role` is deprecated: it mirrors the role set** — `ADMIN` when that
  role is held, otherwise the first role granted, which is the answer every
  reader of the field actually asked for. It is written where the set is
  written and no decision is taken from it; `PATCH /api/admin/users/{id}` with
  a `role` replaces the whole set, because "this person is a client now" must
  not leave `ADMIN` behind. Read `roles` instead: the field goes away in 0.3.0.
  An account whose set has never been filled in reads as the one role its
  mirror names, and the start fills the sets from that column, so an existing
  database loses nothing.
- **`max_upload_bytes` is enforced** — it was declared and checked nowhere. A
  body over the limit is refused with 413 before the route reads it (at once
  when `Content-Length` says so, as it arrives otherwise). It applies to
  routes whose body the framework reads; a route that reads its own body
  (`is_upload`, `raw_request`) is limited only by what it declares.
- **Failed sign-ins arriving at once are all counted.** The count was read,
  incremented and written back, so failures landing together on several
  replicas counted as one and the lockout came later than
  `LOGIN_MAX_ATTEMPTS` said. It is one upsert statement now, the same on
  PostgreSQL and SQLite, window reset included.
- **The event log sweeps itself.** Only a manual route ever deleted old events,
  so the table grew for as long as the deployment ran. Events older than
  `EVENTS_RETENTION_DAYS` (90 by default) are deleted hourly, in chunks, under a
  distributed lock; the request audit and the event log share one chunked
  delete (`keepup/retention.py`).
- **Scheduler jobs coalesce missed runs, never run beside themselves, and may
  start up to a minute late** (`coalesce`, `max_instances = 1`,
  `misfire_grace_time = 60`). APScheduler's defaults ran every missed slot
  separately and dropped a run that was a second late. The framework's
  scheduler is created before the plugins initialise, so one that registers
  jobs then finds it; it is still started after them.
- **The active theme and the panel's section catalogue are no longer read on
  every request.** Each replica keeps them for `catalogue_cache_seconds` (30 by
  default); the replica that changes them drops its copy at once and tells the
  others over the notification bus when the application runs one. Caches are
  empty when the server starts taking requests. The theme cache the service
  used to write and never read is gone.
- **No query to the database holds the event loop.** The audit flush,
  distributed locks, the event log, the panel section catalogue, the health
  check, sign-in, the cluster heartbeat, metrics and the plugin panel ran
  blocking queries inside coroutines: while one waited for the database, the
  process served nobody. Coroutines now await the new `*_async` methods or hand
  their blocking helpers to a worker thread, and endpoints that only query are
  plain functions, which FastAPI runs in its thread pool. The audit flush holds
  the buffer lock only while it picks rows, not while it writes them. Sign-in
  checks the password (bcrypt) off the loop as well.
- **The user check on every signed-in request reads the account once, off the
  event loop.** It read the account twice, and each read started a thread with
  an event loop of its own that the request waited for with a blocking join —
  holding the loop every request needs. The session check and the account read
  now run together in one `asyncio.to_thread` hop, on pooled connections; a
  revoked session ends the check before the account is read. Answers do not
  change: 401 for a bad token, a revoked session or a removed account, 403 for
  a blocked one.

- **Shipping logs to a collector is a module of its own.** `keepup.logging_setup`
  is the console and rotated files; `keepup.log_shipping` is the queue, the
  thread, the retries and the collector registration, and an application that
  names no collector never loads it. `logging_setup.configure()` remains the
  one entry for logging values and hands the collector's on. The shipping
  names still answer from `keepup.logging_setup`, with a `DeprecationWarning`.
- **The event log and its HTTP routes are separate modules.** `keepup.events`
  is the journal (table, `emit_event`, reading, retention) and loads no web
  framework; `keepup.events_api` holds the routes and their models, and looks
  the journal's manager up at each call. `register_event_api_routes` and the
  models still answer from `keepup.events`, with a `DeprecationWarning`.
- **Collecting metrics and handing them out are separate modules.**
  `keepup.metrics` collects and writes; `keepup.metrics_api` serves `/metrics`
  and the panel's summary. The names that moved — `register_metrics_routes`,
  `PANEL_METRICS`, `fresh_since` and the rest — still answer from
  `keepup.metrics`, with a `DeprecationWarning` naming the new place.

### Removed

- **`keepup.db.DatabaseManager`, the legacy database layer.** It opened a new
  driver connection on every call, outside the pool, took positional `?`
  parameters and rewrote them for PostgreSQL by string replacement — and a
  batch write of it left its connection open. Every part of the framework now
  goes through the pooled `DatabaseManagerV2`, with named parameters. An
  application still calling the old class moves its statements to
  `DatabaseManagerV2` (`execute`, `execute_one`, `execute_commit`,
  `execute_commit_returning`, `execute_many`); code written against a cursor
  takes one from the pool with `raw_connection()`.

## 0.1.1 — 2026-09-24

A security release. Do not use 0.1.0: its dependency ranges made a safe
installation impossible.

### Fixed

- **The cap on Starlette held installations on a version with four published
  advisories.** `starlette>=0.52,<1` was ours, while FastAPI asks only for
  `>=0.46`, and the fixes for PYSEC-2026-248, -249, -2280 and -2281 are in the
  1.x line — which our own cap excluded. There was no way to make an
  installation of 0.1.0 clean. The range is now `>=1.3.1,<2`; the only code it
  needed was registering socket routes as a plain `WebSocketRoute`, because
  Starlette 1.0 dropped `add_websocket_route` and FastAPI's
  `add_api_websocket_route` refuses a handler whose socket parameter carries no
  annotation.

- **python-jose replaced by PyJWT**, which removes `ecdsa`, `rsa` and `pyasn1`.
  `ecdsa` carries PYSEC-2026-1325, an advisory with no fixed version at all, and
  python-jose required it unconditionally. The path to it was reachable: the
  framework signs its own sessions with HS256, where `ecdsa` takes no part, but
  sign-in through a provider allows ES256, ES384 and ES512 — the curves `ecdsa`
  implements. Nothing about sign-in changed: not the algorithms, not the
  lifetimes, not which tokens are refused.

### Changed

- A provider's signing key is now built from its JWK description as a step of
  its own, so a description that cannot be read is refused in those words
  rather than reported as a signature that did not match.
- `cryptography` is a real dependency, brought by `pyjwt[crypto]`.

### Added

- The repository checks itself: the suite on 3.11, 3.12 and 3.13 with a coverage
  floor, lint that blocks on real errors, `pip-audit` weekly as well as on
  changes, CodeQL, a build that installs the wheel somewhere with nothing of
  ours around it, Dependabot, and a release on a tag through Trusted Publishing.
- A `test` extra, declaring what the suite needs beyond the package. The suite
  now runs in a clone of this repository — install first (`pip install -e
  ".[test]"`), then `pytest tests/`.

## 0.1.0 — 2026-09-23

The release that makes the framework a package: something that installs
somewhere else and works there, rather than a directory that happens to sit
next to an application.

### Added

- Apache 2.0, with `NOTICE` beside it. The package is published so that people
  outside this repository can build on it, and "all rights reserved" on a
  public index would have said the opposite: readable by everyone, usable by
  nobody. `THIRD-PARTY.md` carries the MIT notices of the five front-end
  bundles the panel ships, whose headers minification had removed.

- Package metadata of its own (`pyproject.toml` inside the package), its own
  dependency list and a version read from the distribution.
- The panel shell as package data: theme pages, the panel script and the
  stylesheets, served by the framework at `/keepup-static`.
- Eight administrative panel sections — users, the section catalogue, the
  cluster, metrics, themes, events, background tasks and the integration log —
  declared and carried by the package, entered into the catalogue at start-up.
- A declared public interface: `__all__` on every module an application may
  import from, held to the applications by a test.
- The framework's own test suite (`ci/tests/keepup.sh`), which needs no
  application, and a coverage floor.
- A distribution boundary held by tests: English only, no credentials, no
  deployment addresses, no application names in the package.
- Retention for the audit table, which previously grew without bound.
- Renewal for distributed locks, so a job that runs longer than the lock's life
  is not overtaken by another replica.
- A request mask per route: a route declares what it accepts, and the framework
  refuses the rest before the handler is called.

### Changed

- Defaults on the outside edge now close rather than open: no allowed origins,
  metrics behind credentials, security headers on responses, and the audit
  keeping field names instead of values.
- The collector address, its token, the application's name and its description
  come from the application; the framework carries none of them.
- The token lifetime and the signing algorithm are read from the deployment's
  authentication configuration, which until now was parsed and ignored.
- The password rule applies wherever a password is set, not only at
  registration.
- Blocking an account revokes its sessions.
- A path taken from data cannot leave the front-end directory, checked both
  where it is written and where it is served.

### Removed

- The route that called any plugin handler by name over HTTP: that mechanism is
  how plugins call each other inside the process, and publishing it made every
  such handler an endpoint open to anybody signed in.
- A switch in the signature of the dependency that identifies the user, which
  FastAPI published as a query parameter on every protected route.
- A legacy helper that opened a hard-coded SQLite file named after a customer
  of an earlier project.

### Fixed

- The audit recorded no outcome at all: the wrapper ended each request twice,
  and the second call wrote the status, the body and the error back to nothing.
- Sockets of a plugin whose initialisation failed were still registered.
- A parameter of the path could be overridden by one of the query string.
- An unknown query parameter answered with a server error instead of refusing
  the request.
