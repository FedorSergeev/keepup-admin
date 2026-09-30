"""Every route of every profile, as the application registered it (keepup-94).

A route is judged by how it is declared, not by its path: one whose FastAPI
dependencies reach the sign-in is "signed in", one that reaches the
administrator check is "administrative", and one that reaches neither has to be
in PUBLIC_ROUTES with a reason -- or it is a finding. The list lives here on
purpose: a public route is a decision, and a new one has to be made in this
file, in plain sight, rather than by forgetting a dependency.

Then each class is knocked on: nobody gets 401 from every signed-in route, a
client gets 403 from every administrative one, a cookie request that changes
something without the CSRF header gets 403, a socket that signs in closes on a
caller it cannot sign in.

    python3 -m pytest keepup/tests/security_audit_tests/route_sweep_tests.py -v
"""

import re

import pytest
from fastapi.routing import APIRoute
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from keepup.auth import dependencies

import audit_actors
from audit_profiles import (
    HTTP_PROFILES,
    PLUGIN_PUBLIC,
    PLUGIN_RAW,
    SOCKET_GUEST,
    SOCKET_SIGNED,
)

pytestmark = pytest.mark.area("routes")

PROFILE_NAMES = [profile.name for profile in HTTP_PROFILES]

#: Routes that answer without a sign-in, and why each may.
PUBLIC_ROUTES = {
    ("GET", "/"): "the client page; its data comes from signed-in calls",
    ("GET", "/selfcare"): "the panel page; signing in happens inside it",
    ("GET", "/selfcare/modules/{path:path}"): "section scripts of the panel: code, no data",
    ("GET", "/favicon.ico"): "an icon",
    ("POST", "/api/auth/login"): "signing in",
    ("POST", "/api/auth/logout"): "clears the cookies of a session that may already be gone",
    ("POST", "/api/auth/register"): "self-registration, refused unless the deployment opens it",
    ("GET", "/api/health"): "the orchestrator's liveness probe",
    ("GET", "/api/version"): "the build the panel shows before sign-in",
    ("GET", "/api/versions"): "which API versions this server speaks",
    ("GET", "/api/public/config"): "what the sign-in screen needs before sign-in",
    ("GET", "/api/theme/brand"): "the logo on the sign-in screen",
    ("GET", "/api/auth/oidc/login"): "external sign-in; checks state, nonce and PKCE itself",
    ("GET", "/api/auth/oidc/callback"): "external sign-in; checks state, nonce and PKCE itself",
    ("GET", PLUGIN_PUBLIC): "the audit plugin's deliberately public route",
    ("POST", PLUGIN_RAW): "a raw route signs its caller in itself -- knocked on below",
}

#: FastAPI's own documentation routes: public by FastAPI's design.
DOCUMENTATION = {"/api/docs", "/api/redoc", "/docs/oauth2-redirect", "/openapi.json"}

#: Directories served as files, and why each may be public.
MOUNTS = {"/keepup-static": "the package's panel shell: code and styles, no data"}

#: Sockets of the profiles, and whether they sign the caller in.
SOCKETS = {SOCKET_SIGNED: "signed", SOCKET_GUEST: "a guest socket of the audit plugin, by design"}

_SIGNED_IN = {dependencies.get_current_user, dependencies.get_panel_user,
              dependencies.request_token}
_ADMINISTRATIVE = {dependencies.get_current_admin}


def _reaches(dependant, calls) -> bool:
    return any(sub.call in calls or _reaches(sub, calls) for sub in dependant.dependencies)


def classify(route: APIRoute) -> str:
    if _reaches(route.dependant, _ADMINISTRATIVE):
        return "admin"
    if _reaches(route.dependant, _SIGNED_IN):
        return "signed"
    return "public"


def api_routes(running, kind=None):
    """(method, path, route) of the application's API routes, of one kind or all."""
    found = []
    for route in running.app.routes:
        if not isinstance(route, APIRoute):
            continue
        if kind is not None and classify(route) != kind:
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            found.append((method, route.path, route))
    return found


def concrete(path: str) -> str:
    """The path with every parameter filled in by something harmless."""
    return re.sub(r"\{[^}]+\}", "1", path)


def call(client, method, path, **kwargs):
    return client.request(method, concrete(path), **kwargs)


# --- the declaration ---------------------------------------------------------------------

