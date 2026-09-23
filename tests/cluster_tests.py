"""The cluster of replicas: registry, commands, stopping and restarting.

The rules are checked as pure functions; the registry and the commands against
a real SQLite database built from the production DDL, because what matters
there is what the database does -- a command claimed twice, a row updated
rather than duplicated. Two controllers with different instance ids share one
database the way two replicas do.

    python3 -m pytest keepup/tests/cluster_tests.py -v
"""

import asyncio
import signal
import sys
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from keepup import cluster, schema
from keepup.api_versions import ApiVersionMiddleware
from keepup.auth.dependencies import get_current_user
from keepup.db import DatabaseManagerV2, db_config

NOW = datetime(2026, 9, 18, 12, 0, 0)
ADMIN = {"id": 1, "username": "admin", "role": "ADMIN"}


def member(instance_id, state=cluster.STATE_SERVING, seen=NOW, can_restart=True, blocker=None):
    return {"instance_id": instance_id, "state": state, "last_seen": seen,
            "can_restart": can_restart, "restart_blocker": blocker}


@pytest.fixture(autouse=True)
def no_controller_left_behind():
    cluster.set_controller(None)
    yield
    cluster.set_controller(None)


@pytest.fixture
def fresh_database(tmp_path, monkeypatch):
    """Its own database: the shared engine is cached on the class."""
    DatabaseManagerV2.dispose()
    monkeypatch.setattr(db_config, "db_path", str(tmp_path / "cluster.db"), raising=False)
    monkeypatch.setattr(db_config, "db_type", "sqlite", raising=False)
    schema.init_db()
    yield
    DatabaseManagerV2.dispose()


class FakeScheduler:
    running = True

    def __init__(self):
        self.calls = []

    def pause(self):
        self.calls.append("pause")

    def resume(self):
        self.calls.append("resume")


class Clock:
    def __init__(self, moment=NOW):
        self.moment = moment

    def __call__(self):
        return self.moment


def controller(instance_id, clock=None, scheduler=None, blocker="", running=("servershare",),
               pending=(), **kwargs):
    async def picture():
        return cluster.PluginPicture(list(running), list(pending))
    return cluster.ReplicaController(
        plugins=picture, build="0.0.1 #17", instance_id=instance_id,
        clock=clock or Clock(), scheduler=lambda: scheduler, blocker=blocker, **kwargs)


def run(coroutine):
    return asyncio.run(coroutine)


# ================================== rules ==================================

def test_a_replica_is_gone_after_three_missed_heartbeats():
    limit = timedelta(seconds=cluster.HEARTBEAT_SECONDS * cluster.MISSED_HEARTBEATS)
    assert cluster.is_alive(NOW - limit, NOW)
    assert not cluster.is_alive(NOW - limit - timedelta(seconds=1), NOW)
    assert not cluster.is_alive(None, NOW)


def test_sqlite_text_timestamps_are_read_as_times():
    assert cluster.is_alive((NOW - timedelta(seconds=5)).isoformat(sep=" "), NOW)


def test_a_process_under_a_supervising_parent_cannot_restart_itself():
    reason = cluster.restart_blocker(parent_process=lambda: object(), orig_argv=["uvicorn"])
    assert reason and "reload" in reason


def test_a_process_of_its_own_can_restart():
    assert cluster.restart_blocker(parent_process=lambda: None,
                                   orig_argv=["python", "-m", "uvicorn", "app.main:app"]) is None


@pytest.mark.parametrize("path, allowed", [
    ("/api/servershare/orders", False),
    ("/ws/agent", False),
    ("/api/admin/plugins", False),
    ("/api/auth/login", True),
    ("/api/modules", True),
    ("/api/theme/brand", True),
    ("/api/admin/cluster", True),
    ("/api/admin/cluster/host-1/commands", True),
    ("/api/health", True),
    ("/selfcare", True),
    ("/keepup-static/js/main_new.js", True),
])
def test_what_a_stopped_replica_still_answers(path, allowed):
    assert cluster.allowed_while_stopped(path) is allowed


