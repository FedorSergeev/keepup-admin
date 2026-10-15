"""The gateway: one way in for the application's services.

Every request comes to the application first; the gateway finds the route it is
addressed to by the longest prefix, checks the sign-in at the edge when the route
asks for it, and passes the request on -- to one of the route's addresses in
turn, leaving out an address that failed. A service behind it learns who is
calling from signed headers it can check, and never from a header a client wrote.

    python -m pytest tests/gateway_tests.py -v
"""

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from keepup.kernel import security
from keepup_gateway import IdentityError, verify_identity
from keepup_gateway.balancer import Balancer
from keepup_gateway.identity import Caller, caller_of, sign_identity
from keepup_gateway.plugin import GatewayPlugin
from keepup_gateway.table import TableError, match, parse_table
from keepup_http.routes import register_declared_routes

SECRET = "shared-with-the-services"


class Upstreams:
    """Stand-in services: what each address answers, and what each was sent."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.seen = []

    def __call__(self, request):
        self.seen.append(request)
        host = request.url.host
        answer = self.answers.get(host)
        if isinstance(answer, Exception):
            raise type(answer)(str(answer), request=request)
        if callable(answer):
            return answer(request)
        status = answer if isinstance(answer, int) else 200
        return httpx.Response(status, json={"host": host, "path": request.url.path,
                                            "query": request.url.query.decode()})

    def hosts(self):
        return [request.url.host for request in self.seen]


def gateway(routes, upstreams, secret=None):
    """An application with the gateway's routes mounted, and the plugin."""
    config = {"routes": routes}
    if secret is not None:
        config["identity_secret"] = secret
    plugin = GatewayPlugin(config, transport=httpx.MockTransport(upstreams))
    app = FastAPI()
    register_declared_routes(app, plugin.get_api_routes())
    return TestClient(app), plugin


@pytest.fixture(autouse=True)
def no_identity():
    """Each test starts in a deployment with no sign-in, and leaves it so."""
    security.clear_identity()
    yield
    security.clear_identity()


def signed_in_as(subject, allowed=True):
    """A deployment whose sign-in recognises one caller and decides rights."""
    async def who(request):
        if request.headers.get("authorization") != "Bearer good":
            raise HTTPException(status_code=401, detail="not signed in")
        return subject

    async def decide(actor, asked):
        if not allowed:
            raise HTTPException(status_code=403, detail="no")
        return True

    security.set_identity(security.Identity(subject_dependency=who, checker=decide,
                                            name="test"))


# --- the table ---------------------------------------------------------------------------

def test_the_longest_prefix_wins_and_only_on_a_segment_boundary():
    routes = parse_table([
        {"id": "shop", "prefix": "/api/shop", "upstreams": ["http://shop"]},
        {"id": "orders", "prefix": "/api/shop/orders/", "upstreams": ["http://orders"]},
    ])
    assert match(routes, "/api/shop/orders/7").id == "orders"
    assert match(routes, "/api/shop/orders").id == "orders"
    assert match(routes, "/api/shop/items").id == "shop"
    assert match(routes, "/api/shop-archive") is None


@pytest.mark.parametrize("entry, words", [
    ({"prefix": "/", "upstreams": ["http://a"]}, "would cover the panel"),
    ({"prefix": "api", "upstreams": ["http://a"]}, "starting with '/'"),
    ({"prefix": "/a/../b", "upstreams": ["http://a"]}, "not a plain path"),
    ({"prefix": "/a"}, "no upstream"),
    ({"prefix": "/a", "upstreams": ["ftp://a"]}, "not an http(s) URL"),
    ({"prefix": "/a", "upstreams": ["http://a"], "auth": "token"}, "auth must be"),
    ({"prefix": "/api/admin", "upstreams": ["http://a"]}, "gateway's own"),
    ({"prefix": "/a", "upstreams": ["http://a"], "headers": {"X-Keepup-User": "1"}},
     "gateway's own"),
    ({"prefix": "/a", "upstreams": ["http://a"], "timeout": 0}, "positive number"),
])
def test_a_table_that_cannot_be_served_is_refused_naming_the_route(entry, words):
    with pytest.raises(TableError) as refused:
        parse_table([entry])
    assert words in str(refused.value)


