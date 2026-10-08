# The distributions

A deployment installs `keepup-admin` and then the capabilities it wants. The
constructor, the catalogue and the service registry are what the base package
is; everything that needs a library the base must not carry travels in a
distribution of its own, and each declares the plugin it brings in the
`keepup.plugins` entry-point group.

| Distribution | Provides | Brings |
| --- | --- | --- |
| `keepup-db` | `datasource` | sqlalchemy>=2.0,<3 |
| `keepup-postgres` | `datasource_driver` | psycopg2-binary>=2.9, asyncpg>=0.29 |
| `keepup-sqlite` | `datasource_driver` | nothing beyond the abstraction |
| `keepup-auth` | `auth, permissions` | bcrypt>=4.0,<5, pyjwt[crypto]>=2.15,<3 |
| `keepup-users` | `users` | nothing beyond the abstraction |
| `keepup-ui` | `ui` | nothing beyond the abstraction |
| `keepup-audit` | `audit, events` | nothing beyond the abstraction |
| `keepup-metrics` | `metrics` | prometheus-client>=0.24, psutil>=7.0 |
| `keepup-integration-log` | `integration_log` | nothing beyond the abstraction |

## The graph points one way

`keepup-admin` (the constructor, nothing else) <-- `keepup-db` (SQLAlchemy and
the table language) <-- `keepup-postgres` or `keepup-sqlite`. A capability
depends on the abstraction and declares what it provides; it never imports
another capability, and an application reaches both through the settings and the
service registry.

## What the base package stops carrying

In 0.4.0 the base still contains the code of the capabilities, so it still
depends on what that code imports: `sqlalchemy`, `psycopg2-binary`, `bcrypt` and
`pyjwt[crypto]`. Each of them leaves `keepup-admin` in keepup-124, when the code
that needs it moves into the distribution above -- and the acceptance of that
change is what this table promises: `keepup-admin` plus `keepup-postgres` is a
deployment that talks to PostgreSQL, and neither of them is installed with a
library it does not use.

A deployment that wants everything installs the capabilities it names; a
deployment that wants a socket and a metrics endpoint installs `keepup-admin`,
`keepup-db`, its driver and `keepup-metrics`, and gets no panel, no sign-in and
no bcrypt.

## Which modules keep a library in the base

A library leaves the base package when the last module that imports it has moved
into its distribution. This is that list, computed from the code: when a module
moves, its name has to leave this table in the same change, and the day a row is
empty, the library comes out of `pyproject.toml` -- and the check written for
that day (`tests/base_package_freedom_tests.py`) stops finding it.

A library with no module in the base that imports it is a *run-time* need of
something the base does: `psycopg2` is the driver SQLAlchemy reaches for when it
connects, named in a connection string rather than in an import. It leaves with
the module that builds that string.

| Library | Its distribution | The base modules that still import it |
| --- | --- | --- |
| `sqlalchemy` | `keepup-db` | `auth/login_throttle.py`, `auth/user_roles.py`, `events.py`, `schema.py` |
| `psycopg2` | `keepup-postgres` | — no module imports it (see below) |
| `bcrypt` | `keepup-auth` | `auth/dependencies.py`, `auth/providers/base.py`, `auth/providers/local.py`, `auth/seed_accounts.py`, `auth/user_routes.py` |
| `jwt` | `keepup-auth` | `auth/dependencies.py`, `auth/oidc.py`, `auth/panel_session.py`, `auth/providers/base.py`, `auth/providers/local.py`, `auth/routes.py` |

## The units that still move, and why they move whole

The table above says which modules keep a library in the base. They do not move
one by one, and the sizes say why: a unit moves as a whole because its files
import each other, and moving one of them would leave the same package in two
homes at once.

| Unit | Its distribution | Size | Why it moves whole |
| --- | --- | --- | --- |
| `keepup/auth/` | `keepup-auth` | 20 files, ~6 000 lines | its modules import each other (`providers`, `dependencies`, `user_roles`), and it is one capability: sign-in, sessions, throttling, roles, OIDC, the routes of the panel's sign-in |
| `keepup/events.py` | `keepup-audit` | 495 lines, 10 importers | it is the event log the audit capability keeps; the event API, the administrator's trail and the panel's section reach it by name, and `keepup.events` stays as the compatibility name |
| `keepup/schema.py` | -- | 214 lines, 49 importers | it keeps the four declarations whose capabilities are 0.5.0 work (`doc/table-ownership.md`), and it is the path that predates the catalogue: it shrinks as those capabilities arrive, not before |
| `keepup/migrations.py` | -- | depends on `schema.py` | the same: it initialises what `schema.py` declares |

