"""The plugin runtime, raised without an application.

Task keepup-20. A plugin route is data, and the framework turns it into a
FastAPI endpoint: it injects the current user, unpacks the path and the query
into the handler's arguments, parses the body, and writes the call to the
incoming request audit. All of that lives in one closure inside
register_plugin_routes, in eight branches that differ by small things -- which
method goes into the audit, whether the user is passed, whether the body is
parsed -- and small things are where its bugs were.

Until this file the branches were checked by the application: the test that
raises the runtime for real loads this deployment's plugins, so it stayed in
the application's repository. In the framework's own repository there is no
application, and what was left of the runtime was twenty-two per cent.

So the plugins here are made up. They are written into a temporary directory
and loaded from it by the same manager that loads real ones, their routes are
registered by the same function, and the requests are real requests through a
TestClient. Nothing of an application is imported, read or started.

    python3 -m pytest keepup/tests/plugin_runtime_tests.py -v
"""

import asyncio
import json
import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from keepup import audit
from keepup.auth.dependencies import get_panel_user
from keepup.plugins import admin, registry, routes
from keepup.plugins.base import (
    OUTCOME_DISABLED,
    OUTCOME_INITIALIZED,
    BasePlugin,
    PluginManager,
)


# --- the made-up plugin and the harness ---------------------------------------

#: Whoever the framework says is calling. A test that cares about signing in
#: says so by not using this.
SOMEBODY = {"id": 7, "username": "tester", "role": "admin"}


class ProbePlugin(BasePlugin):
    """A plugin that publishes exactly the routes a test hands it."""

    def __init__(self, routes, plugin_id="probe"):
        super().__init__(plugin_id, "Probe", {})
        self.initialized = True
        self._routes = routes

    async def initialize(self):
        return True

    def get_api_routes(self):
        return self._routes

    def get_handlers(self):
        return {}


def running(routes, signed_in_as=SOMEBODY):
    """An application with those routes on it, and a client to call them with.

    The routes go through register_plugin_routes, which is the only place a
    wrapper is built -- there is no way to reach one otherwise, and no reason
    to want one: what is worth checking is the endpoint a plugin actually gets.

    The user arrives by overriding the dependency rather than by minting a
    token: a token would check the sign-in, which has tests of its own, and
    would tie these to how it works. The one test that does check the sign-in
    passes signed_in_as=None and overrides nothing.

    Server exceptions are not re-raised, so a handler blowing up answers 500
    here exactly as it does in a real process.
    """
    plugin = ProbePlugin(routes)
    # Never read: the routes are handed over rather than loaded from a file.
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
    """The audit records into this buffer, and no further.

    Writing it out is a background flush to a database, which this file does
    not have and does not need: what a test asks of the audit is the outcome of
    the call it has just made.
    """
    audit.incoming_requests_buffer.clear()
    yield audit.incoming_requests_buffer
    audit.incoming_requests_buffer.clear()


@pytest.fixture
def values_kept(monkeypatch):
    """Let the test read what was written, not the shape of it.

    The audit hides values by default (task keepup-11) -- right for a
    deployment, useless for a test asking who the caller was.
    """
    monkeypatch.setattr(audit, "redact", audit.keep_as_is)


def the_record(audited):
    """The single audit record of the request just made."""
    assert len(audited) == 1, f"expected one audited call, got {len(audited)}"
    return next(iter(audited.values()))


# --- what reaches the handler -------------------------------------------------


async def a_thing(current_user, thing_id, verbose=None):
    return {"thing_id": thing_id, "verbose": verbose, "who": current_user["username"]}


async def a_summary(current_user, page=None):
    return {"page": page, "who": current_user["username"]}


async def anybody(kind=None):
    return {"kind": kind}


THING = {"path": "/api/probe/things/{thing_id}", "methods": ["GET"], "handler": a_thing}
SUMMARY = {"path": "/api/probe/summary", "methods": ["GET"], "handler": a_summary}
OPEN = {"path": "/api/probe/open", "methods": ["GET"], "handler": anybody,
        "require_auth": False}


def test_the_path_the_query_and_the_user_reach_the_handler_by_name():
    """A plugin handler takes plain arguments, never a Request -- this is why."""
    answer = running([THING]).get("/api/probe/things/5?verbose=1").json()

    assert answer == {"thing_id": "5", "verbose": "1", "who": "tester"}


def test_a_route_without_path_parameters_still_gets_the_query_and_the_user():
    answer = running([SUMMARY]).get("/api/probe/summary?page=2").json()

    assert answer == {"page": "2", "who": "tester"}


