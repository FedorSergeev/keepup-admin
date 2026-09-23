# keepup

An admin framework over FastAPI. You give it settings; it gives you an
application with a panel, a plugin runtime, users, a database layer, an audit,
metrics and coordination between replicas already wired.

```python
from keepup import KeepupSettings, create_app

app = create_app(KeepupSettings(title="Your product", project_name="yours"))
```

That is a running application with a working panel at `/selfcare`: the shell,
the themes and eight administrative sections ship inside the package, so there
is nothing to copy into your tree to see them.

Installed as `keepup-admin`; imported as `keepup`. The name `keepup` on PyPI
belongs to an unrelated project from 2015 that installs a script and no module,
so the import name is free.

## What it gives you

| | |
|---|---|
| **Application assembly** | `create_app(settings)` — FastAPI, middleware, lifespan, static mounts |
| **Plugin runtime** | features as plugins; routes are data, not decorators |
| **Panel** | the shell, visual themes, and the catalogue of sections with role grants |
| **Users** | local accounts, roles, panel sessions, sign-in through an external provider |
| **Database** | a SQLAlchemy layer with a pool, table declarations for two dialects |
| **Replicas** | distributed locks, leader election, a message bus, a cluster registry |
| **Observability** | audit of incoming requests, an event log, metrics, log shipping |

## What it does not decide for you

The framework carries no product. Anything it cannot know arrives through
`KeepupSettings`: your name and version, where your logs are shipped, which
fields of a request are secret, what a password must look like, which tables
belong to you, which origins may read your answers.

Where a default would have to guess, it closes rather than opens. Metrics are
not served without credentials, no origin is allowed cross-origin, the audit
keeps field names and not values, and there is no collector address, no signing
key and no password anywhere in the source. An application says what it needs
open; the package does not decide that on its behalf.

## What is public

A module that an application may import from declares `__all__`. Everything
else is internal and may change without notice — including any name starting
with an underscore, whatever module it sits in.

`keepup/tests/public_interface_tests.py` holds this to the applications in the
repository: a name taken out of the framework and not declared public fails the
run, and the failure says what to do about it.

## Requirements

Python 3.11 or newer. PostgreSQL in production; SQLite works and is meant for
development. `pip install keepup-admin[postgres]` adds the driver the bus
between replicas needs.

## Tests

```bash
ci/tests/keepup.sh          # the framework's own suite, needs no application
```

The suite runs against SQLite with no network and holds a coverage floor. The
plugin runtime is the least covered part of the framework, and deliberately so
for now: the test that exercises it hardest stands up a real application's
plugins and therefore lives with that application. See `doc/keepup.md`.

## Licence

Apache 2.0. See `LICENSE`, and `NOTICE` for the attribution that clause 4(d)
asks to travel with a derivative.

The panel's front end also ships five third-party bundles -- Tailwind, Feather,
AOS, Chart.js and its date adapter. All five are MIT and stay under their own
terms; minification stripped their headers, so their notices live in
`THIRD-PARTY.md`.
