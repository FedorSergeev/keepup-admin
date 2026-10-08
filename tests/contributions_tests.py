"""What a plugin contributes, and the two route keys a contribution needed.

Task keepup-102. Two things are checked here. First, the contribution points:
the closed list of what a plugin may add, one consumer per kind, and a collection
that records a plugin whose getter raised instead of taking the start down.
Second, the two route keys 0.4.0 adds, because without them a capability cannot
become a plugin at all: a route that answers something other than JSON -- a
Prometheus scrape is text -- and a route that asks not to be written to the
incoming-request audit, because a scrape every fifteen seconds is not an audit.

Loading and binding are also checked apart: a runtime that serves no HTTP loads
and initialises its plugins, and a declaration the route runtime refuses stops
the start where it is found rather than leaving a route quietly missing.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from keepup import audit
from keepup.auth.dependencies import get_panel_user
from keepup.kernel import (
    KIND_OPTIONAL,
    KIND_TRANSPORT,
    MiddlewareSpec,
    PluginDescriptor,
    collect,
    create_runtime,
    mount,
)
from keepup.kernel.contributions import DEFAULT_MIDDLEWARE_ORDER, Contributions
from keepup.plugins import registry
from keepup.plugins.base import BasePlugin, PluginManager


SOMEBODY = {"id": 7, "username": "tester"}


class ProbePlugin(BasePlugin):
    """A plugin that publishes exactly the routes a check hands it."""

    def __init__(self, declared_routes, plugin_id="probe"):
        super().__init__(plugin_id, "Probe", {})
        self.initialized = True
        self._routes = declared_routes

    async def initialize(self):
        return True

    def get_api_routes(self):
        return self._routes

    def get_handlers(self):
        return {}


def running(declared_routes, signed_in_as=SOMEBODY):
    """An application with those routes on it, and a client to call them with."""
    plugin = ProbePlugin(declared_routes)
    manager = PluginManager(plugins_dir="")
    manager.plugins["probe"] = plugin
    manager.loaded_plugins["probe"] = plugin

    app = FastAPI()
    asyncio.run(registry.register_plugin_routes(app, manager))
    if signed_in_as is not None:
        app.dependency_overrides[get_panel_user] = lambda: signed_in_as
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def audited():
    """The audit records into this buffer, and no further."""
    audit.incoming_requests_buffer.clear()
    yield audit.incoming_requests_buffer
    audit.incoming_requests_buffer.clear()


# --- the two route keys ---------------------------------------------------------


async def a_metric():
    """A route whose body is not JSON, as a scrape is."""
    return "keepup_up 1"


async def a_plain(current_user):
    """A route that answers a dictionary, as every older route does."""
    return {"who": current_user["username"]}


async def a_response():
    """A route that builds its own answer."""
    return Response(content="raw", media_type="text/csv")


def test_a_route_that_asks_not_to_be_audited_is_not(audited):
    """A scrape every fifteen seconds per replica is not an audit."""
    client = running([{
        "path": "/metrics", "methods": ["GET"], "handler": a_metric,
        "require_auth": False, "audit": False, "response_media_type": "text/plain",
    }])
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "keepup_up 1"
    assert audited == {}


def test_a_route_is_audited_unless_it_says_otherwise(audited):
    """The default stays audited: a route that quietly leaves is what the audit prevents."""
    client = running([{"path": "/api/plain", "methods": ["GET"], "handler": a_plain}])
    assert client.get("/api/plain").status_code == 200
    assert len(audited) == 1


def test_a_handler_that_builds_its_own_answer_is_passed_through():
    """A Response is an answer already; wrapping it would break it."""
    client = running([{
        "path": "/api/csv", "methods": ["GET"], "handler": a_response,
        "require_auth": False,
    }])
    response = client.get("/api/csv")
    assert response.headers["content-type"].startswith("text/csv")
    assert response.text == "raw"


def test_a_text_route_says_what_it_answers(audited):
    """Without the key the answer would be JSON, and a scrape is not."""
    client = running([{
        "path": "/api/up", "methods": ["GET"], "handler": a_metric,
        "require_auth": False, "response_media_type": "text/plain",
    }])
    response = client.get("/api/up")
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "keepup_up 1"
    assert len(audited) == 1


def test_a_media_type_is_a_media_type():
    """A route that declares nonsense is refused at the start, not at a request."""
    with pytest.raises(ValueError) as refused:
        running([{
            "path": "/api/up", "methods": ["GET"], "handler": a_metric,
            "require_auth": False, "response_media_type": 12,
        }])
    assert "response_media_type" in str(refused.value)


def test_a_raw_route_cannot_also_declare_a_media_type():
    """A raw route answers with a Response of its own; the key would be ignored."""
    async def a_raw(request):
        return Response(content="raw")

    with pytest.raises(ValueError) as refused:
        running([{
            "path": "/api/raw", "methods": ["GET"], "handler": a_raw,
            "raw_request": True, "response_media_type": "text/plain",
        }])
    assert "response_media_type" in str(refused.value)


# --- the contribution points ----------------------------------------------------


class ContributorPlugin(BasePlugin):
    """A plugin that contributes one of everything, to be collected."""

    descriptor = PluginDescriptor(
        id="contributor",
        name="Contributor",
        kind=KIND_OPTIONAL,
        provides=("contributor>=1",),
        contributions=(
            "routes", "sockets", "sections", "tables", "jobs", "events", "metrics",
            "middleware", "settings", "permissions",
        ),
    )

    def __init__(self, config=None):
        super().__init__("contributor", "Contributor", config)

    async def initialize(self):
        return True

    def get_api_routes(self):
        return [{"path": "/api/contributor", "methods": ["GET"], "handler": self.list_things}]

    async def list_things(self):
        return {"things": []}

    def get_websocket_routes(self):
        return [{"path": "/ws/contributor", "handler": self.socket}]

    async def socket(self, websocket):
        return None

    def get_panel_sections(self):
        return [{"id": "contributor", "name": "Contributor", "js": "/x.js", "icon": "box"}]

    def get_declared_tables(self):
        return ["contributor_thing"]

    def get_scheduled_jobs(self):
        return [("contributor_sweep", self.list_things, {"seconds": 60})]

    def get_event_sinks(self):
        return [self.list_things]

    def get_metric_collectors(self):
        return [self.list_things]

    def get_middleware(self):
        return [MiddlewareSpec(order=30, factory=object, name="Gate")]

    def get_settings_defaults(self):
        return {"contributor_batch": 100}

    def get_route_permissions(self):
        return {"contributor.read": "anyone signed in"}

    def get_handlers(self):
        return {"list": self.list_things}


class BrokenContributorPlugin(ContributorPlugin):
    """A plugin whose section declaration raises, and whose routes do not."""

    descriptor = PluginDescriptor(id="broken", name="Broken", kind=KIND_OPTIONAL)

    def __init__(self, config=None):
        super().__init__(config)
        self.plugin_id = "broken"
        self.name = "Broken"

    def get_panel_sections(self):
        raise RuntimeError("the section catalogue is unreadable")

    def get_api_routes(self):
        return [{"path": "/api/broken", "methods": ["GET"], "handler": self.list_things}]


class QuietTransportPlugin(BasePlugin):
    """A transport: a way in, which is a kind and not a contribution call."""

    descriptor = PluginDescriptor(id="quiet", name="Quiet transport", kind=KIND_TRANSPORT)

    def __init__(self, config=None):
        super().__init__("quiet", "Quiet transport", config)

    async def initialize(self):
        return True

    async def serve(self, runtime):
        return None

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


def entry_point(plugin_class):
    """An entry point for a plugin class, named by its descriptor."""

    class FakeEntryPoint:
        def __init__(self):
            self.name = plugin_class.descriptor.id
            self.group = "keepup.plugins"

        def load(self):
            return plugin_class

    return FakeEntryPoint()


def runtime_with(*plugin_classes, enabled=True):
    """A runtime whose catalogue declares exactly these plugins."""
    entries = [
        {"id": plugin_class.descriptor.id, "enabled": enabled} for plugin_class in plugin_classes
    ]
    return create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue={"plugins": entries},
        entry_points=[entry_point(plugin_class) for plugin_class in plugin_classes],
    )


async def test_every_kind_is_collected_from_the_plugin_that_contributed_it():
    """The list is closed, and each kind has one consumer -- this checks the first half."""
    runtime = runtime_with(ContributorPlugin)
    await runtime.start()
    contributions = collect(runtime)
    assert [route["path"] for route in contributions.of("routes")] == ["/api/contributor"]
    assert contributions.of("routes")[0]["_plugin_id"] == "contributor"
    assert len(contributions.of("sockets")) == 1
    assert contributions.of("sections")[0]["icon"] == "box"
    assert contributions.of("tables") == ["contributor_thing"]
    assert len(contributions.of("jobs")) == 1
    assert len(contributions.of("events")) == 1
    assert len(contributions.of("metrics")) == 1
    assert contributions.merged("settings") == {"contributor_batch": 100}
    assert contributions.merged("permissions") == {"contributor.read": "anyone signed in"}
    assert contributions.errors == []


async def test_middleware_is_ordered_outermost_first():
    """The order is the contract: the body limit sits inside the version middleware."""
    runtime = runtime_with(ContributorPlugin, QuietTransportPlugin)
    await runtime.start()
    contributions = collect(runtime)
    contributions.by_kind["middleware"].append(MiddlewareSpec(order=10, factory=object, name="Outer"))
    contributions.by_kind["middleware"].append(lambda: None)
    ordered = contributions.middleware_in_order()
    assert [spec.order for spec in ordered] == [10, 30, DEFAULT_MIDDLEWARE_ORDER]
    assert ordered[1].plugin_id == "contributor"
    assert ordered[-1].name == "<lambda>"


async def test_a_transport_is_collected_by_being_one():
    """A transport contributes a server, not a call."""
    runtime = runtime_with(QuietTransportPlugin)
    await runtime.start()
    contributions = collect(runtime)
    assert contributions.of("transport")[0]["plugin_id"] == "quiet"
    assert hasattr(contributions.of("transport")[0]["plugin"], "serve")


async def test_a_plugin_whose_getter_raises_loses_that_kind_and_keeps_the_rest():
    """One broken declaration must not take the other nine kinds with it."""
    runtime = runtime_with(BrokenContributorPlugin, ContributorPlugin)
    await runtime.start()
    contributions = collect(runtime)
    assert contributions.errors == [("broken", "sections", "RuntimeError: the section catalogue is unreadable")]
    assert [route["path"] for route in contributions.of("routes")] == [
        "/api/broken", "/api/contributor"
    ]
    assert len(contributions.of("sections")) == 1


async def test_a_registrar_that_refuses_a_declaration_stops_the_mounting():
    """A consumer that knows a declaration is wrong says so where it is found."""
    runtime = runtime_with(ContributorPlugin)
    await runtime.start()
    contributions = collect(runtime)

    def refuses(items, runtime=None):
        raise ValueError("a mask names a parameter the handler does not take")

    with pytest.raises(ValueError):
        mount(contributions, {"routes": refuses})


async def test_mounting_hands_each_kind_to_its_consumer_and_counts_it():
    """What was mounted is a number the report can carry."""
    runtime = runtime_with(ContributorPlugin)
    await runtime.start()
    contributions = collect(runtime)
    seen = []
    mounted = mount(contributions, {
        "routes": lambda items, runtime=None: seen.append(("routes", len(items))),
        "settings": lambda items, runtime=None: seen.append(("settings", len(items))),
    })
    assert mounted == {"routes": 1, "settings": 1}
    assert ("routes", 1) in seen
    assert mount(contributions, {}) == {}


async def test_a_kind_nobody_knows_is_refused():
    """The list is closed: a new kind is a change to the specification."""
    with pytest.raises(ValueError):
        mount(Contributions(), {"widgets": lambda items, runtime=None: None})


def test_the_contribution_list_is_the_one_the_specification_names():
    """The kinds are data, so the guide, the spec and the code can be one list."""
    from keepup.kernel.contributions import CONTRIBUTION_KINDS, GETTERS

    assert set(CONTRIBUTION_KINDS) == {
        "routes", "sockets", "sections", "tables", "jobs", "events", "metrics",
        "middleware", "transport", "settings", "permissions",
    }
    assert set(GETTERS) | {"transport"} == set(CONTRIBUTION_KINDS)


# --- loading is not binding -----------------------------------------------------

PLUGIN_FILE = '''"""A plugin of an application, written by a check."""

from keepup.plugins.base import BasePlugin


class DemoPlugin(BasePlugin):
    """A plugin with one route and nothing else."""

    def __init__(self, config=None):
        super().__init__("demo", "Demo", config)
        self.initialized = False

    async def initialize(self):
        self.initialized = True
        return True

    def get_api_routes(self):
        return [{"path": "/api/demo", "methods": ["GET"], "handler": self.hello,
                 "require_auth": False}]

    async def hello(self):
        return {"hello": "world"}

    def get_handlers(self):
        return {}
'''

BAD_MASK_FILE = '''"""A plugin whose route declares a parameter its handler does not take."""

from keepup.plugins.base import BasePlugin


class BadPlugin(BasePlugin):
    """The declaration is the mistake, and the start is where it must be found."""

    def __init__(self, config=None):
        super().__init__("bad", "Bad", config)

    async def initialize(self):
        return True

    def get_api_routes(self):
        return [{"path": "/api/bad", "methods": ["GET"], "handler": self.hello,
                 "require_auth": False, "params": {"nope": {"type": "str"}}}]

    async def hello(self):
        return {"hello": "world"}

    def get_handlers(self):
        return {}
'''


def manager_for(tmp_path, plugin_file, plugin_id, source):
    """A manager over a directory holding one plugin and a catalogue declaring it."""
    directory = tmp_path / plugin_id
    directory.mkdir(parents=True)
    (directory / f"{plugin_id}.py").write_text(plugin_file, encoding="utf-8")
    catalogue = tmp_path / f"{plugin_id}.json"
    catalogue.write_text(json.dumps({"plugins": [{"id": plugin_id, "enabled": True}]}),
                         encoding="utf-8")
    manager = PluginManager(plugins_dir=str(directory))
    return manager, str(catalogue)


async def test_loading_initialises_without_an_application(tmp_path):
    """A runtime that serves no HTTP needs no ASGI application to load a plugin."""
    manager, catalogue = manager_for(tmp_path, PLUGIN_FILE, "demo", "demo")
    resolution = await registry.load_and_initialize(manager, catalogue)
    assert resolution is not None
    assert manager.get_plugin("demo").initialized is True


async def test_binding_is_the_separate_step_that_registers_the_routes(tmp_path):
    """The same call, plus an application, and the routes are there."""
    manager, catalogue = manager_for(tmp_path, PLUGIN_FILE, "demo", "demo")
    app = FastAPI()
    await registry.initialize_plugins(app, manager, catalogue)
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/demo" in paths


async def test_a_route_declaration_refused_by_the_runtime_stops_the_start(tmp_path):
    """A route that would have gone quietly missing is a mistake found at the start."""
    manager, catalogue = manager_for(tmp_path, BAD_MASK_FILE, "bad", "bad")
    # Loading is fine: the plugin itself is not the problem.
    assert await registry.load_and_initialize(manager, catalogue) is not None

    manager_again, catalogue_again = manager_for(tmp_path / "again", BAD_MASK_FILE, "bad", "bad")
    with pytest.raises(ValueError) as refused:
        await registry.initialize_plugins(FastAPI(), manager_again, catalogue_again)
    assert "/api/bad" in str(refused.value)


async def test_an_unreadable_declaration_is_reported_and_not_fatal(tmp_path):
    """An application that names no catalogue is an application without plugins."""
    manager = PluginManager(plugins_dir=str(tmp_path))
    assert await registry.load_and_initialize(manager, str(tmp_path / "absent.json")) is None
