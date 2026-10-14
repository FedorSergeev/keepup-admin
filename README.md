# keepup

[![tests](https://github.com/FedorSergeev/keepup-admin/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/FedorSergeev/keepup-admin/actions/workflows/tests.yml)
[![security](https://github.com/FedorSergeev/keepup-admin/actions/workflows/security.yml/badge.svg?branch=main)](https://github.com/FedorSergeev/keepup-admin/actions/workflows/security.yml)
[![PyPI](https://img.shields.io/pypi/v/keepup-admin)](https://pypi.org/project/keepup-admin/)
[![licence](https://img.shields.io/pypi/l/keepup-admin)](LICENSE)

[Project page](https://fedorsergeev.github.io/keepup-admin/) ·
[Package](https://pypi.org/project/keepup-admin/) ·
[Install](INSTALL.md) ·
[Changes](CHANGELOG.md)

Four badges, not six checks. These answer what somebody deciding whether to
install asks: does it run on my Python, is there a known vulnerability in what
it pulls in, what will I get, and may I use it. `quality`, `build` and `codeql`
are housekeeping and live on the Actions page -- six badges in a row stop being
read at all.

A badge shows; it does not require. What keeps a red check from being merged is
a branch rule, not a picture -- see `doc/keepup.md`.

An admin framework over FastAPI. You give it settings; it gives you an
application with a panel, a plugin runtime, users, a database layer, an audit,
metrics and coordination between replicas already wired.

```python
from keepup import KeepupSettings, create_app

app = create_app(KeepupSettings(title="Your product", project_name="yours"))
```

That is a running application with a working panel at `/selfcare` once the
capabilities are installed: the shell, the themes and eight administrative
sections ship with them, so there is nothing to copy into your tree to see them.

Installed as `keepup-admin`; imported as `keepup`. The name `keepup` on PyPI
belongs to an unrelated project from 2015 that installs a script and no module,
so the import name is free.

## Install

Python 3.11 or newer. PostgreSQL in production; SQLite works and is meant for
development.

```bash
pip install "keepup-admin[panel,postgres]"
```

That is the base plus the capabilities the panel profile enables and the
PostgreSQL driver. The base alone is the constructor:

```bash
pip install keepup-admin
```

— the kernel, the catalogue, the service registry, the lifecycle and the
transport seam, carrying no capability of its own. Each capability is an extra:
`db`, `auth`, `users`, `ui`, `audit`, `metrics`, `tasks`, `modules`, `cluster`,
`log_shipping`, `http`, `integration_log`, and one driver, `postgres` or
`sqlite` — one, because two drivers of one required service stop the start on
purpose.

`INSTALL.md` has the long form: every extra and what it brings, installing from
the repository, the build and the release.

## What it gives you

| | |
|---|---|
| **Application assembly** | `create_app(settings)` — FastAPI, middleware, lifespan, static mounts |
| **Plugin runtime** | features as plugins; routes are data, not decorators |
| **Panel** | the shell, visual themes, and the catalogue of sections with role grants |
| **Users** | local accounts, roles, panel sessions, sign-in through an external provider |
| **Database** | a SQLAlchemy layer with a pool, table declarations for two dialects |
| **Replicas** | distributed locks, a message bus, a cluster registry, performance settings in one place |
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

## Build

A release is one distribution per capability, and `build.yml` checks on every
change that they are publishable rather than discovering it at a tag:

```bash
python -m pip install --upgrade build twine

python -m build                          # the base, keepup-admin
for package in packages/*; do            # each capability
  python -m build --outdir dist "$package"
done

python -m twine check --strict dist/*
```

The same job then installs the built wheels into an empty virtual environment
and imports the framework there: a version on PyPI cannot be replaced, and a
wheel that installs and does not import is the one failure a metadata check
cannot see. The tag is the release's number and the job refuses one the metadata
disagrees with; see `.github/workflows/release.yml` and `INSTALL.md`.

## Tests

```bash
pip install -e ".[test]" -e packages/keepup-*
python -m pytest tests/ -q --cov --cov-config=pyproject.toml
```

**Install first, then test.** Pointing pytest at the checkout does not work on
its own: `import keepup` has to resolve, and the directory a clone lands in is
called `keepup-admin`, so nothing is named `keepup` until the package is
installed. The capabilities are distributions of their own in `packages/` —
each carries the libraries it needs, and the suite imports them — so a clone
installs them from the tree; the extras name the same ones for an installation
from the index.

The suite runs against SQLite with no network, needs no application beside the
package, and holds a coverage floor declared in `pyproject.toml`. Checks that
genuinely need a consumer of the framework find one by trait and skip with a
reason when there is none, so a skip here always says what is missing. A test is
named `*_tests.py` and run by path — `python -m pytest tests/runtime_tests.py -v`.

Every one of these runs on push and on a pull request, on Python 3.11, 3.12 and
3.13; see `.github/workflows/`. `security.yml` also runs weekly, because that
check goes red without anybody touching the repository — an advisory gets
published against a version that was fine yesterday. `INSTALL.md` carries the
heavier checks: the wheel installed into a throwaway environment, and the
security audit.

## Licence

Apache 2.0. See `LICENSE`, and `NOTICE` for the attribution that clause 4(d)
asks to travel with a derivative.

The panel's front end also ships five third-party bundles -- Tailwind, Feather,
AOS, Chart.js and its date adapter. All five are MIT and stay under their own
terms; minification stripped their headers, so their notices live in
`THIRD-PARTY.md`.
