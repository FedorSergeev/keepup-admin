# keepup-audit

Incoming calls, the application's events, the administrator's trail and their retention.

One capability of [keepup](https://github.com/FedorSergeev/keepup-admin), the framework over FastAPI for admin
panels: the base is installed as `keepup-admin` and imported as `keepup`, and
this distribution carries one part of it. It is installed on its own
(`pip install keepup-audit`) or together with the base (`pip install "keepup-admin[panel]"`),
and it declares itself to the kernel through its
`[project.entry-points."keepup.plugins"]` entry point.