def test_two_routes_with_one_prefix_are_refused():
    with pytest.raises(TableError, match="already route"):
        parse_table([{"id": "a", "prefix": "/x", "upstreams": ["http://a"]},
                     {"id": "b", "prefix": "/x/", "upstreams": ["http://b"]}])


def test_a_route_that_asks_for_sign_in_needs_the_secret():
    plugin = GatewayPlugin({"routes": [{"prefix": "/x", "upstreams": ["http://a"],
                                        "auth": "session"}]})
    assert [route["path"] for route in plugin.get_api_routes()] == \
        ["/api/admin/gateway/routes"]
    with pytest.raises(TableError, match="identity_secret"):
        import asyncio
        asyncio.run(plugin.initialize())


def test_the_secret_may_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("GATEWAY_SECRET", SECRET)
    plugin = GatewayPlugin({"identity_secret": "${GATEWAY_SECRET}", "routes": [
        {"prefix": "/x", "upstreams": ["http://a"], "auth": "session"}]})
    assert plugin.proxy.identity_secret == SECRET


# --- passing on --------------------------------------------------------------------------

def test_the_request_is_passed_on_without_the_prefix_and_with_its_query():
    services = Upstreams()
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://shop:9000"],
                          "strip_prefix": True}], services)
    answer = client.get("/api/shop/items?page=2")
    assert answer.status_code == 200
    assert answer.json() == {"host": "shop", "path": "/items", "query": "page=2"}
    assert client.get("/api/shop").json()["path"] == "/"


def test_the_prefix_is_kept_unless_the_route_strips_it():
    services = Upstreams()
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://shop"]}], services)
    assert client.get("/api/shop/items").json()["path"] == "/api/shop/items"


def test_the_body_and_the_method_reach_the_service():
    services = Upstreams({"shop": lambda request: httpx.Response(
        201, content=request.method.encode() + b":" + request.content)})
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://shop"]}], services)
    answer = client.patch("/api/shop/1", content=b'{"name": "tea"}')
    assert answer.status_code == 201
    assert answer.content == b'PATCH:{"name": "tea"}'


def test_the_request_id_and_the_forwarded_headers_reach_the_service():
    services = Upstreams()
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://shop"]}], services)
    answer = client.get("/api/shop", headers={"X-Request-Id": "abc-123",
                                              "X-Forwarded-For": "10.0.0.1"})
    sent = services.seen[-1].headers
    assert sent["x-request-id"] == "abc-123"
    assert answer.headers["x-request-id"] == "abc-123"
    assert sent["x-forwarded-for"] == "10.0.0.1, testclient"
    assert sent["x-forwarded-prefix"] == "/api/shop"
    assert sent["x-forwarded-proto"] == "http"
    assert sent["x-forwarded-host"] == "testserver"


def test_a_request_id_a_log_line_could_be_broken_by_is_replaced():
    services = Upstreams()
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://shop"]}], services)
    client.get("/api/shop", headers={"X-Request-Id": "a b\tc"})
    assert services.seen[-1].headers["x-request-id"] != "a b\tc"


def test_connection_headers_stay_on_their_side():
    services = Upstreams({"shop": lambda request: httpx.Response(
        200, headers=[("Keep-Alive", "timeout=5"), ("Set-Cookie", "a=1"),
                      ("Set-Cookie", "b=2")], content=b"ok")})
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://shop"]}], services)
    answer = client.get("/api/shop", headers={"Connection": "X-Secret-Hop",
                                              "X-Secret-Hop": "1", "TE": "trailers"})
    sent = services.seen[-1].headers
    assert "x-secret-hop" not in sent and "te" not in sent
    assert "keep-alive" not in answer.headers
    assert answer.headers.get_list("set-cookie") == ["a=1", "b=2"]


