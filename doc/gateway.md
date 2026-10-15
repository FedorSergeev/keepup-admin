# The gateway

`keepup-gateway` is one way in for the services of an application. Every request
comes to the application first; the gateway finds the route it is addressed to,
checks the sign-in and the right at the edge when the route asks for them, and
passes the request on -- to a microservice, a subsystem that runs as a process of
its own, or any other HTTP address. It knows nothing about what stands behind an
address: it routes, it balances, and it tells the service who is calling.

```bash
pip install "keepup-admin[gateway]"
```

The plugin is `optional` and no profile enables it: a deployment that has
services beside the application names it in its plugins file.

## The table

The routes are the plugin's `config`:

```json
{
  "id": "gateway",
  "enabled": true,
  "config": {
    "identity_secret": "${GATEWAY_IDENTITY_SECRET}",
    "routes": [
      {"id": "catalogue", "prefix": "/api/catalogue",
       "upstreams": ["http://catalogue-1:8000", "http://catalogue-2:8000"],
       "strip_prefix": true},
      {"id": "orders", "prefix": "/api/orders", "upstreams": "http://orders:8000",
       "auth": "session", "methods": ["GET", "POST"], "timeout": 10},
      {"id": "reports", "prefix": "/api/reports", "upstreams": ["http://reports:8000"],
       "permission": "reports.read", "max_body_bytes": 1048576}
    ]
  }
}
```

| Key | Meaning | Default |
| --- | --- | --- |
| `id` | the name in the report and in the log | the prefix |
| `prefix` | the path the route answers, matched on a segment boundary | required |
| `upstreams` | one address or a list; requests go to them in turn | required |
| `methods` | the methods the route opens; anything else is 405 | all seven |
| `strip_prefix` | send the service the path without the prefix | `false` |
| `auth` | `none`, or `session` -- only a caller the kernel recognises passes | `none` |
| `permission` | a right the caller must hold; implies sign-in | none |
| `timeout`, `connect_timeout` | seconds to wait for the answer to start, for a connection | 30, 5 |
| `cooldown` | seconds an address that failed is left out of the turn | 30 |
| `max_body_bytes` | the largest request body passed on; above it, 413 | 10 MB |
| `headers` | headers added to every request of the route | none |
| `application` | a keepup application stands behind it: the panel shows its card | `false` |

`identity_secret` is what the caller's headers are signed with; a whole value
`${NAME}` is read from the environment. A route that asks for sign-in or stands
for an application is refused without it.