def refusal(action, target, members, open_command=None, force=False):
    with pytest.raises(cluster.CommandRefused) as caught:
        cluster.check_command(action, target, members, open_command, force, NOW)
    return caught.value


def test_an_unknown_action_is_refused():
    assert refusal("reboot", member("a"), [member("a")]).status_code == 400


def test_an_unknown_replica_is_refused():
    assert refusal(cluster.ACTION_STOP, None, []).status_code == 404


def test_a_gone_replica_gets_no_commands():
    gone = member("a", seen=NOW - timedelta(minutes=5))
    assert refusal(cluster.ACTION_START, gone, [gone]).status_code == 409


def test_one_unfinished_command_at_a_time():
    a, b = member("a"), member("b")
    open_command = {"action": "stop", "status": "pending"}
    assert refusal(cluster.ACTION_START, a, [a, b], open_command=open_command).status_code == 409


def test_restart_is_refused_with_the_replica_s_own_reason():
    a = member("a", can_restart=False, blocker="run by uvicorn reload")
    refused = refusal(cluster.ACTION_RESTART, a, [a, member("b")])
    assert refused.status_code == 409 and refused.reason == "run by uvicorn reload"


def test_the_last_serving_replica_is_not_stopped_without_force():
    a = member("a")
    others = [member("b", state=cluster.STATE_STOPPED),
              member("c", seen=NOW - timedelta(minutes=5))]
    assert refusal(cluster.ACTION_STOP, a, [a] + others).status_code == 409
    cluster.check_command(cluster.ACTION_STOP, a, [a] + others, None, True, NOW)


def test_one_of_two_serving_replicas_is_stopped_freely():
    a, b = member("a"), member("b")
    cluster.check_command(cluster.ACTION_STOP, a, [a, b], None, False, NOW)


# ================================ repository ===============================

def test_a_replica_keeps_one_row_however_often_it_publishes(fresh_database):
    replica = controller("host-1")
    run(replica.tick())
    run(replica.tick())

    members = cluster.read_members()
    assert [m["instance_id"] for m in members] == ["host-1"]
    assert members[0]["plugins_running"] == ["servershare"]
    assert members[0]["build"] == "0.0.1 #17"
    assert members[0]["can_restart"] is True


def test_a_command_is_claimed_once(fresh_database):
    command_id = cluster.record_command("host-1", "stop", False, 1, "admin", NOW)
    assert cluster.claim_command(command_id, NOW) is True
    assert cluster.claim_command(command_id, NOW) is False


def test_commands_that_never_finish_expire(fresh_database):
    late = NOW - cluster.COMMAND_TTL - timedelta(seconds=1)
    never_picked = cluster.record_command("host-1", "stop", False, 1, "admin", late)
    never_back = cluster.record_command("host-2", "restart", False, 1, "admin", late)
    cluster.claim_command(never_back, late)
    fresh = cluster.record_command("host-3", "stop", False, 1, "admin", NOW)

    assert cluster.expire_commands(NOW) == 2
    statuses = {c["id"]: c["status"] for c in cluster.recent_commands()}
    assert statuses == {never_picked: "expired", never_back: "expired", fresh: "pending"}


def test_rows_older_than_a_day_are_deleted_by_any_replica(fresh_database):
    old = controller("host-old", clock=Clock(NOW - cluster.MEMBER_RETENTION - timedelta(minutes=1)))
    run(old.tick())
    run(controller("host-1").tick())

    assert [m["instance_id"] for m in cluster.read_members()] == ["host-1"]


# ================================ controller ===============================