def test_a_route_adds_its_own_headers():
    services = Upstreams()
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://shop"],
                          "headers": {"X-Tenant": "north"}}], services)
    client.get("/api/shop")
    assert services.seen[-1].headers["x-tenant"] == "north"


def test_a_method_the_route_does_not_open_is_405():
    services = Upstreams()
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://shop"],
                          "methods": ["GET"]}], services)
    answer = client.delete("/api/shop/1")
    assert answer.status_code == 405
    assert answer.headers["allow"] == "GET"
    assert services.seen == []


def test_a_body_over_the_limit_never_reaches_the_service():
    services = Upstreams()
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://shop"],
                          "max_body_bytes": 10}], services)
    assert client.post("/api/shop", content=b"x" * 11).status_code == 413
    assert services.seen == []


def test_a_nested_route_mounted_later_still_wins():
    """The transport matched /api/shop/{path}; the handler chooses again."""
    services = Upstreams()
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://shop"]},
                         {"prefix": "/api/shop/orders", "upstreams": ["http://orders"]}],
                        services)
    assert client.get("/api/shop/orders/7").json()["host"] == "orders"


# --- the addresses ------------------------------------------------------------------------

def test_addresses_are_asked_in_turn():
    services = Upstreams()
    client, _ = gateway([{"prefix": "/api/shop",
                          "upstreams": ["http://one", "http://two"]}], services)
    for _ in range(4):
        client.get("/api/shop")
    assert services.hosts() == ["one", "two", "one", "two"]


def test_an_address_that_takes_no_connection_is_left_out_and_reported():
    services = Upstreams({"one": httpx.ConnectError("refused")})
    client, plugin = gateway([{"id": "shop", "prefix": "/api/shop",
                               "upstreams": ["http://one", "http://two"]}], services)
    assert client.post("/api/shop", content=b"order").json()["host"] == "two"
    assert client.get("/api/shop").json()["host"] == "two"
    assert services.hosts() == ["one", "two", "two"]
    report = plugin.service.report()["routes"][0]["upstreams"]
    assert report[0]["state"] == "cooling" and report[0]["last_failure"]
    assert report[1]["state"] == "live"


def test_when_every_address_failed_one_is_still_tried():
    clock = [0.0]
    balancer = Balancer(clock=lambda: clock[0])
    balancer.failed("r", "http://a", 30)
    balancer.failed("r", "http://b", 30)
    assert sorted(balancer.order("r", ("http://a", "http://b"))) == ["http://a", "http://b"]
    clock[0] = 31.0
    assert balancer.state("r", "http://a")["state"] == "live"


def test_an_idempotent_request_moves_on_after_503_and_a_post_does_not():
    services = Upstreams({"one": 503})
    client, _ = gateway([{"prefix": "/api/shop",
                          "upstreams": ["http://one", "http://two"]}], services)
    assert client.get("/api/shop").json()["host"] == "two"

    services = Upstreams({"one": 503})
    client, _ = gateway([{"prefix": "/api/shop",
                          "upstreams": ["http://one", "http://two"]}], services)
    assert client.post("/api/shop", content=b"order").status_code == 503
    assert services.hosts() == ["one"]


def test_no_address_answering_is_502_and_a_timeout_is_504():
    services = Upstreams({"one": httpx.ConnectError("refused")})
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://one"]}], services)
    answer = client.get("/api/shop")
    assert answer.status_code == 502 and "x-request-id" in answer.headers

    services = Upstreams({"one": httpx.ReadTimeout("slow")})
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://one"]}], services)
    assert client.get("/api/shop").status_code == 504


# --- the edge and the identity ------------------------------------------------------------