**The table is checked at the start.** A route with the prefix `/` (it would
cover the panel and the framework's own routes), `..` in the prefix, no address,
an address that is not `http(s)`, an unknown kind of sign-in, an `X-Keepup-*`
header of its own, or a prefix another route already has leaves the plugin
failed, with the route named in the plugins' report (`GET /api/admin/plugins`),
and none of its routes is mounted -- a half-served table would answer some
prefixes and drop others without saying so.

**The longest prefix wins**, on a segment boundary: `/orders` covers `/orders`
and `/orders/7`, not `/orders-archive`. Each route is mounted as two declarations
of the HTTP transport's `raw_request` kind -- the prefix and everything below it
-- and the handler chooses the route again from the full path, so a nested route
wins whichever of the two the transport mounted first. There is no catch-all: one
mounted before the framework's routes would cover them, one mounted after would
never be reached.

## Passing on

- The method, the path (without the prefix when the route strips it), the query,
  the body and the headers go to the service -- except what describes the
  connection rather than the message (`Connection` and the headers it names,
  `Keep-Alive`, `TE`, `Upgrade`, ...), `Host` and `Content-Length`.
- The gateway adds `X-Forwarded-For` (appended to the client's), `X-Forwarded-Proto`,
  `X-Forwarded-Host`, `X-Forwarded-Prefix` and `X-Request-Id`. A request id the
  client sent is kept when it is short and plain; otherwise a new one is made. The
  same id is in the answer.
- The body is read whole, up to the route's limit, before an address is chosen:
  that is what makes a second address possible and what refuses an oversized body
  before any service sees it. The answer is streamed back as it arrives, with
  every header the service sent except the connection's -- `Set-Cookie` repeated
  stays repeated.

## Several addresses

A route's addresses take requests in turn. An address that took no connection,
did not answer in time, broke the connection, or answered 502, 503 or 504 is left
out for the route's `cooldown`; when every address is cooling, they are tried
anyway rather than the request refused.

A second address is asked only when that is safe. A connection that was never
made carried nothing, so any method moves on. A timeout, a broken connection or
an answer of 502/503/504 moves on only for GET, HEAD, OPTIONS, PUT and DELETE: a
POST or a PATCH may have been carried out before the service failed to say so,
and the client gets the failure instead.

The cooldowns are this replica's own, the way a balancer's always are. Each
replica finds out for itself whether it reaches an address; sharing that through
the database would turn every proxied request into a write.

The gateway's own refusals are JSON `{"detail": ...}` with the request id: 404
(no route), 405 (method not opened, with `Allow`), 413 (body over the limit), 401
and 403 (the edge), 502 (no address answered), 503 (nothing to try), 504 (timeout).

## Who is calling

A route with `auth: session` or a `permission` passes only a caller the kernel
recognises (`keepup.kernel.security` -- whatever the deployment put behind
`auth`), and asks the deployment's `permissions` about the right. A deployment
with no sign-in answers such a route 401, never lets it through. The gateway does
not import `keepup-auth`.

A service behind the gateway must not learn to read the panel's session, and is
not handed its cookie as a credential. The gateway passes the caller on as five
headers signed with `identity_secret`:

| Header | Value |
| --- | --- |
| `X-Keepup-User-Id` | the account's id |
| `X-Keepup-User` | its name |
| `X-Keepup-Roles` | its roles, sorted, comma-separated |
| `X-Keepup-Timestamp` | the moment, in seconds |
| `X-Keepup-Signature` | HMAC-SHA256 over the four values and `X-Request-Id`, one per line |

**Every `X-Keepup-*` header a client sends is cut, on every route**, signed in or
not: a header a service trusts must never be one a client can write. A service
checks the headers with the standard library alone:

```python
from keepup_gateway import IdentityError, verify_identity

try:
    caller = verify_identity(request.headers, secret, max_age=60)
except IdentityError:
    ...  # not from the gateway, altered, or too old
caller.id, caller.username, caller.roles
```

The secret is the gateway's, not the session signing key: a service that leaks it
must not be able to forge a panel session.

## The administrator

`GET /api/admin/gateway/routes`, under the right `gateway.admin`, answers the
table and the state of every address in the replica that answered: `live`, or
`cooling` with the seconds left and the last failure.

### The panel section

The plugin declares a section, **Gateway**, with two parts: the table with the
state of each address, and a card for every address of every route marked
`"application": true`. The catalogue imports a declared section without a grant;
an application gives it to its administrators in its own `config/modules.json`
(`"ADMIN"` role, module `gateway`) or an administrator does in the panel. The
section's script is served by the distribution itself, at `/keepup-gateway/gateway.js`
-- the panel's static directory belongs to `keepup-http` -- from the panel's own
origin, which is what its Content-Security-Policy allows. Every value a card shows
came from another service and is escaped; the markup carries no handler.

### The applications behind it

A headless subsystem -- the kernel, the HTTP transport and a product plugin, no
panel -- is shown the way the panel shows its own runtime: which plugins it runs,
which failed and why, whether it is up. It may have no `keepup-auth` and no
`keepup-modules`, so the panel's `/api/admin/plugins`, guarded by the panel's
sign-in, is not something to count on. Instead the subsystem installs
`keepup-gateway` too, with the same `identity_secret` and no routes:

```json
{"id": "gateway", "enabled": true, "config": {"identity_secret": "${GATEWAY_IDENTITY_SECRET}"}}
```

and answers `GET /api/gateway/self` -- its name, this replica, the framework's
version and its runtime's report of every plugin -- to the gateway's signed
identity of an administrator, and to nothing else: 401 for headers that are
missing, altered or old, 403 for an identity without the `ADMIN` role.

`GET /api/admin/gateway/applications`, under `gateway.admin`, asks every address
of every application route three questions at once, signed as the administrator
who opened the section, with a three-second timeout:

| Question | What the card shows |
| --- | --- |
| `/api/gateway/self` | the application, the replica, the plugins with their outcome and reason -- or why it could not be read (no `keepup-gateway` there, another secret, not an administrator) |
| `/api/health` | the status and the state the application reports |
| `/metrics` | the number of samples; a 401 here means the application's metrics are behind its own sign-in (`metrics_public`) |

An address that does not answer is a card that says so, not a page that waits.

## Not here yet

- WebSocket through the gateway.
- Applications that register themselves, and plugins of the same runtime served
  without a proxy -- the next change.
- Cooldowns shared between replicas.
- Rate limits at the edge.
- Routes another plugin declares rather than the file: the kernel's
  contribution points are a closed list, and a new one is a change of its own.
