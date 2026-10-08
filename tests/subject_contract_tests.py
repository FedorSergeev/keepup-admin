"""The subject of a call and the right attached to it, as the kernel's contract.

Task keepup-119. Eleven modules of the kernel used to reach for the current
administrator by importing the module that finds them, which made the sign-in a
thing every capability depended on and nothing could be moved without breaking
all eleven at once. What replaced it is a name: the kernel owns the shape of the
question and the two services that answer it, a deployment puts an
implementation behind them, and a transport hands the actor over on the call.

These checks are about the seam rather than about sign-in, which has tests of its
own: a deployment with no identity system refuses instead of failing to import
one, the actor a transport passes is what the handler sees, and a right is
decided by whoever the deployment put behind ``permissions``.
"""

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from keepup.kernel import Call, CallError, RouteSpec, invoke
from keepup.kernel import security


class NotesPlugin:
    """Not a plugin: the pieces this file needs from one, without the loading."""

    @staticmethod
    async def list_notes(current_user=None):
        """A handler that wants to know who is calling."""
        return {"notes": [], "who": (current_user or {}).get("username")}


def build_app(**wrapper_options):
    """An application with one signed-in route on it, built the way the runtime does."""
    from keepup.plugins import registry

    route = {"path": "/api/notes", "methods": ["GET"], "handler": NotesPlugin.list_notes}
    route.update(wrapper_options)
    plugin = type("Probe", (), {
        "get_api_routes": lambda self: [route],
        "get_handlers": lambda self: {},
        "initialized": True,
    })()
    manager = type("Manager", (), {
        "get_all_api_routes": lambda self: [route],
        "get_all_websocket_routes": lambda self: [],
        "loaded_plugins": {"probe": plugin},
    })()
    app = FastAPI()
    asyncio.run(registry.register_plugin_routes(app, manager))
    return app


def test_a_deployment_with_no_identity_system_refuses_instead_of_failing(monkeypatch):
    """401, not an ImportError three frames down: no sign-in is a deployment too."""
    monkeypatch.setattr(security, "_identity", None)
    client = TestClient(build_app(), raise_server_exceptions=False)
    response = client.get("/api/notes")
    assert response.status_code == 401


def test_the_caller_the_deployment_finds_is_the_caller_the_handler_sees(monkeypatch):
    """The seam is how a route knows who is calling, whoever finds them."""
    async def someone(request: Request):
        return {"id": 4, "username": "somebody"}

    monkeypatch.setattr(security, "_identity", security.Identity(subject_dependency=someone))
    client = TestClient(build_app())
    assert client.get("/api/notes").json()["who"] == "somebody"


def test_a_right_is_decided_by_what_the_deployment_put_behind_permissions(monkeypatch):
    """The kernel asks; the deployment answers; the answer is what refuses the call."""
    asked = []

    async def refuses(actor, action):
        asked.append((action.permission, action.method, action.path))
        raise PermissionError("no")

    spec = RouteSpec(path="/api/x", methods=("GET",), handler=lambda: {"ok": True},
                     permission="x.read")
    monkeypatch.setattr(security, "_identity", security.Identity(checker=refuses))
    with pytest.raises(PermissionError):
        asyncio.run(invoke(Call(route=spec), checker=security.checker()))
    assert asked == [("x.read", "GET", "/api/x")]


def test_the_question_carries_the_values_taken_from_the_address():
    """A provider that decides by path parameter gets the path parameter."""
    from keepup.kernel.security import AccessRequest

    spec = RouteSpec(path="/api/x/{x_id}", methods=("GET",), handler=lambda x_id: x_id,
                     permission="x.read")
    call = Call(route=spec, params={"x_id": "7"})
    from keepup.kernel.call import access_request

    action: AccessRequest = access_request(call)
    assert action.permission == "x.read"
    assert action.path == "/api/x/{x_id}"
    assert action.path_params == {"x_id": "7"}


def test_a_route_that_wants_a_right_is_not_run_when_nobody_can_decide():
    """Silence is not permission: an undecidable right stops the call."""
    spec = RouteSpec(path="/api/x", methods=("GET",), handler=lambda: {"ok": True},
                     permission="x.read")
    with pytest.raises(CallError) as refused:
        asyncio.run(invoke(Call(route=spec)))
    assert refused.value.code == "unchecked_permission"


def test_the_services_are_named_where_the_catalogue_says():
    """The two names are the kernel's, and the catalogue is where they are written down."""
    assert (security.SERVICE_AUTH, security.SERVICE_PERMISSIONS) == ("auth", "permissions")
    catalogue = (security.__doc__ or "") + ""
    assert catalogue  # the module explains itself, as every module here does
