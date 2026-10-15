# Installing, building and testing keepup

The long form of what `README.md` says in short: what a deployment installs,
how the distributions are built and how the suite is run. `keepup-admin` is one
distribution of several since 0.5.0 -- the base is the constructor, and every
capability that needs a library the base must not carry travels in a
distribution of its own -- so "installing keepup" is naming the capabilities
the stand uses.

Installed as `keepup-admin`; imported as `keepup`. The name `keepup` on PyPI
belongs to an unrelated project from 2015 that installs a script and no module,
so the import name is free.

## Requirements

Python 3.11 or newer. PostgreSQL in production; SQLite works and is meant for
development. The base package declares no capability of its own, and what a
capability needs is declared by the distribution that carries it.

## Install what the stand uses

The base is the constructor: the kernel, the catalogue, the service registry,
the lifecycle and the transport seam. It serves no panel and talks to no
database on its own.

```bash
pip install keepup-admin
```

A full admin panel, over PostgreSQL, is the base plus the capabilities the
panel profile enables plus one driver:

```bash
pip install "keepup-admin[panel,postgres]"
```

**One driver, not two.** `keepup-postgres` and `keepup-sqlite` both provide
`datasource_driver`, and two drivers of one required service stop the start on
purpose. A development stand over SQLite is `keepup-admin[panel,sqlite]`.

Each capability is an extra of its own. Installing the extra installs the
distribution, and the distribution declares the libraries it needs in turn:

| Extra | Distribution | What it provides | What it brings |
| --- | --- | --- | --- |
| `db` | `keepup-db` | the data source, the table language, the pool, the dialect registry | SQLAlchemy |
| `postgres` | `keepup-postgres` | the PostgreSQL dialect and the bus between replicas | psycopg2-binary, asyncpg |
| `sqlite` | `keepup-sqlite` | the SQLite dialect and an in-process bus | nothing beyond the abstraction |
| `auth` | `keepup-auth` | sign-in, sessions, roles, permissions, OIDC | bcrypt, PyJWT, PyYAML, pydantic |
| `users` | `keepup-users` | accounts and the roles they hold | nothing beyond the abstraction |
| `ui` | `keepup-ui` | the panel's own sections and themes | FastAPI |
| `audit` | `keepup-audit` | the audit of incoming requests, the event log | nothing beyond the abstraction |
| `metrics` | `keepup-metrics` | the collector, the metrics API and the loop that feeds it | prometheus-client, psutil |
| `tasks` | `keepup-tasks` | the scheduler | APScheduler |
| `modules` | `keepup-modules` | the section catalogue and its grants | nothing beyond the abstraction |
| `cluster` | `keepup-cluster` | the registry of replicas and the commands sent to them | nothing beyond the abstraction |
| `log_shipping` | `keepup-log-shipping` | the queue, the retries and the remote log | requests, urllib3 |
| `http` | `keepup-http` | the application, the routes, the panel shell | FastAPI, Starlette, pydantic |
| `integration_log` | `keepup-integration-log` | the log of what other systems asked | FastAPI, SQLAlchemy |
| `gateway` | `keepup-gateway` | one way in for the application's services: routes, a registry of services, proxy, balancer, sign-in at the edge | FastAPI, Starlette, httpx, SQLAlchemy |
| `panel` | -- | every capability the panel profile enables | the twelve capabilities above, not the two drivers and not the gateway |

`panel` names distributions, not libraries: pip resolves each of them and each
declares its own. A stand that wants a socket and a metrics endpoint names the
capabilities it uses and installs no panel section and no front end.

Names the base does not carry are not silently absent: the base's `create_app`
is a lazy wrapper and raises with the distribution to install when the transport
is not there -- `create_app needs the HTTP transport: install keepup-http
(pip install 'keepup-admin[http]')`.

## Install from the repository

The capabilities live in `packages/` and the suite imports them, so a clone
installs them from the tree. Editable, for working on the framework itself:

```bash
git clone https://github.com/FedorSergeev/keepup-admin
cd keepup-admin
python -m pip install -e ".[test]" -e packages/keepup-*
```

The `test` extra is the suite's own need beyond the package; the extras in the
table above name the same distributions for an installation from the index.
Editable installs do not need a build step.

## Build the distributions

A release is one wheel and one sdist per distribution, and `build.yml` checks on
every change that they are publishable rather than discovering it at a tag:

```bash
python -m pip install --upgrade build twine

python -m build                          # the base, keepup-admin
for package in packages/*; do            # each capability
  python -m build --outdir dist "$package"
done

python -m twine check --strict dist/*
```

The workflow then installs the built wheels into an empty virtual environment
and imports the framework there, because a wheel that installs and does not
import is the one failure a metadata check cannot see. Build from the checkout,
never from an installed copy: `pyproject.toml` at the repository root is the
base's metadata, and each `packages/*/pyproject.toml` is a distribution's.

### Release

A release is a `v*` tag. The tag is the number, and it is not taken on trust:
the release job holds every distribution's metadata to it and refuses a tag any
one of them disagrees with.

```bash
python .github/scripts/release_versions.py 0.5.0   # what the tag must say
```

The capabilities publish first, in parallel, and the base waits for all of
them: an installation that reads the base's extras must never be pointed at a
project the index does not have. Upload is authorised by Trusted Publishing, so
no token is stored; see `.github/workflows/release.yml` for the one-time setup
each project needs.

## Run the suite

**Install first, then test.** Pointing pytest at the checkout does not work on
its own: `import keepup` has to resolve, and the directory a clone lands in is
called `keepup-admin`, so nothing is named `keepup` until the package is
installed.

```bash
python -m pip install -e ".[test]" -e packages/keepup-*
python -m pytest tests/ -q \
  --cov --cov-config=pyproject.toml \
  --cov-report=term-missing:skip-covered
```

The suite runs against SQLite with no network, needs no application beside the
package, and holds the coverage floor declared in `pyproject.toml`. Checks that
genuinely need a consumer of the framework find one by trait and skip with a
reason when there is none, so a skip always says what is missing.

A test is named `*_tests.py` and run by path, which is the repository's
convention rather than pytest's:

```bash
python -m pytest tests/runtime_tests.py -v
```

The same commands run on push and on a pull request, on Python 3.11, 3.12 and
3.13 -- every version `requires-python` claims. See
`.github/workflows/tests.yml`.

### The heavier checks

Two checks are not part of the ordinary run, because they build, install or
start something real:

```bash
# build a wheel, install it into a throwaway venv, import the framework there
KEEPUP_PACKAGING_TESTS=1 python -m pytest tests/packaging_tests.py -v

# build applications on this tree and knock on every route they register
tests/security_audit_tests/run_security_audit.sh
```

The security audit writes a report; a route that answers without a sign-in must
be named in `PUBLIC_ROUTES` of `tests/security_audit_tests/route_sweep_tests.py`
with the reason, which is a decision rather than a fix for a red check. The
audit runs on push and weekly -- an advisory is published against a version that
was fine yesterday.

## Troubleshooting

- **`ModuleNotFoundError: No module named 'keepup'`** -- the package is not
  installed. Install it; being inside the directory a clone landed in does not
  put it on `sys.path`, because the directory is called `keepup-admin`.
- **`ModuleNotFoundError: No module named 'keepup_db'`** (or `keepup_auth`,
  `keepup_http`, ...) -- a module that moved in 0.4.0 is imported by the name of
  the distribution that holds it now, and the distribution is not installed.
  Install the extra that carries it, from the table above.
- **The start refuses with two drivers** -- `postgres` and `sqlite` are both
  installed. Keep the one the stand talks to.
- **A panel that serves no section** -- `keepup-http` and the capability of the
  section are both needed: a deployment that serves no HTTP carries no front
  end, and one that serves HTTP without `keepup-modules` has no catalogue.