def test_the_path_wins_over_the_query():
    """`GET /api/probe/things/5?thing_id=99` used to reach the handler with 99.

    Every rule built on the path -- a proxy's ACL, a reading of the audit
    afterwards -- still said 5. route_parameters_tests.py holds the order of
    the two updates in the source, for the branches a request does not walk;
    here it is a request.
    """
    answer = running([THING]).get("/api/probe/things/5?thing_id=99").json()

    assert answer["thing_id"] == "5"


def test_a_public_route_is_called_with_no_user_at_all():
    """Not with an empty one: the handler of a public route takes no user."""
    answer = running([OPEN], signed_in_as=None).get("/api/probe/open?kind=x").json()

    assert answer == {"kind": "x"}


def test_a_parameter_the_route_does_not_take_is_a_bad_request_not_a_crash():
    """It used to raise TypeError inside the wrapper, which the client saw as 500.

    Every one of those wrote an audit row, so `?nosuch=1` in a loop filled the
    database and the log at once -- and on a public route anybody could send
    it. Checked here through the whole wrapper, where the refusal has to travel
    out as an HTTP answer rather than as an exception.
    """
    response = running([OPEN], signed_in_as=None).get("/api/probe/open?nosuch=1")

    assert response.status_code == 400
    assert "nosuch" in response.json()["detail"]


# --- the body of a request ----------------------------------------------------


async def a_written_thing(request, current_user, thing_id):
    return {"body": request, "thing_id": thing_id}


async def a_written_note(request, current_user):
    return {"body": request}


async def an_uploaded_file(request, current_user):
    return {"size": len(await request.body())}


WRITTEN_THING = {"path": "/api/probe/things/{thing_id}", "methods": ["POST"],
                 "handler": a_written_thing}
NOTE = {"path": "/api/probe/notes", "methods": ["POST"], "handler": a_written_note}
UPLOAD = {"path": "/api/probe/files", "methods": ["POST"],
          "handler": an_uploaded_file, "is_upload": True}


def test_a_json_body_reaches_the_handler_parsed_and_beside_the_path():
    answer = running([WRITTEN_THING]).post("/api/probe/things/5",
                                           json={"name": "x"}).json()

    assert answer == {"body": {"name": "x"}, "thing_id": "5"}


def test_a_body_that_does_not_say_it_is_json_is_not_read():
    """The content type decides, not the bytes.

    A handler written for `request` as a dictionary would otherwise get a
    string the first time somebody posted a form, and break inside the plugin
    rather than at the edge.
    """
    answer = running([NOTE]).post("/api/probe/notes", content="name=x",
                                  headers={"content-type": "text/plain"}).json()

    assert answer == {"body": {}}


def test_a_json_body_that_does_not_parse_becomes_an_empty_one():
    """Deliberate, and worth knowing: the plugin is handed {} and decides.

    The framework does not refuse on the plugin's behalf -- a handler that
    needs a field says so by missing it, and one that needs nothing answers.
    """
    answer = running([NOTE]).post("/api/probe/notes", content="{oops",
                                  headers={"content-type": "application/json"}).json()

    assert answer == {"body": {}}


# --- the content type, and what an unreadable body leaves behind ---------------


def runtime_warnings(caplog):
    """What the runtime warned about, in order."""
    return [record.getMessage() for record in caplog.records
            if record.name == "keepup.plugins.routes"
            and record.levelno == logging.WARNING]


@pytest.mark.parametrize("header,expected", [
    ("application/json", "application/json"),
    ("application/json; charset=utf-8", "application/json"),
    ("application/json;charset=UTF-8", "application/json"),
    ("Application/JSON", "application/json"),
    ("  application/json  ", "application/json"),
    ("application/x-www-form-urlencoded", "application/x-www-form-urlencoded"),
    ("", ""),
    (None, ""),
])
def test_the_media_type_is_the_type_without_its_parameters(header, expected):
    """The header is not the type, and comparing it as one lost bodies.

    A charset is written by default by a great many clients, and the type is
    case-insensitive by the standard -- so `application/json; charset=utf-8`
    used to be a different string and the body went nowhere.
    """
    assert routes.media_type(header) == expected


@pytest.mark.parametrize("header", [
    "application/json; charset=utf-8",
    "application/json;charset=UTF-8",
    "Application/JSON",
])
def test_a_body_reaches_the_handler_whatever_the_header_is_spelled_like(header):
    answer = running([NOTE]).post("/api/probe/notes", content=b'{"name": "x"}',
                                  headers={"content-type": header}).json()

    assert answer == {"body": {"name": "x"}}


