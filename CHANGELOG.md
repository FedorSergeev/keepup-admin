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

### Added

- A project page: `docs/index.html`, one static page for GitHub Pages to serve
  from this branch. Until now there was no address to give somebody who has not
  heard of the package — the README speaks to a reader already in the
  repository, and the index page needs the name `keepup-admin` to be found at
  all. What the page repeats after the metadata is checked against it, not kept
  in step by hand. `[project.urls]` names four places instead of two, so a
  reader who arrives from the index has somewhere to go.

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