def test_a_command_given_through_one_replica_is_carried_out_by_another(fresh_database):
    """Two replicas, one database: the command travels through the table."""
    scheduler_b = FakeScheduler()
    a = controller("host-a")
    b = controller("host-b", scheduler=scheduler_b)
    run(a.tick())
    run(b.tick())
    cluster.set_controller(a)

    run(cluster.give_command("host-b", {"action": "stop"}, ADMIN, now=NOW))
    run(b.tick())

    assert b.state == cluster.STATE_STOPPED
    assert scheduler_b.calls == ["pause"]
    assert cluster.read_member("host-b")["state"] == cluster.STATE_STOPPED
    command = cluster.recent_commands()[0]
    assert (command["status"], command["requested_by_name"]) == ("done", "admin")


def test_start_puts_a_stopped_replica_back(fresh_database):
    scheduler = FakeScheduler()
    replica = controller("host-1", scheduler=scheduler)
    run(replica.tick())
    replica.stop()
    run(controller("host-2").tick())

    cluster.record_command("host-1", "start", False, 1, "admin", NOW)
    run(replica.tick())

    assert replica.state == cluster.STATE_SERVING
    assert scheduler.calls == ["pause", "resume"]


def test_a_stopped_replica_stays_stopped_across_a_restart(fresh_database):
    first = controller("host-1", scheduler=FakeScheduler())
    run(first.tick())
    first.stop()

    scheduler = FakeScheduler()
    again = controller("host-1", scheduler=scheduler)
    again.restore()

    assert again.state == cluster.STATE_STOPPED
    assert scheduler.calls == ["pause"]


def test_restart_shuts_down_the_normal_way_and_execs_the_same_command(fresh_database, monkeypatch):
    kills, execs, registered = [], [], []
    monkeypatch.setattr(cluster.atexit, "register", registered.append)
    monkeypatch.setattr(sys, "orig_argv", ["python", "-m", "uvicorn", "app.main:app"])
    replica = controller("host-1", kill=lambda pid, sig: kills.append(sig),
                         execv=lambda path, argv: execs.append((path, argv)))
    run(replica.tick())
    run(controller("host-2").tick())

    cluster.record_command("host-1", "restart", False, 1, "admin", NOW)
    run(replica.tick())

    assert kills == [signal.SIGTERM]
    assert registered == [replica.exec_again]
    assert cluster.recent_commands()[0]["status"] == "executing"

    replica.exec_again()
    assert execs == [(sys.executable, ["python", "-m", "uvicorn", "app.main:app"])]


def test_the_process_that_comes_back_closes_its_restart(fresh_database):
    command_id = cluster.record_command("host-1", "restart", False, 1, "admin", NOW)
    cluster.claim_command(command_id, NOW)

    controller("host-1", clock=Clock(NOW + timedelta(seconds=20))).restore()

    command = cluster.recent_commands()[0]
    assert command["status"] == "done" and "Restarted" in command["result"]


def test_a_replica_that_cannot_restart_says_why_and_fails_the_command(fresh_database):
    replica = controller("host-1", blocker="run by uvicorn reload")
    run(replica.tick())
    assert cluster.read_member("host-1")["restart_blocker"] == "run by uvicorn reload"

    command_id = cluster.record_command("host-1", "restart", False, 1, "admin", NOW)
    run(replica.tick())

    command = cluster.recent_commands()[0]
    assert (command["id"], command["status"]) == (command_id, "failed")


def test_plugins_awaiting_a_restart_are_published_per_replica(fresh_database):
    run(controller("host-1", running=("servershare", "telegrambot"), pending=("telegrambot",)).tick())

    published = cluster.read_member("host-1")
    assert published["plugins_pending"] == ["telegrambot"]


# ============================== admin routes ===============================