def test_a_body_that_does_not_parse_is_named_in_the_log(caplog):
    """The whole point of the task: an empty body used to mean three things.

    There was none, the type was not JSON, or it arrived and could not be read
    -- and the handler, unable to tell them apart, answered about a missing
    field the client had sent.
    """
    client = running([NOTE])
    body = b'{"password": "hunter2"'

    with caplog.at_level(logging.WARNING):
        answer = client.post("/api/probe/notes", content=body,
                             headers={"content-type": "application/json"}).json()

    assert answer == {"body": {}}, "the handler is still handed {} and decides"
    warned = runtime_warnings(caplog)
    assert len(warned) == 1, warned
    assert "/api/probe/notes" in warned[0], "the route the call was for"
    assert "POST" in warned[0]
    assert "application/json" in warned[0], "what the body claimed to be"
    assert f"length={len(body)}" in warned[0], "a body arrived, and this long"
    assert "JSONDecodeError" in warned[0], "and this is why it was not read"


def test_the_body_itself_is_never_written_to_the_log(caplog):
    """Passwords and keys go through these routes.

    The length says a body arrived without saying what was in it. The reason
    names a position and at most the single byte that would not decode, which
    is what makes an encoding fault diagnosable without quoting the request.
    """
    client = running([NOTE])

    with caplog.at_level(logging.WARNING):
        client.post("/api/probe/notes", content=b'{"password": "hunter2"',
                    headers={"content-type": "application/json"})

    assert "hunter2" not in "\n".join(runtime_warnings(caplog))


def test_a_body_in_another_encoding_says_so(caplog):
    """The fault this task exists for.

    A client on a Russian Windows built its body out of strings the machine
    hands over in its code page. JSON is UTF-8 by definition, the parse fell
    over, and the client read `400 id is required` about an id it had sent
    correctly. Neither log said the word "encoding".
    """
    client = running([NOTE])
    # A word in a single-byte code page, written as the bytes it is rather than
    # as letters: the package is in English, and what matters here is that no
    # UTF-8 decoder accepts these bytes -- 0xe2 opens a three-byte sequence and
    # 0xf1 is not a continuation of one.
    in_the_code_page = b'{"name": "\xe2\xf1\xf2"}'

    with caplog.at_level(logging.WARNING):
        client.post("/api/probe/notes", content=in_the_code_page,
                    headers={"content-type": "application/json"})

    warned = runtime_warnings(caplog)
    assert len(warned) == 1, warned
    assert "UnicodeDecodeError" in warned[0], (
        "the one word three rounds of searching went without")


def test_a_request_with_no_body_at_all_stays_silent(caplog):
    """Ordinary, not an incident: plenty of writes take everything in the path.

    Warning about them would bury the lines this exists to produce.
    """
    client = running([NOTE])

    with caplog.at_level(logging.WARNING):
        answer = client.post("/api/probe/notes",
                             headers={"content-type": "application/json"}).json()

    assert answer == {"body": {}}
    assert runtime_warnings(caplog) == []


def test_a_body_of_another_type_stays_silent(caplog):
    """A form is not a broken JSON body, it is a different thing entirely."""
    client = running([NOTE])

    with caplog.at_level(logging.WARNING):
        client.post("/api/probe/notes", content="name=x",
                    headers={"content-type": "application/x-www-form-urlencoded"})

    assert runtime_warnings(caplog) == []


def test_a_json_structured_type_is_not_read_but_is_named(caplog):
    """`+json` means JSON by construction (RFC 6839) and is still not read.

    A route declares what it accepts, and a media type nobody declared must not
    start arriving because the runtime grew lenient. But it does not vanish
    quietly either -- that silence is the bug this task is about.
    """
    client = running([NOTE])

    with caplog.at_level(logging.WARNING):
        answer = client.post("/api/probe/notes", content=b'{"name": "x"}',
                             headers={"content-type": "application/merge-patch+json"}).json()

    assert answer == {"body": {}}
    warned = runtime_warnings(caplog)
    assert len(warned) == 1, warned
    assert "application/merge-patch+json" in warned[0]
    assert "/api/probe/notes" in warned[0]


def test_an_upload_is_handed_the_request_itself():
    """The one case where a plugin handler sees a framework object.

    Reading a file into memory to pass it as a value is the thing an upload
    route exists to avoid.
    """
    answer = running([UPLOAD]).post("/api/probe/files", content=b"0123456789").json()

    assert answer == {"size": 10}


