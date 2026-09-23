"""What a plugin route accepts, and from where.

Task keepup-12. A plugin route is data, so the framework unpacks what arrived
into the handler's keyword arguments. Three things followed from how that was
done, and none of them is visible in a correct request.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/route_parameters_tests.py -v
"""

import pytest
from fastapi import HTTPException

from keepup.plugins.base import BasePlugin, PluginManager
from keepup.plugins.registry import accepted_params


# --- what reaches the handler -------------------------------------------------

async def handler_with_a_default(current_user, order_id, as_user=None):
    return {"order_id": order_id, "as_user": as_user}


async def handler_taking_anything(current_user, **kwargs):
    return kwargs


def test_a_parameter_the_route_does_not_take_is_refused_to_the_client():
    """It used to raise TypeError, which the client saw as 500.

    A 500 is the server saying it broke, and every one of them wrote a row into
    the audit table -- so `?nosuch=1` in a loop filled the database and the log
    at once, and on a route with require_auth False anybody could send it.
    """
    with pytest.raises(HTTPException) as refused:
        accepted_params(handler_with_a_default, {"order_id": "5", "nosuch": "1"})

    assert refused.value.status_code == 400
    assert "nosuch" in refused.value.detail


def test_the_declared_parameters_pass():
    passed = accepted_params(handler_with_a_default, {"order_id": "5", "as_user": "1"})
    assert passed == {"order_id": "5", "as_user": "1"}


def test_a_handler_that_takes_anything_gets_everything():
    """**kwargs is a handler asking for the lot, which is its own decision."""
    passed = accepted_params(handler_taking_anything, {"whatever": "1"})
    assert passed == {"whatever": "1"}


def test_the_user_is_never_taken_from_the_caller():
    """current_user is supplied by the framework.

    A client sending ?current_user=1 used to get a 500 out of the duplicate
    keyword argument -- which at least meant it could not be overridden, but
    said so by crashing.
    """
    passed = accepted_params(handler_with_a_default,
                             {"order_id": "5", "current_user": "1"})
    assert "current_user" not in passed


def test_an_uninspectable_callable_is_left_alone(monkeypatch):
    """Refusing a plugin over introspection would be the framework's fault, not its.

    Forced rather than found: the callables that cannot be inspected are
    builtins and C extensions, and a plugin handler is neither -- which is
    exactly why the branch would otherwise go untested until it fired.
    """
    from keepup.plugins import routes

    def refuses(_):
        raise ValueError("no signature for this one")

    monkeypatch.setattr(routes.inspect, "signature", refuses)
    passed = accepted_params(handler_with_a_default, {"anything": "1"})
    assert passed == {"anything": "1"}


# --- the path wins over the query ---------------------------------------------

def test_the_path_is_what_the_route_matched_on():
    """`GET /api/orders/5?order_id=99` used to reach the handler with 99.

    Every rule built on the path -- a proxy ACL, a reading of the audit
    afterwards -- still said 5.

    This used to count the order of the two updates in the source instead of
    making a request: the merge lived in eight copies of a closure, and a
    request walked one of them at a time. Since keepup-21 there is one, and
    `plugin_runtime_tests.test_the_path_wins_over_the_query` walks it with a
    real request. What is left here is the statement that there is still one
    place -- a second copy is how the two drifted apart the first time.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "plugins/routes.py").read_text(encoding="utf-8")

    assert source.count("params.update(request.path_params)") == 1, \
        "the path is merged in more than one place again"
    assert source.index("params.update(request.query_params)") \
        < source.index("params.update(request.path_params)"), \
        "the query is applied over the path"


# --- a raw route ---------------------------------------------------------------

class RawRoutePlugin(BasePlugin):
    """A plugin declaring a raw route that also asks for authentication."""

    def __init__(self):
        super().__init__("rawroute", "Raw route", {})
        self.initialized = True

    async def initialize(self):
        return True

    async def raw(self, request):
        return {"ok": True}

    def get_handlers(self):
        return {}

    def get_api_routes(self):
        return [{
            "path": "/api/raw",
            "methods": ["POST"],
            "handler": self.raw,
            "raw_request": True,
            "require_auth": True,
        }]


async def test_a_raw_route_may_not_also_promise_authentication():
    """The combination used to register an open route and print auth=True.

    A raw handler is handed the Request and nothing else, so require_auth on it
    is a promise the framework does not keep -- and the start-up line said it
    did.
    """
    from fastapi import FastAPI

    from keepup.plugins.registry import register_plugin_routes

    manager = PluginManager(plugins_dir="app/plugins")
    plugin = RawRoutePlugin()
    manager.plugins["rawroute"] = plugin
    manager.loaded_plugins["rawroute"] = plugin

    with pytest.raises(ValueError) as refused:
        await register_plugin_routes(FastAPI(), manager)

    assert "raw_request" in str(refused.value)


# --- the sockets of a plugin that never came up --------------------------------

class SocketPlugin(BasePlugin):
    """A plugin with a WebSocket route, which may or may not have initialised."""

    def __init__(self):
        super().__init__("socketplugin", "Socket plugin", {})

    async def initialize(self):
        return True

    async def socket(self, websocket):
        return None

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}

    def get_websocket_routes(self):
        return [{"path": "/ws/socketplugin", "handler": self.socket}]


def test_a_plugin_that_did_not_initialise_registers_no_sockets():
    """Its HTTP routes were dropped and its sockets were not.

    The admin surface reported the plugin failed while a socket of its went on
    listening against an object with no tables and no connections behind it.
    """
    manager = PluginManager(plugins_dir="app/plugins")
    plugin = SocketPlugin()
    manager.plugins["socketplugin"] = plugin

    assert manager.get_all_websocket_routes() == []

    manager.loaded_plugins["socketplugin"] = plugin
    assert len(manager.get_all_websocket_routes()) == 1