def test_the_route_refuses_what_the_rules_refuse(fresh_database):
    run(controller("host-1").tick())

    with pytest.raises(HTTPException) as caught:
        run(cluster.give_command("host-1", {"action": "stop"}, ADMIN, now=NOW))
    assert caught.value.status_code == 409

    with pytest.raises(HTTPException) as caught:
        run(cluster.give_command("nobody", {"action": "stop"}, ADMIN, now=NOW))
    assert caught.value.status_code == 404

    run(cluster.give_command("host-1", {"action": "stop", "force": True}, ADMIN, now=NOW))
    with pytest.raises(HTTPException) as caught:
        run(cluster.give_command("host-1", {"action": "start"}, ADMIN, now=NOW))
    assert caught.value.status_code == 409


def test_the_overview_marks_gone_replicas_and_their_last_command(fresh_database):
    run(controller("host-old", clock=Clock(NOW - timedelta(minutes=10))).tick())
    run(controller("host-1").tick())
    cluster.record_command("host-1", "stop", True, 1, "admin", NOW)

    overview = run(cluster.cluster_overview(now=NOW))

    views = {m["instance_id"]: m for m in overview["members"]}
    assert views["host-old"]["display_state"] == cluster.STATE_GONE
    assert views["host-1"]["display_state"] == cluster.STATE_SERVING
    assert views["host-1"]["last_command"]["action"] == "stop"


def test_the_cluster_routes_are_for_administrators_only():
    app = FastAPI()
    cluster.register_cluster_routes(app)
    app.dependency_overrides[get_current_user] = lambda: {"id": 2, "username": "c", "role": "CLIENT"}
    client = TestClient(app)

    assert client.get("/api/admin/cluster").status_code == 403
    assert client.post("/api/admin/cluster/host-1/commands", json={"action": "stop"}).status_code == 403


# ============================ gate and health ==============================

def gated_app():
    app = FastAPI()

    @app.get("/api/orders")
    async def orders():
        return {"ok": True}

    @app.get("/api/auth/me")
    async def me():
        return {"ok": True}

    @app.get("/selfcare")
    async def page():
        return {"ok": True}

    @app.websocket("/ws/agent")
    async def agent(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    app.add_middleware(cluster.StoppedReplicaGate)
    app.add_middleware(ApiVersionMiddleware)
    return app


def stopped_controller():
    replica = controller("host-1")
    replica.state = cluster.STATE_STOPPED
    cluster.set_controller(replica)


def test_a_serving_replica_passes_everything():
    client = TestClient(gated_app())
    assert client.get("/api/orders").status_code == 200
    with client.websocket_connect("/ws/agent") as ws:
        assert ws.receive_text() == "hello"


def test_a_stopped_replica_answers_503_to_the_api_even_under_a_version_prefix():
    stopped_controller()
    client = TestClient(gated_app())

    response = client.get("/api/orders")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == str(cluster.HEARTBEAT_SECONDS)
    assert client.get("/api/v1/orders").status_code == 503


def test_a_stopped_replica_still_lets_the_panel_in():
    stopped_controller()
    client = TestClient(gated_app())
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/selfcare").status_code == 200


def test_a_stopped_replica_refuses_websockets():
    stopped_controller()
    client = TestClient(gated_app())
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/ws/agent"):
            pass
    assert caught.value.code == cluster.WS_CLOSE_TRY_AGAIN


def test_health_of_a_stopped_replica_is_503_stopped():
    from keepup.web import register_web_routes
    app = FastAPI()
    register_web_routes(app)
    stopped_controller()

    response = TestClient(app).get("/api/health")

    assert response.status_code == 503
    assert response.json()["status"] == "stopped"


def test_the_framework_installs_the_gate_inside_the_version_middleware():
    from keepup.factory import create_app
    from keepup.settings import KeepupSettings

    app = create_app(KeepupSettings(title="t", static_mounts=(), plugin_manager=None))
    stack = [m.cls for m in app.user_middleware]
    assert stack.index(ApiVersionMiddleware) < stack.index(cluster.StoppedReplicaGate)
    paths = {route.path for route in app.routes}
    assert {"/api/admin/cluster", "/api/admin/cluster/{instance_id}/commands"} <= paths