@pytest.mark.parametrize("method", ["PUT", "PATCH"])
def test_put_and_patch_go_through_the_same_branch_and_keep_their_own_name(method, audited):
    """The branch is chosen by the set of methods; the audit records the first.

    A route declared as PATCH used to be worth checking precisely because the
    wrapper's own name says POST.
    """
    route = dict(NOTE, methods=[method])
    client = running([route])

    answer = client.request(method, "/api/probe/notes", json={"name": "x"}).json()

    assert answer == {"body": {"name": "x"}}
    assert the_record(audited)["method"] == method


async def a_removed_thing(current_user, thing_id):
    return {"deleted": thing_id}


async def a_cleared_list(current_user, older_than=None):
    return {"older_than": older_than}


def test_delete_reaches_the_handler_with_the_path_parameter(audited):
    client = running([{"path": "/api/probe/things/{thing_id}", "methods": ["DELETE"],
                       "handler": a_removed_thing}])

    assert client.delete("/api/probe/things/5").json() == {"deleted": "5"}
    assert the_record(audited)["method"] == "DELETE"


def test_delete_without_a_path_parameter_still_gets_the_query():
    client = running([{"path": "/api/probe/things", "methods": ["DELETE"],
                       "handler": a_cleared_list}])

    assert client.delete("/api/probe/things?older_than=7").json() == {"older_than": "7"}


# --- every shape unpacks the same way ------------------------------------------
#
# Task keepup-21. Writes without a path parameter used to unpack nothing at
# all: two of the eight copies of the wrapper had drifted, and nothing pointed
# at the difference -- a correct request behaves identically either way.


async def a_noted_draft(request, current_user, draft=None):
    return {"body": request, "draft": draft}


async def an_uploaded_file_of_a_kind(request, current_user, kind=None):
    return {"size": len(await request.body()), "kind": kind}


DRAFT = {"path": "/api/probe/notes", "methods": ["POST"], "handler": a_noted_draft}


def test_a_write_without_a_path_parameter_gets_the_query_as_well():
    """It used to arrive empty, and the caller had no way to tell.

    An argument the handler declares and the framework silently never passes
    reads, from inside the plugin, as "the client did not send it".
    """
    answer = running([DRAFT]).post("/api/probe/notes?draft=1", json={"name": "x"}).json()

    assert answer == {"body": {"name": "x"}, "draft": "1"}


def test_a_write_without_a_path_parameter_refuses_what_it_does_not_take():
    """The refusal is the whole point of declaring what a route accepts.

    `POST /api/probe/notes?nosuch=1` used to answer 200: a route with a mask
    was not checked against it at all if its path had no parameter, which is
    the one case where the mask was supposed to do the work of a proxy.
    """
    response = running([DRAFT]).post("/api/probe/notes?nosuch=1", json={})

    assert response.status_code == 400
    assert "nosuch" in response.json()["detail"]


def test_an_upload_without_a_path_parameter_gets_the_query_as_well():
    """The case an application had already worked around.

    The pool an image belongs in travels as `?kind=`, and the handler used to
    read it off the request itself, with a comment saying why: the wrapper did
    not unpack it. It arrives as an argument now, like everywhere else.
    """
    route = {"path": "/api/probe/files", "methods": ["POST"],
             "handler": an_uploaded_file_of_a_kind, "is_upload": True}

    answer = running([route]).post("/api/probe/files?kind=face", content=b"0123456789").json()

    assert answer == {"size": 10, "kind": "face"}


# --- methods, schema and the fallback -----------------------------------------


def test_a_method_the_runtime_cannot_wrap_answers_that_it_is_not_implemented():
    """Registered rather than dropped, so the route exists and says why.

    A dropped route answers 404, which reads as "no such thing here" and sends
    whoever declared it looking at the path. 501 names the method.
    """
    client = running([{"path": "/api/probe/odd", "methods": ["OPTIONS"],
                       "handler": anybody}])

    response = client.options("/api/probe/odd")

    assert response.status_code == 501
    assert "OPTIONS" in response.json()["detail"]


def test_a_route_kept_out_of_the_schema_answers_all_the_same():
    """Out of the published API, not out of the application.

    The two are easy to confuse, and confusing them either publishes an
    internal callback or silently unregisters a working route.
    """
    client = running([dict(SUMMARY, include_in_schema=False), THING])

    assert client.get("/api/probe/summary").status_code == 200

    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/probe/summary" not in paths
    assert "/api/probe/things/{thing_id}" in paths


