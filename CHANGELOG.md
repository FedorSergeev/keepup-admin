# Changelog

Notable changes to `keepup-admin`.

## Unreleased

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
