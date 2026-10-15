# keepup-gateway

One way in for the application's services: a route table, a proxy, a balancer and the sign-in at the edge.

One capability of [keepup](https://github.com/FedorSergeev/keepup-admin), the framework over FastAPI for admin
panels: the base is installed as `keepup-admin` and imported as `keepup`, and
this distribution carries one part of it. It is installed on its own
(`pip install keepup-gateway`) or together with the base (`pip install "keepup-admin[gateway]"`),
and it declares itself to the kernel through its
`[project.entry-points."keepup.plugins"]` entry point.

Every request comes to the application first; the gateway finds the route it is
addressed to, checks the sign-in and the right when the route asks for them, and
passes the request on to a microservice, a subsystem of the application, or any
other address. A service behind the gateway learns who is calling from signed
`X-Keepup-*` headers and checks them with `keepup_gateway.verify_identity`.