def test_a_header_the_gateway_signs_is_cut_from_the_client_on_an_open_route():
    services = Upstreams()
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://shop"]}], services)
    client.get("/api/shop", headers={"X-Keepup-User-Id": "1", "X-Keepup-Roles": "ADMIN"})
    sent = services.seen[-1].headers
    assert not [name for name in sent if name.lower().startswith("x-keepup-")]


def test_a_route_that_asks_for_sign_in_refuses_without_it():
    services = Upstreams()
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://shop"],
                          "auth": "session"}], services, secret=SECRET)
    assert client.get("/api/shop").status_code == 401      # no sign-in configured at all
    signed_in_as({"id": 7, "username": "ann", "roles": ["CLIENT"]})
    assert client.get("/api/shop").status_code == 401      # configured, not signed in
    assert services.seen == []


def test_a_signed_in_caller_reaches_the_service_with_an_identity_it_can_check():
    services = Upstreams()
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://shop"],
                          "auth": "session"}], services, secret=SECRET)
    signed_in_as({"id": 7, "username": "ann", "roles": ["CLIENT", "ADMIN"]})
    answer = client.get("/api/shop", headers={"Authorization": "Bearer good",
                                              "X-Keepup-Roles": "ROOT"})
    assert answer.status_code == 200
    sent = services.seen[-1].headers
    caller = verify_identity(sent, SECRET)
    assert caller == Caller(id="7", username="ann", roles=("ADMIN", "CLIENT"),
                            request_id=sent["x-request-id"])


def test_a_permission_is_decided_at_the_edge():
    services = Upstreams()
    client, _ = gateway([{"prefix": "/api/shop", "upstreams": ["http://shop"],
                          "permission": "shop.read"}], services, secret=SECRET)
    signed_in_as({"id": 7, "username": "ann", "roles": ["CLIENT"]}, allowed=False)
    assert client.get("/api/shop", headers={"Authorization": "Bearer good"}).status_code == 403
    signed_in_as({"id": 7, "username": "ann", "roles": ["CLIENT"]}, allowed=True)
    assert client.get("/api/shop", headers={"Authorization": "Bearer good"}).status_code == 200


def test_an_altered_or_old_identity_is_refused():
    headers = sign_identity(Caller("7", "ann", ("CLIENT",), ""), SECRET, "req-1", now=1000)
    headers["X-Request-Id"] = "req-1"
    assert verify_identity(headers, SECRET, now=1010).username == "ann"
    with pytest.raises(IdentityError, match="too old"):
        verify_identity(headers, SECRET, now=1100)
    with pytest.raises(IdentityError, match="does not match"):
        verify_identity({**headers, "X-Keepup-Roles": "ADMIN"}, SECRET, now=1010)
    with pytest.raises(IdentityError, match="does not match"):
        verify_identity({**headers, "X-Request-Id": "req-2"}, SECRET, now=1010)
    with pytest.raises(IdentityError, match="does not match"):
        verify_identity(headers, "another secret", now=1010)
    with pytest.raises(IdentityError, match="no signed identity"):
        verify_identity({}, SECRET)


def test_the_caller_is_read_from_the_kernels_subject_in_either_shape():
    assert caller_of({"id": 1, "username": "a", "role": "ADMIN"}).roles == ("ADMIN",)
    assert caller_of({"id": 1, "username": "a", "roles": ["B", "A"]}).roles == ("A", "B")


# --- the administrator --------------------------------------------------------------------

def test_the_report_asks_for_its_right_and_shows_the_table():
    plugin = GatewayPlugin({"routes": [{"id": "shop", "prefix": "/api/shop",
                                        "upstreams": ["http://one", "http://two"]}]})
    report_route = plugin.get_api_routes()[0]
    assert report_route["path"] == "/api/admin/gateway/routes"
    assert report_route["permission"] == "gateway.admin"
    report = plugin.service.report()["routes"]
    assert [route["id"] for route in report] == ["shop"]
    assert [item["address"] for item in report[0]["upstreams"]] == \
        ["http://one", "http://two"]
