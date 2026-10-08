"""The transport seam: a server that is not HTTP, and a route that is not FastAPI.

Task keepup-103. The point of the kernel is that a capability can be moved out
and a deployment can be assembled in shapes the framework did not decide. The
sharpest test of that is a transport the framework has never heard of: here a
plugin that speaks two lines over a TCP socket, answering through a route
another plugin declared -- a route that knows nothing about requests, status
codes or JSON bodies, because the invocation is transport-neutral
(``keepup.kernel.call``).

The transport is a test double and stays one: the task that follows builds the
HTTP one on the same seam. What is checked is the seam itself -- the call, the
mask, the refusal, the caller, the worker with no transport at all.
"""

import asyncio
import json
import logging

import pytest

from keepup.kernel import Call, CallError, PluginDescriptor, RouteSpec, create_runtime, invoke
from keepup.kernel.transports import TransportPlugin, serve_all
from keepup.plugins.base import BasePlugin


# --- a transport the framework has never heard of ---------------------------------


class LineTransportPlugin(TransportPlugin):
    """Answers one two-line request over a socket, then returns.

    The protocol: a path on the first line, JSON parameters on the second, and
    the answer -- or the refusal -- as JSON on one line back. It is deliberately
    the smallest thing that is not HTTP.
    """

    descriptor = PluginDescriptor(
        id="line", name="Line transport", kind="transport", priority=1,
    )

    def __init__(self, config=None):
        super().__init__("line", "Line transport", config)
        self.port = None
        self.requests = 0

    async def serve(self, runtime):
        """Serve exactly one request and stop."""
        server = await asyncio.start_server(
            lambda reader, writer: self._answer(runtime, reader, writer), "127.0.0.1", 0
        )
        self.port = server.sockets[0].getsockname()[1]
        async with server:
            await server.start_serving()
            while self.requests == 0:
                await asyncio.sleep(0.01)

    async def _answer(self, runtime, reader, writer):
        """One request: find the route, invoke it, write what came back."""
        try:
            path = (await reader.readline()).decode().strip()
            raw = (await reader.readline()).decode().strip() or "{}"
            self.requests += 1
            spec = self._route(runtime, path)
            if spec is None:
                answer = {"error": {"code": "not_found", "detail": f"no route {path}"}}
            else:
                answer = await self._call(runtime, spec, json.loads(raw))
            writer.write((json.dumps(answer) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()

    def _route(self, runtime, path):
        """The declared route with this path, wherever it came from."""
        for spec in runtime.route_specs():
            if spec.path == path:
                return spec
        return None

    async def _call(self, runtime, spec, params):
        """Invoke a declared route and render the answer, or the refusal."""
        call = Call(route=spec, params=params, actor={"id": 1, "username": "line"}, source="line")
        try:
            answer = await invoke(call, checker=_allow)
        except CallError as refused:
            return {"error": {"code": refused.code, "detail": refused.detail}}
        return {"answer": answer}


async def _allow(actor, action):
    """A checker that lets everything through: what this file checks is the seam."""
    return True


class ReportsPlugin(BasePlugin):
    """An ordinary plugin: it declares routes and knows nothing about transports."""

    descriptor = PluginDescriptor(id="reports", name="Reports", kind="optional")

    def __init__(self, config=None):
        super().__init__("reports", "Reports", config)

    async def initialize(self):
        return True

    def get_api_routes(self):
        return [
            {"path": "/api/reports", "methods": ["GET"], "handler": self.list_reports},
            {"path": "/api/reports/{report_id}", "methods": ["GET"], "handler": self.one,
             "params": {"report_id": {"type": "int", "in": "path"}},
             "permission": "reports.read"},
        ]

    async def list_reports(self, current_user=None, since=None):
        return {"reports": [], "since": since, "who": current_user["username"]}

    async def one(self, report_id, current_user=None):
        return {"report_id": report_id}

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


def runtime_with(*plugin_classes):
    """A runtime whose catalogue enables exactly these plugins."""
    return create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue={"plugins": [{"id": plugin_class.descriptor.id, "enabled": True}
                                           for plugin_class in plugin_classes]},
        entry_points=[entry_point(plugin_class) for plugin_class in plugin_classes],
    )


async def serving(*plugin_classes):
    """Start a runtime, serve on its transport, and hand back what it built.

    The runtime constructs the plugin -- that is the loader's job and the point
    of it -- so the check asks the runtime for the instance rather than making
    one of its own, which would be a second object serving nothing.
    """
    runtime = runtime_with(*plugin_classes)
    await runtime.start()
    pairs = runtime.transports()
    if not pairs:
        return runtime, None, None
    transport = pairs[0][1]
    serving_task = asyncio.create_task(runtime.serve_all())
    for _ in range(500):
        if transport.port is not None or serving_task.done():
            break
        await asyncio.sleep(0.01)
    return runtime, transport, serving_task


async def ask(transport, path, params=None):
    """One request over the line protocol, and the answer parsed."""
    reader, writer = await asyncio.open_connection("127.0.0.1", transport.port)
    writer.write((path + "\n").encode())
    writer.write((json.dumps(params or {}) + "\n").encode())
    await writer.drain()
    answer = json.loads((await reader.readline()).decode())
    writer.close()
    return answer


async def test_a_route_declared_by_one_plugin_is_answered_by_another_transport():
    """The point of the seam: the route knows nothing about the wire."""
    runtime, transport, serving_task = await serving(ReportsPlugin, LineTransportPlugin)
    answer = await ask(transport, "/api/reports", {"since": "2026-01-01"})
    await serving_task

    assert answer["answer"]["reports"] == []
    assert answer["answer"]["since"] == "2026-01-01"
    assert answer["answer"]["who"] == "line"
    assert [plugin_id for plugin_id, _ in runtime.transports()] == ["line"]


async def test_the_mask_decides_what_a_non_http_transport_may_send():
    """A mask is transport-neutral: it is the route's, not HTTP's."""
    runtime, transport, serving_task = await serving(ReportsPlugin, LineTransportPlugin)
    refused = await ask(transport, "/api/reports/{report_id}", {"report_id": "not a number"})
    accepted = await ask(transport, "/api/reports/{report_id}", {"report_id": 7})
    await serving_task

    assert refused["error"]["code"] == "invalid_request"
    assert "report_id" in refused["error"]["detail"]
    assert accepted["answer"]["report_id"] == 7


async def test_a_path_nobody_declared_is_the_transport_s_own_business():
    """The kernel does not answer not-found; a transport does, in its own words."""
    runtime, transport, serving_task = await serving(ReportsPlugin, LineTransportPlugin)
    answer = await ask(transport, "/api/nothing")
    await serving_task
    assert answer["error"]["code"] == "not_found"


async def test_a_permission_nobody_checks_is_refused_rather_than_passed():
    """A right that nobody decides is worse than a route that does not run."""
    spec = RouteSpec(path="/api/x", methods=("GET",), handler=lambda: {"ok": True},
                     permission="x.read")
    with pytest.raises(CallError) as refused:
        await invoke(Call(route=spec, source="line"))
    assert refused.value.code == "unchecked_permission"


async def test_a_raw_route_is_the_transport_s_own_business():
    """A route that takes the request itself cannot be invoked neutrally."""
    spec = RouteSpec(path="/api/raw", methods=("GET",), handler=lambda request: None,
                     raw_request=True)
    with pytest.raises(CallError) as refused:
        await invoke(Call(route=spec, source="line"))
    assert refused.value.code == "not_invocable"


async def test_a_worker_with_no_transport_serves_nothing_and_says_so(caplog):
    """A process whose work is a job has no way in, and that is a deployment."""
    runtime = runtime_with(ReportsPlugin)
    await runtime.start()
    assert runtime.transports() == []
    caplog.set_level(logging.INFO)
    await serve_all(runtime)
    assert "serves nothing" in caplog.text
    await runtime.stop()


async def test_the_runtime_runs_a_transport_and_stops_afterwards():
    """run_forever is start, serve, stop -- and a transport that returns ends it."""
    runtime = runtime_with(ReportsPlugin, LineTransportPlugin)
    running = asyncio.create_task(runtime.run_forever())
    for _ in range(500):
        pairs = runtime.transports()
        if pairs and pairs[0][1].port is not None:
            break
        await asyncio.sleep(0.01)
    transport = runtime.transports()[0][1]
    answer = await ask(transport, "/api/reports")
    await running
    assert answer["answer"]["reports"] == []
    assert runtime.get_plugin("reports") is None  # stopped, and cleaned up