# --- the outcome, as the audit records it -------------------------------------


async def a_plain_answer(current_user):
    return "just a string"


async def a_refusal(current_user):
    raise HTTPException(status_code=409, detail="that order is already paid")


async def a_breakage(current_user):
    raise RuntimeError("no database")


def test_a_successful_call_is_recorded_with_its_path_and_its_status(audited):
    running([THING]).get("/api/probe/things/5")

    record = the_record(audited)
    assert record["method"] == "GET"
    # The declared path, not the one that arrived: a table of endpoints is only
    # readable while /api/probe/things/5 and .../6 are one line rather than two.
    assert record["endpoint"] == "/api/probe/things/{thing_id}"
    assert record["http_status"] == 200
    # Never written rather than written empty: the outcome is recorded once,
    # and a row with a status and no error is what a successful call looks like.
    assert "error_message" not in record


def test_an_answer_that_is_not_a_dictionary_reaches_the_client_as_it_is(audited, values_kept):
    """And is recorded under a name, because the column holds an object."""
    answer = running([{"path": "/api/probe/plain", "methods": ["GET"],
                       "handler": a_plain_answer}]).get("/api/probe/plain")

    assert answer.json() == "just a string"
    assert the_record(audited)["response_data"] == {"data": "just a string"}


def test_a_refusal_keeps_its_status_and_its_reason(audited):
    """The handler's own answer, not a 500 with the reason in the log."""
    response = running([{"path": "/api/probe/refuse", "methods": ["GET"],
                         "handler": a_refusal}]).get("/api/probe/refuse")

    assert response.status_code == 409
    assert response.json()["detail"] == "that order is already paid"

    record = the_record(audited)
    assert record["http_status"] == 409
    # The wrapper records the detail, and then the exception travels on out of
    # log_api_request(), which records it again with the status in front:
    # "409: that order is already paid". Nothing is lost, so the reason is
    # checked by what it contains rather than by its exact spelling.
    assert "that order is already paid" in record["error_message"]


def test_an_unhandled_exception_is_five_hundred_and_says_what_it_was(audited):
    """The text stays in the audit and not in the answer.

    A caller gets the framework's 500; the reason is kept where the person
    reading the incident afterwards will look for it.
    """
    response = running([{"path": "/api/probe/break", "methods": ["GET"],
                         "handler": a_breakage}]).get("/api/probe/break")

    assert response.status_code == 500

    record = the_record(audited)
    assert record["http_status"] == 500
    assert record["error_message"] == "no database"


def test_a_public_call_is_recorded_as_anonymous(audited, values_kept):
    """There is no user to name, and the row says so rather than leaving a gap."""
    running([OPEN], signed_in_as=None).get("/api/probe/open?kind=x")

    written = the_record(audited)["request_data"]
    assert written["username"] == "anonymous"
    assert written["user_id"] is None


def test_a_call_with_a_user_is_recorded_against_that_user(audited, values_kept):
    running([THING]).get("/api/probe/things/5")

    written = the_record(audited)["request_data"]
    assert (written["user_id"], written["username"]) == (7, "tester")
    assert written["path_params"] == {"thing_id": "5"}


# --- authentication and a raw route -------------------------------------------


def test_a_route_that_did_not_declare_itself_public_refuses_a_caller_with_nothing():
    """The one test here that does not override the sign-in.

    Without it, every other test in this file would prove only that a route
    called with an overridden dependency reaches its handler -- which says
    nothing at all about whether it would have asked.
    """
    response = running([SUMMARY], signed_in_as=None).get("/api/probe/summary")

    assert response.status_code == 401


async def a_raw_route(request):
    body = await request.json()
    return {"asked": body["ask"]}


def test_a_raw_route_is_handed_the_request_and_nothing_else(audited):
    """For a surface with a wire format of its own -- a gateway answering in
    somebody else's shape, or streaming events.

    Its body is deliberately not audited: it carries what users typed. A raw
    route authenticates inside the handler, which is why declaring require_auth
    on one is refused outright (route_parameters_tests.py).
    """
    client = running([{"path": "/api/probe/raw", "methods": ["POST"],
                       "handler": a_raw_route, "raw_request": True}],
                     signed_in_as=None)

    answer = client.post("/api/probe/raw", json={"ask": "who goes there"})

    assert answer.json() == {"asked": "who goes there"}
    assert audited == {}, "the body of a raw route reached the audit"


# --- initialisation: the file, the order, the self-checks ---------------------

