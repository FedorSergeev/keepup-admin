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