@pytest.mark.parametrize("profile_name", PROFILE_NAMES)
def test_every_route_is_signed_in_or_declared_public(start, profile_name):
    running = start(profile_name)
    undeclared = [f"{method} {path}" for method, path, _ in api_routes(running, "public")
                  if (method, path) not in PUBLIC_ROUTES]
    for route in running.app.routes:
        if isinstance(route, Mount) and route.path not in MOUNTS:
            undeclared.append(f"mount {route.path}")
        elif isinstance(route, WebSocketRoute) and route.path not in SOCKETS:
            undeclared.append(f"socket {route.path}")
        elif type(route) is Route and route.path not in DOCUMENTATION:
            undeclared.append(f"route {route.path}")
    assert undeclared == [], (
        f"{profile_name}: these answer without a sign-in and nobody said they may -- "
        "add a dependency on the sign-in, or add them to PUBLIC_ROUTES with the reason:\n  "
        + "\n  ".join(undeclared))


def test_the_public_list_has_no_dead_entries(start):
    """An entry no profile registers is a door somebody closed; the list must follow."""
    seen = set()
    for name in PROFILE_NAMES:
        seen |= {(method, path) for method, path, _ in api_routes(start(name), "public")}
    assert sorted(set(PUBLIC_ROUTES) - seen) == []


@pytest.mark.parametrize("profile_name", PROFILE_NAMES)
def test_administrative_routes_are_all_under_the_administrator_check(start, profile_name):
    """A route under /api/admin/ that only signs the caller in is open to every client."""
    running = start(profile_name)
    loose = [f"{method} {path}" for method, path, route in api_routes(running)
             if path.startswith("/api/admin/") and classify(route) != "admin"]
    assert loose == []

# --- nobody, a client, and the cookie without its pair -------------------------------------

@pytest.mark.parametrize("profile_name", PROFILE_NAMES)
def test_nobody_is_refused_every_signed_in_route(start, profile_name):
    running = start(profile_name)
    answered = {}
    for method, path, _ in api_routes(running, "signed") + api_routes(running, "admin"):
        status = call(running.client, method, path).status_code
        if status != 401:
            answered[f"{method} {path}"] = status
    assert answered == {}, f"{profile_name}: answered somebody who did not sign in"


@pytest.mark.parametrize("profile_name", PROFILE_NAMES)
def test_a_client_is_refused_every_administrative_route(start, profile_name):
    running = start(profile_name)
    client = audit_actors.client_actor()
    answered = {}
    for method, path, _ in api_routes(running, "admin"):
        status = call(running.client, method, path, headers=client.bearer()).status_code
        if status != 403:
            answered[f"{method} {path}"] = status
    assert answered == {}, f"{profile_name}: an administrative route answered a client"


@pytest.mark.parametrize("profile_name", PROFILE_NAMES)
def test_an_administrator_gets_past_the_door(start, profile_name):
    """The control: without it, every refusal above could be a broken harness."""
    running = start(profile_name)
    admin = audit_actors.admin()
    refused = {}
    for method, path, _ in api_routes(running, "admin"):
        # /metrics answers with what the collector renders, and under test the
        # collector is a stub (keepup/tests/conftest.py) whose answer is no body.
        if method != "GET" or "{" in path or path == "/metrics":
            continue
        status = call(running.client, method, path, headers=admin.bearer()).status_code
        if status in (401, 403):
            refused[path] = status
    assert refused == {}


@pytest.mark.parametrize("profile_name", PROFILE_NAMES)
def test_a_cookie_request_that_changes_something_needs_the_csrf_header(start, profile_name):
    running = start(profile_name)
    admin = audit_actors.admin()
    let_through = {}
    for method, path, _ in api_routes(running, "signed") + api_routes(running, "admin"):
        if method == "GET":
            continue
        for csrf in (None, "wrong"):
            answer = call(running.client, method, path, headers=admin.cookies(csrf=csrf))
            if answer.status_code != 403 or "CSRF" not in answer.text:
                let_through[f"{method} {path} (csrf={csrf})"] = answer.status_code
    assert let_through == {}, f"{profile_name}: a cookie request went past without its pair"


def test_the_right_csrf_pair_is_accepted(start):
    """The control of the check above, on a route that changes nothing lasting."""
    running = start("bare")
    client = audit_actors.client_actor()
    answer = running.client.post("/api/auth/refresh", headers=client.cookies(csrf="right"))
    assert answer.status_code == 200, answer.text

# --- routes that sign themselves in, sockets and files --------------------------------------