#: A plugin as a deployment has one: a file in the directory the application
#: named, a class whose name is derived from the id, and a route of its own.
PLUGIN_SOURCE = '''
from keepup.plugins.base import BasePlugin


class {klass}Plugin(BasePlugin):
    def __init__(self, config=None):
        super().__init__("{plugin_id}", "{plugin_id}", config)

    async def initialize(self):
        self.initialized = True
        return True

    async def answer(self, current_user):
        return {{"plugin": "{plugin_id}", "config": self.config}}

    def get_api_routes(self):
        return [{{"path": "/api/{plugin_id}", "methods": ["GET"], "handler": self.answer}}]

    def get_handlers(self):
        return {{}}
'''


def a_plugin_file(directory, plugin_id):
    (directory / f"{plugin_id}.py").write_text(
        PLUGIN_SOURCE.format(klass=plugin_id.capitalize(), plugin_id=plugin_id),
        encoding="utf-8")


def a_deployment(tmp_path, plugins, monkeypatch):
    """Write the plugin files and the configuration that declares them.

    Returns the manager and the path of the file, ready for initialize_plugins.
    """
    for plugin in plugins:
        a_plugin_file(tmp_path, plugin["id"])
    config_path = tmp_path / "modules.json"
    config_path.write_text(json.dumps({"plugins": plugins}), encoding="utf-8")

    # An override is read from the deployment's database, which this file does
    # not have; without them the file and the environment decide, which is the
    # behaviour that existed before the panel could.
    monkeypatch.setattr(registry, "read_plugin_overrides", lambda: {})
    return PluginManager(plugins_dir=str(tmp_path)), str(config_path)


def test_initialisation_runs_what_the_file_enables_and_marks_the_rest(tmp_path, monkeypatch):
    """The whole path from a file on disk to an answering route.

    A plugin that is switched off is recorded rather than passed over: from
    outside, a plugin nobody enabled and a plugin that fell over look the same.
    """
    manager, config_path = a_deployment(tmp_path, [
        {"id": "alpha", "name": "Alpha", "enabled": True, "config": {"season": "winter"}},
        {"id": "beta", "name": "Beta", "enabled": False},
    ], monkeypatch)

    app = FastAPI()
    asyncio.run(registry.initialize_plugins(app, manager, config_path=config_path,
                                            environ={}))

    assert manager.get_outcome("alpha")[0] == OUTCOME_INITIALIZED
    assert manager.get_outcome("beta")[0] == OUTCOME_DISABLED

    app.dependency_overrides[get_panel_user] = lambda: SOMEBODY
    client = TestClient(app, raise_server_exceptions=False)

    # Its route is on the application, and it was given its own config section.
    assert client.get("/api/alpha").json() == {"plugin": "alpha",
                                               "config": {"season": "winter"}}
    # The one that was switched off has no routes to answer with.
    assert client.get("/api/beta").status_code == 404


def test_the_deployment_can_enable_what_the_file_leaves_off(tmp_path, monkeypatch):
    """PLUGINS_ENABLE belongs to one stand; the file is committed and shared."""
    manager, config_path = a_deployment(tmp_path, [
        {"id": "beta", "name": "Beta", "enabled": False},
    ], monkeypatch)

    asyncio.run(registry.initialize_plugins(FastAPI(), manager, config_path=config_path,
                                            environ={"PLUGINS_ENABLE": "beta"}))

    assert manager.get_outcome("beta")[0] == OUTCOME_INITIALIZED


def test_an_id_nobody_declared_is_reported_and_not_fatal(tmp_path, monkeypatch):
    """A typo in a deployment must not take the server down with it."""
    manager, config_path = a_deployment(tmp_path, [
        {"id": "alpha", "name": "Alpha", "enabled": True},
    ], monkeypatch)

    resolution = asyncio.run(registry.initialize_plugins(
        FastAPI(), manager, config_path=config_path,
        environ={"PLUGINS_ENABLE": "ghost"}))

    assert resolution.unknown == ["ghost"]
    assert manager.get_outcome("alpha")[0] == OUTCOME_INITIALIZED


def test_a_configuration_that_cannot_be_read_does_not_stop_the_start(tmp_path, monkeypatch):
    """It comes back with nothing, and the application goes on without plugins.

    Which is the right trade only because the state is visible afterwards: the
    admin list shows each declared plugin and what became of it.
    """
    manager, _ = a_deployment(tmp_path, [], monkeypatch)

    assert asyncio.run(registry.initialize_plugins(
        FastAPI(), manager, config_path=str(tmp_path / "nothing.json"),
        environ={})) is None


