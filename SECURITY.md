# Security policy

keepup is an admin framework: a vulnerability here travels into every
application built on it. The maintainers treat that as a release blocker, not a
backlog item.

## Supported versions

The newest minor line receives security fixes. An older line gets a fix only
when the same change applies without a migration.

| Version | Supported |
|---|---|
| 0.6.x | yes |
| 0.5.x | critical fixes only |
| older | no |

## Reporting a vulnerability

**Do not open a public issue.**

Use GitHub's private advisory flow:

<https://github.com/FedorSergeev/keepup-admin/security/advisories/new>

If that is unavailable, write to the maintainer at the address in `pyproject.toml`
(`authors`).

Please include:

- the version and distribution (`keepup-admin`, `keepup-auth`, ...);
- what an attacker gains, and the smallest reproduction you have;
- whether the route is reachable without signing in;
- any proof of concept, log line or configuration that matters.

Do not include live secrets, other people's data, or a working exploit against a
deployment you do not own.

## What happens next

| Step | Target |
|---|---|
| Acknowledgement | 2 business days |
| Triage and severity | 5 business days |
| Critical fix | 7 days |
| High fix | 30 days |
| Medium fix | 90 days |
| Public advisory | with the fix, or 90 days after the report |

A critical issue is one that gives an unauthenticated attacker code execution,
administrator access, or every tenant's data. The clock starts when the report
arrives, not when a fix is convenient.

The fix ships as a release of every affected distribution, with an advisory that
names the versions and the workaround. Credit is given unless you ask otherwise.

## Out of scope

- A deployment that ignored the documented requirements: a placeholder
  `SECRET_KEY`, `MFA_REQUIRED_FOR_ADMINS` off where the deployment's policy
  requires it, or an application's own plugin route that declares no
  `permission`.
- Denial of service by volume against a deployment that put no proxy or rate
  limit in front of it.
- Findings that require an already-compromised host, database or signing key.

## For deployments

The framework closes by default, but a deployment still owns its edge: TLS in
front, `SECRET_KEY` and `SECRET_KEY_PREVIOUS` managed as secrets, an identity
provider with a second factor where one exists, backups that are encrypted and
restored on a schedule, and alerts on refused sign-ins and on
administrator changes in the audit.

See `INSTALL.md` and `doc/` for the settings that make a stand hard.