def test_a_raw_route_refuses_nobody_itself(start):
    running = start("plugins")
    before = list(running.plugin_calls())
    assert running.client.post(PLUGIN_RAW).status_code == 401
    forged = audit_actors.forged_other_key(audit_actors.admin())
    assert running.client.post(PLUGIN_RAW,
                               headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    assert running.plugin_calls() == before


def _socket_refused(client, path) -> bool:
    try:
        with client.websocket_connect(path) as socket:
            socket.receive_json()
    except WebSocketDisconnect as closed:
        return closed.code == 1008
    return False


@pytest.mark.parametrize("profile_name", [n for n in PROFILE_NAMES if n != "bare"])
def test_a_signed_in_socket_refuses_whom_it_cannot_sign_in(start, profile_name):
    running = start(profile_name)
    actor = audit_actors.client_actor()
    assert _socket_refused(running.client, SOCKET_SIGNED)
    for forge in ("alg-none", "other-key", "expired", "no-session", "revoked-session"):
        token = audit_actors.FORGERIES[forge](actor)
        assert _socket_refused(running.client, f"{SOCKET_SIGNED}?token={token}"), forge

    with running.client.websocket_connect(f"{SOCKET_SIGNED}?token={actor.token}") as socket:
        assert socket.receive_json() == {"user": actor.username}


def test_a_socket_does_not_take_the_cookie_of_another_site(start):
    """The cookie rides along with any page's socket; the origin says whose page it is."""
    running = start("plugins")
    actor = audit_actors.client_actor()
    headers = {"Cookie": f"ss_session={actor.token}", "Origin": "https://evil.example"}
    try:
        with running.client.websocket_connect(SOCKET_SIGNED, headers=headers) as socket:
            socket.receive_json()
        opened = True
    except WebSocketDisconnect as closed:
        opened = closed.code != 1008
    assert not opened


TRAVERSALS = (
    "/selfcare/modules/..%2f..%2f..%2fpyproject.toml",
    "/selfcare/modules/%2e%2e/%2e%2e/%2e%2e/pyproject.toml",
    "/selfcare/modules/....//....//pyproject.toml",
    "/keepup-static/..%2f..%2fpyproject.toml",
    "/keepup-static/%2e%2e/%2e%2e/pyproject.toml",
)


@pytest.mark.parametrize("path", TRAVERSALS)
def test_files_are_not_served_from_outside_their_directory(start, path):
    answer = start("bare").client.get(path)
    assert "keepup-admin" not in answer.text and "[project]" not in answer.text, answer.status_code


@pytest.mark.parametrize("profile_name", PROFILE_NAMES)
def test_answers_carry_the_protective_headers(start, profile_name):
    answer = start(profile_name).client.get("/api/health")
    assert answer.headers.get("x-frame-options") == "SAMEORIGIN"
    assert answer.headers.get("x-content-type-options") == "nosniff"
    assert answer.headers.get("referrer-policy") == "same-origin"
    if profile_name == "https":
        assert "max-age=" in answer.headers.get("strict-transport-security", "")
    else:
        assert "strict-transport-security" not in answer.headers


def test_the_session_cookie_is_out_of_the_page_s_reach_and_tls_only_on_tls(start):
    for profile_name, secure in (("bare", False), ("https", True)):
        running = start(profile_name)
        actor = audit_actors.client_actor()
        answer = running.client.post("/api/auth/login", json={
            "username": actor.username, "password": audit_actors.ACTOR_PASSWORD})
        assert answer.status_code == 200, answer.text
        cookies = [value.lower() for key, value in answer.headers.multi_items()
                   if key.lower() == "set-cookie"]
        session = next(c for c in cookies if c.startswith("ss_session="))
        assert "httponly" in session and "samesite=lax" in session
        assert ("secure" in session.split(";")[-1] or "; secure" in session) is secure, session
        running.client.cookies.clear()


def test_the_stripped_application_answers_nothing_but_metrics(the_other_system, tmp_path):
    """DISABLE_HTTP_SERVER: the routes are absent, not disabled."""
    from audit_profiles import by_name, running as run

    with run(by_name("stripped"), tmp_path) as stripped:
        paths = {getattr(route, "path", None) for route in stripped.app.routes}
        assert "/metrics" in paths
        for path in ("/api/auth/me", "/api/auth/login", "/api/admin/users", "/selfcare"):
            assert stripped.client.get(path).status_code in (404, 405), path

# --- known findings ------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason=(
    "finding: /openapi.json describes every route, administrative ones included, to "
    "anybody, and no setting switches it off -- create_app() passes docs_url and "
    "redoc_url but not openapi_url (keepup-96). Strict: when it is fixed this starts "
    "passing and the mark has to go."))
@pytest.mark.parametrize("profile_name", ["bare"])
def test_the_api_schema_is_not_published_to_everybody(start, profile_name):
    answer = start(profile_name).client.get("/openapi.json")
    assert answer.status_code in (401, 403, 404)