An attempt to move a single file out of `keepup/auth/` would be an attempt to make
`from keepup.auth import providers` resolve to two different packages; the honest
unit is the package. That is why this list is written down rather than discovered
by whoever tries next.

## The edges of the sign-in package, before it moves

`keepup/auth/` is the last unit of 0.4.0 that still moves, and it is the largest:
32 files that import each other by fifteen internal names. Its edges are surveyed
here so the move is a move rather than a discovery.

**What it reaches outwards** -- `keepup.instance`, `keepup.kernel`, `keepup.roles`,
`keepup.retention`-style helpers, the tables and the manager (through
`keepup_db`), and its own declarations (`keepup_auth.tables`). Everything else it
uses is inside the package: those fifteen internal names are why it moves whole.

**What reaches inwards** -- twenty modules of the base name it: the routes of the
panel and the API documentation, the plugin files of the sign-in and the accounts,
the cluster, locks, the scheduler, the section catalogue, metrics, the themes, the
notification bus, the integration log, the event API, `factory`, `settings`,
`schema`, `roles`, `compat` and the route runtime. Not one of them has to change:
`keepup.auth` keeps answering through the compatibility layer, name by name, which
is what the layer was built for and has now been used for three times.

**What the move has to carry** -- the four declarations it owns (already in
`keepup_auth.tables`), its own modules, and nothing of the base. The base's
modules that import it stay where they are until their own capabilities move.

## The runbook for the last move

The sign-in package is the last unit of 0.4.0 that moves, and its size is measured
rather than feared: **75 import lines** inside the package name it by its old path
(`from keepup.auth...`), **42 references** outside it do, and the package is 20
top-level modules plus the subpackages `dto/`, `identity/` and `providers/`. The 42
outside references do not change -- `keepup.auth` answers through the compatibility
layer -- so the move is the 75 lines and the files.

In this order, one change:

1. `git mv auth/* packages/keepup-auth/keepup_auth/` -- the package's contents
   become the distribution's, so `keepup_auth/` *is* the sign-in rather than
   holding a copy of it.
2. Merge the two `__init__.py`: the distribution's keeps its docstring and
   `__version__`, and takes the exports the package declared.
3. Rewrite the 75 lines: `from keepup.auth` becomes `from keepup_auth`,
   `import keepup.auth` becomes `import keepup_auth`. Nothing else in them changes.
4. Strike `auth/` from `CAN_BE_MADE_LAZY`-style lists -- the entries in
   `MOVES_WITH_ITS_CAPABILITY` for the files that moved -- and let the shrinking
   check confirm.
5. Checks that read a sign-in source read it by name already, or are named here:
   `grep -rn "auth/" tests/*.py` before starting, and convert each hit to
   `source_of("keepup.auth.<module>")`.
6. Full suite. If it is not green, `git checkout -- . && rm -rf
   packages/keepup-auth/keepup_auth/*` restores the state -- the move is a rename
   plus 75 lines, and a revert is one command.

The libraries leave in the same change: `sqlalchemy`, `bcrypt` and `pyjwt` have no
keeper left in the base once this package is out, and
`tests/base_package_freedom_tests.py` fails on purpose the moment one of them is
still declared and unused.

## In what order the move happens

A declaration or a module moves in one change, and the change is only complete
when all three of these are true -- which is why the first attempt at moving
`INTEGRATION_LOGS` was reverted rather than patched:

1. **The code is in the distribution**, and `keepup.schema` (or the module the old
   name pointed at) re-exports it from there.
2. **The name is removed from the base rather than re-exported, and the
   compatibility layer says what to install.** The base package declares *no*
   dependency on a distribution: every one of them depends on `keepup-admin` for
   the kernel it is loaded by, so the reverse edge would point both ways and
   neither could be installed alone. A deployment installs the capabilities it
   names, and an application that imports a name whose capability is not
   installed is told so -- `keepup.db moved to keepup_db: install keepup-db` --
   rather than wondering about a missing module. In the repository the
   `packages/*` directories are on the suite's `pythonpath`, which is a test
   arrangement and not an installation.
3. **The compatibility layer is told**, so the old import path keeps working and
   says where the name went (keepup-114), and the check that measures the debt
   (keepup-124, `tests/base_package_freedom_tests.py`) is updated in the same
   change: it fails on purpose the day a library leaves the base.

So the move of a capability's code and the removal of its library from the base's
`dependencies` are one change, not two, and the entry in `pyproject.toml` is where
the two meet.