class OrderedPlugin(BasePlugin):
    """Writes down when its turn came."""

    def __init__(self, plugin_id, priority, seen):
        super().__init__(plugin_id, plugin_id, {})
        self.set_priority(priority)
        self._seen = seen

    async def initialize(self):
        self._seen.append(self.plugin_id)
        self.initialized = True
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


def test_plugins_come_up_in_priority_order_and_ties_keep_the_declared_one():
    """Priority is why one plugin may depend on another being up already.

    Declared and then not applied for a long time -- the order was whatever
    the dictionary happened to hold.
    """
    seen = []
    manager = PluginManager(plugins_dir="")
    for plugin_id, priority in (("late", 50), ("first", 1), ("tie_a", 10), ("tie_b", 10)):
        manager.plugins[plugin_id] = OrderedPlugin(plugin_id, priority, seen)

    assert asyncio.run(manager.initialize_plugins()) is True
    assert seen == ["first", "tie_a", "tie_b", "late"]


class SelfCheckingPlugin(BasePlugin):
    """A plugin with a post-construct check that may refuse to pass."""

    def __init__(self, plugin_id, initialized=True, blows_up=False):
        super().__init__(plugin_id, plugin_id, {})
        self.initialized = initialized
        self.checked = False
        self._blows_up = blows_up

    async def initialize(self):
        return True

    async def post_construct(self):
        if self._blows_up:
            raise RuntimeError("the port is not listening yet")
        self.checked = True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


async def test_the_self_check_runs_only_for_the_plugins_that_came_up():
    """post_construct needs a live server; a plugin that has no tables behind
    it has nothing to check against one."""
    manager = PluginManager(plugins_dir="")
    came_up = SelfCheckingPlugin("up")
    never_did = SelfCheckingPlugin("down", initialized=False)
    manager.plugins = {"up": came_up, "down": never_did}

    await registry.run_post_construct_processors(manager)

    assert came_up.checked is True
    assert never_did.checked is False


async def test_a_self_check_that_blows_up_does_not_take_the_others_with_it():
    """It runs after the server is up, so its failure must not unmake it."""
    manager = PluginManager(plugins_dir="")
    broken = SelfCheckingPlugin("broken", blows_up=True)
    healthy = SelfCheckingPlugin("healthy")
    manager.plugins = {"broken": broken, "healthy": healthy}

    await registry.run_post_construct_processors(manager)

    assert healthy.checked is True


# --- the administrative surface -----------------------------------------------


@pytest.fixture
def declared(tmp_path, monkeypatch):
    """Declare plugins in a configuration file the runtime will read.

    The path is a module constant -- the application supplies it once at start
    -- so it is pointed at a temporary file rather than at anybody's config/.
    """
    def declare(config):
        config_path = tmp_path / "modules.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        monkeypatch.setattr(admin, "MODULES_CONFIG_PATH", str(config_path))
        return config_path

    return declare


A_PANEL = {
    "plugins": [{"id": "alpha", "name": "Alpha", "config": {"section": "orders"}},
                {"id": "beta", "name": "Beta"}],
    "roles": [{"name": "client", "plugins": ["alpha", "beta"]}],
}


async def test_a_user_is_offered_what_the_role_allows_and_what_actually_came_up(declared):
    """A role's list is visibility, not enablement.

    A plugin granted to the role but not running has no routes to advertise,
    and offering it gives the panel a section that answers 404.
    """
    declared(A_PANEL)
    manager = PluginManager(plugins_dir="")
    manager.loaded_plugins["alpha"] = ProbePlugin([], plugin_id="alpha")

    offered = await admin.get_plugins(manager, {"role": "client"})

    assert offered == {"plugins": [{"id": "alpha", "name": "Alpha",
                                    "config": {"section": "orders"}}]}


async def test_a_role_the_file_does_not_describe_is_offered_nothing(declared):
    declared(A_PANEL)
    manager = PluginManager(plugins_dir="")
    manager.loaded_plugins["alpha"] = ProbePlugin([], plugin_id="alpha")

    assert await admin.get_plugins(manager, {"role": "stranger"}) == {"plugins": []}


async def test_a_plugin_nobody_declared_cannot_be_switched_from_the_panel(declared):
    """Storing a decision about a plugin that does not exist is a decision
    nothing would ever read."""
    declared(A_PANEL)

    with pytest.raises(HTTPException) as refused:
        await admin.set_plugin_enabled("ghost", {"enabled": True}, admin={"id": 1})

    assert refused.value.status_code == 404


