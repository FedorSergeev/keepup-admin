# Changelog

Notable changes to `keepup-admin`. Dates are when the change landed in the
repository; the package has not been published yet.

## 0.1.0 — unreleased

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