## What trying to move a module found

Moving `keepup.db` into `keepup-db` (keepup-124) was attempted twice and reverted
both times. Each attempt turned an unknown into a named piece of work; none of it
is about the module being moved, which transfers cleanly.

1. **The stand-in answers for the declaration, and answers lazily.** Two of the
   first attempt's failures were the compatibility layer's own: it was created
   empty, so the public-interface check found a module declaring nothing and
   reported every name taken from `keepup.db` as undeclared (keepup-125), and it
   resolved its destination eagerly, which dragged the distribution into an
   import that only mentioned the database (keepup-126). Both are fixed and
   checked.
2. **Importing the framework must not load a database.** What is left is real
   code: `keepup.events`, `keepup.log_shipping`, `keepup.metrics` and
   `keepup.themes` import the manager at module level, so with the module moved,
   importing any of them loads the distribution. Four checks refuse exactly that
   today -- `events_split_tests.py`, `log_shipping_split_tests.py`,
   `metrics_split_tests.py`, `themes_tests.py` -- and they are right: a release
   whose point is that the base carries no database library cannot have an import
   that reaches for one. Those imports move inside the functions that use them
   (keepup-127).
3. **Checks that read the moved module by path follow the name.** Four checks
   parse `db.py` or measure what it exports; they are mechanical and belong to
   the move itself (keepup-125): `db_leftovers_tests.py`,
   `leftovers_tests.py`, `one_database_manager_tests.py`, and the compatibility
   check that knows which names have moved.

## How a payment is made, and how it is not

Paying down the import debt (`keepup-127`) has a shape that works and one that
does not, learned the hard way twice.

What works: one module, read by hand. Its database use is found, the import moves
into the function that uses it, the entry is struck off the list in the same
change, the checks that cover the module are run, then the whole suite. Three
payments were made that way -- `retention.py`, `metrics_retention.py`,
`integrations.py` -- and all three hold.

What does not work: a script that rewrites several modules at once. A batch of
five crashed halfway through its first file and left an edit without its other
half; a single-file AST rewrite of `web.py` inserted the import somewhere the file
must not have it and produced a syntax error that broke the collection of a
hundred and sixty checks. Both were reverted whole, and nothing was committed.
The lesson is not that tooling is bad: it is that an edit which cannot be
finished and *seen* must not be started, and these modules are where the release
is least tolerant of a half-done change.

## What is left, and in what order

Two tasks of 0.4.0 remain, and they are one piece of work in two halves. The order
between them is not a preference: it was forced twice by a check refusing a
half-done change.

**First, `create_app` is assembled through the runtime** (keepup-123). The old
path builds the application itself: it creates the tables of `keepup.schema` from
a list inside `init_db`, registers the routes of every framework module and loads
the application's plugins through the old manager. Until that path is the runtime
-- catalogue, profiles, two phases, contributions -- three things stay impossible:

* a table cannot be created by the capability that declares it, because nothing
  asks a plugin for its declarations on that path;
* a declaration cannot leave `keepup.schema`, because the old path would stop
  creating it (this is what the ownership map's rows are waiting for);
* `sqlalchemy`, `psycopg2-binary`, `bcrypt` and `pyjwt` cannot leave the base
  `pyproject.toml`, because the modules that import them are still modules of it.

**Then the declarations move to their owners and the libraries leave**
(keepup-124). `doc/table-ownership.md` names the destination of every declaration;
each move is one declaration, its owner, the module that reads it, and the entry
in `tests/lazy_database_import_tests.py` -- and the last one deletes the debt
check in `tests/base_package_freedom_tests.py`, which fails on purpose the day a
library leaves the base.

## What the attempts taught, so the next one does not repeat them

* A file of a capability is not a client of it: the plugin file of `keepup-db`
  imports its distribution directly *once that distribution holds the code*
  (keepup-123). Until then it goes through the compatibility layer, and the
  packaging check enforces the accounting either way.
* A base package that imports a distribution declares a dependency nobody can
  install: neither package could be installed alone, which is why the base
  declares none and the old names answer through `keepup.compat`.
* An import that moves breaks patch points: anything patching `module.Name` where
  the module no longer takes `Name` at import time has to patch the module that
  holds it. Two checks learned that the hard way; the stand-in forwards reads,
  writes and declarations, but it cannot invent an attribute a module never had.
* A change that cannot be finished and *seen* must not be started. Three attempts
  at moving a module and two at writing a dialect rule were reverted whole; no
  half-done edit was ever committed.