async def test_a_plugin_the_deployment_decided_on_cannot_be_switched(declared, monkeypatch):
    """The environment wins at the next start, so the panel would be lying.

    The administrator switches it, restarts, and nothing changes -- which is
    the trap this refusal exists to avoid. It names the reason instead.
    """
    declared(A_PANEL)
    monkeypatch.setenv("PLUGINS_DISABLE", "alpha")

    with pytest.raises(HTTPException) as refused:
        await admin.set_plugin_enabled("alpha", {"enabled": True}, admin={"id": 1})

    assert refused.value.status_code == 409
    assert "PLUGINS_DISABLE" in refused.value.detail


async def test_a_value_that_is_not_a_flag_is_refused_before_anything_is_stored(declared, monkeypatch):
    stored = []
    monkeypatch.setattr(admin, "write_plugin_override",
                        lambda *args, **kwargs: stored.append(args))
    declared(A_PANEL)

    with pytest.raises(HTTPException) as refused:
        await admin.set_plugin_enabled("alpha", {"enabled": "yes"}, admin={"id": 1})

    assert refused.value.status_code == 400
    assert stored == []


async def test_switching_a_plugin_is_recorded_and_waits_for_the_next_start(declared, monkeypatch):
    """A plugin's routes are built once, at start-up, and are not taken off a
    running application -- so the answer says so rather than implying it worked."""
    stored = []
    monkeypatch.setattr(admin, "write_plugin_override",
                        lambda plugin_id, enabled, changed_by=None:
                        stored.append((plugin_id, enabled, changed_by)))
    declared(A_PANEL)

    answer = await admin.set_plugin_enabled("alpha", {"enabled": False},
                                               admin={"id": 42})

    assert answer == {"success": True, "plugin_id": "alpha", "enabled": False,
                      "pending_restart": True}
    assert stored == [("alpha", False, 42)]


async def test_clearing_gives_the_decision_back_to_the_file(declared, monkeypatch):
    cleared = []
    monkeypatch.setattr(admin, "clear_plugin_override", cleared.append)
    declared(A_PANEL)

    answer = await admin.clear_plugin_enabled("alpha", admin={"id": 42})

    assert answer == {"success": True, "plugin_id": "alpha"}
    assert cleared == ["alpha"]


def test_a_database_that_cannot_answer_does_not_keep_the_server_from_starting(monkeypatch):
    """Without the overrides the file and the environment still decide, which
    is what happened before the panel could decide anything."""
    class Unreachable:
        @staticmethod
        def execute(*args, **kwargs):
            raise RuntimeError("could not connect to the database")

    monkeypatch.setattr(admin, "DatabaseManagerV2", Unreachable)

    assert admin.read_plugin_overrides() == {}


# --- the three layers the runtime is split into --------------------------------
#
# Task keepup-21. The split is only worth having while it holds: two modules
# that import each other are one module written in two files.


def imported_modules(module_name: str):
    """The modules a module of the plugin runtime imports."""
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "plugins"
              / f"{module_name}.py").read_text(encoding="utf-8")
    taken = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            taken.add(node.module)
            for alias in node.names:
                taken.add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                taken.add(alias.name)
    return taken


@pytest.mark.parametrize("module", ["routes", "admin"])
def test_neither_half_knows_the_other_or_the_assembly(module):
    """Routes and the administrative surface are strangers to each other.

    And both are strangers to the assembly that uses them: a module importing
    the one that imports it is a cycle waiting for the next name to move.
    """
    forbidden = {"routes", "admin", "registry"} - {module}
    taken = imported_modules(module)

    for name in sorted(forbidden):
        assert f"keepup.plugins.{name}" not in taken, \
            f"keepup.plugins.{module} imports keepup.plugins.{name}"


def test_the_package_still_publishes_one_address():
    """An application imports from registry, wherever the code actually lives.

    The split is internal. A published path is a promise of the package, and
    breaking one to tidy up inside would charge the people who installed it
    for the tidying.
    """
    for name in ("accepted_params", "clear_plugin_override", "initialize_plugins",
                 "raw_request_wrapper", "read_plugin_overrides",
                 "register_plugin_admin_routes", "register_plugin_routes",
                 "run_post_construct_processors", "write_plugin_override"):
        assert hasattr(registry, name), f"keepup.plugins.registry lost {name}"
        assert name in registry.__all__, f"{name} is no longer declared public"
