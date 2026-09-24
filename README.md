# keepup

[Project page](https://fedorsergeev.github.io/keepup-admin/) ·
[Package](https://pypi.org/project/keepup-admin/) ·
[Changes](CHANGELOG.md)

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
pip install -e ".[test]"
pytest tests/
```

**Install first, then test.** Pointing pytest at the checkout does not work on
its own: `import keepup` has to resolve, and the directory a clone lands in is
called `keepup-admin`, so nothing is named `keepup` until the package is
installed. The `test` extra carries what the suite needs beyond the package —
notably `cryptography`, which the framework itself never imports and the OIDC
tests sign tokens with.

The suite runs against SQLite with no network, needs no application beside the
package, and holds a coverage floor declared in `pyproject.toml`. Checks that
genuinely need a consumer of the framework find one by trait and skip with a
reason when there is none, so a skip here always says what is missing.

Every one of these runs on push and on a pull request; see `.github/workflows/`.
`security.yml` also runs weekly, because that check goes red without anybody
touching the repository — an advisory gets published against a version that was
fine yesterday.

## Licence

Apache 2.0. See `LICENSE`, and `NOTICE` for the attribution that clause 4(d)
asks to travel with a derivative.

The panel's front end also ships five third-party bundles -- Tailwind, Feather,
AOS, Chart.js and its date adapter. All five are MIT and stay under their own
terms; minification stripped their headers, so their notices live in
`THIRD-PARTY.md`.
