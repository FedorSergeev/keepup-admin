# Which capability owns which table

Every table of the framework belongs to exactly one capability, and that
capability declares it -- it never creates one itself, and the abstraction
creates what it is handed (keepup-106). This is the map keepup-124 moves by: a
declaration leaves the kernel's `schema.py` together with the module that keeps
it, and the distribution named here is where it goes.

| Table | Owner | Why |
| --- | --- | --- |
| `users` | `keepup-users` | accounts, and what everything else points at |
| `user_roles` | `keepup-users` | the set of roles an account holds |
| `user_permissions` | `keepup-users` | the rights granted to an account |
| `external_role_mappings` | `keepup-users` | what a foreign system's roles mean here |
| `integration_logs` | `keepup-integration-log` | calls this application makes outside |
| `system_metrics` | `keepup-metrics` | the snapshots the panel draws |
| `distributed_locks` | `keepup-tasks` | one job at a time across replicas |
| `cluster_members` | `keepup-cluster` | which replicas are alive |
| `cluster_commands` | `keepup-cluster` | what one replica tells another to do |
| `frontend_modules` | `keepup-ui` | the section catalogue the panel keeps |
| `role_modules` | `keepup-ui` | which role may see which section |
| `plugin_overrides` | `keepup-modules` | an administrator's decision about a plugin |
| `integration_logs` | `keepup-integration-log` | moved in keepup-124 |
| `users` | `keepup-users` | moved in keepup-124 |
| `user_roles` | `keepup-users` | moved in keepup-124 |
| `user_permissions` | `keepup-users` | moved in keepup-124 |
| `external_role_mappings` | `keepup-users` | moved in keepup-124 |
| `system_metrics` | `keepup-metrics` | moved in keepup-124 |
| `auth_session` | `keepup-auth` | declared by the capability since keepup-116 |
| `login_attempts` | `keepup-auth` | declared by the capability since keepup-116 |
| `incoming_requests` | `keepup-audit` | declared by the capability since keepup-111 |
| `app_events` | `keepup-audit` | declared by the capability since keepup-111 |
| `visual_themes` | `keepup-ui` | declared by the capability since keepup-104 |

Three tables do not appear here because they are not in the kernel's schema at
all: the panel's sessions (`auth_session`) and login attempts
(`login_attempts`) are declared by `keepup-auth` (keepup-116), and the incoming
requests and application events are declared by `keepup-audit` (keepup-111) --
each of them already next to the code that keeps it.

`keepup-tasks`, `keepup-cluster` and `keepup-modules` are capabilities of 0.5.0
(keepup-117, keepup-118, keepup-121): their tables stay in the kernel until the
capability that owns them exists, which is why a table's owner is named here
before the distribution that will hold it is written.
