"""The replicas of one deployment, seen and steered from the panel.

The application runs as several replicas against one database, each with its
own set of plugins up. Until this module an administrator saw only the replica
that happened to answer, and a plugin switched from the panel took effect
"after a restart" that the panel could not perform.

Every replica now publishes itself in ``cluster_members`` and collects the
commands addressed to it from ``cluster_commands``. Delivery goes through the
table rather than over HTTP between replicas: replicas do not know each
other's addresses, and behind a load balancer a request cannot be aimed at
one of them anyway.

Three commands. ``stop`` keeps the process alive and still publishing, but
answers 503 to the API and refuses WebSockets, and pauses the scheduler so
that background jobs and leader elections move to the other replicas.
``start`` undoes it. ``restart`` shuts the server down the normal way --
lifespan, plugin cleanup, buffers flushed -- and then execs the same command
line in the same process, so no supervisor is needed.

See doc/cluster_control.md.
"""

import asyncio
import atexit
import json
import logging
import multiprocessing
import os
import signal
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse

from keepup.auth.dependencies import get_current_admin
from keepup.db import DatabaseManagerV2
from keepup.instance import get_instance_id, get_instance_name

logger = logging.getLogger(__name__)

# ================================ constants ================================

#: How often a replica publishes itself and looks for its commands.
HEARTBEAT_SECONDS = 10
#: A replica silent for this many heartbeats is shown as gone.
MISSED_HEARTBEATS = 3
#: A gone replica stays listed this long: one that vanished without a trace is
#: exactly what an administrator needs to see. Older rows are deleted.
MEMBER_RETENTION = timedelta(days=1)
#: A command nobody picked up, or a restart that never came back, is closed
#: after this long -- otherwise it would block every later command.
COMMAND_TTL = timedelta(minutes=5)
#: How many past commands the panel is shown.
RECENT_COMMANDS = 30

STATE_SERVING = "serving"
STATE_STOPPED = "stopped"
#: Never stored: how a member reads when its heartbeat is too old.
STATE_GONE = "gone"

ACTION_STOP = "stop"
ACTION_START = "start"
ACTION_RESTART = "restart"
ACTIONS = (ACTION_STOP, ACTION_START, ACTION_RESTART)

STATUS_PENDING = "pending"
STATUS_EXECUTING = "executing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
OPEN_STATUSES = (STATUS_PENDING, STATUS_EXECUTING)

#: A stopped replica refuses only these: what serves the product.
GATED_PREFIXES = ("/api/", "/ws/")
#: ...except what the panel needs to sign in and to send ``start``. A stopped
#: last replica must still be startable from the same panel -- otherwise
#: stopping it with ``force`` would need the infrastructure after all.
ALLOWED_WHILE_STOPPED = (
    "/api/auth/",
    "/api/theme/",
    "/api/modules",
    "/api/agreements",
    "/api/public/config",
    "/api/version",
    "/api/admin/cluster",
    # Answers 503 "stopped" by itself, so that a load balancer takes the
    # replica out; see keepup/web.py.
    "/api/health",
)

STOPPED_DETAIL = "This replica is stopped by an administrator; another replica serves the request."
#: WebSocket close code "try again later": the client reconnects, and the
#: balancer sends it to a replica that serves.
WS_CLOSE_TRY_AGAIN = 1013

# ================================== rules ==================================

def utcnow() -> datetime:
    return datetime.utcnow()


def _as_datetime(value: Any) -> Optional[datetime]:
    """SQLite hands timestamps back as text, PostgreSQL as datetimes."""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def is_alive(last_seen: Any, now: datetime) -> bool:
    last_seen = _as_datetime(last_seen)
    if last_seen is None:
        return False
    return now - last_seen <= timedelta(seconds=HEARTBEAT_SECONDS * MISSED_HEARTBEATS)


def restart_blocker(parent_process: Callable[[], Any] = multiprocessing.parent_process,
                    orig_argv: Optional[List[str]] = None) -> Optional[str]:
    """Why this process cannot restart itself, or None when it can.

    Under ``reload`` or with several workers the server process is spawned by
    a supervising parent: its command line is multiprocessing's own, and
    re-running it would start an orphan worker rather than the server. The
    parent owns the process, so it is the parent that has to be restarted.
    """
    if parent_process() is not None:
        return ("The server process is run by a supervising parent (uvicorn reload "
                "or several workers); restart that parent instead.")
    argv = sys.orig_argv if orig_argv is None else orig_argv
    if not argv:
        return "The command line this process was started with is unknown."
    return None


def allowed_while_stopped(path: str) -> bool:
    """Whether a stopped replica still answers this path."""
    if not path.startswith(GATED_PREFIXES):
        return True
    return path.startswith(ALLOWED_WHILE_STOPPED)


class CommandRefused(Exception):
    """A command the administrator cannot give, with the HTTP status to answer."""

    def __init__(self, status_code: int, reason: str):
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason


def check_command(action: str, target: Optional[Dict[str, Any]],
                  members: List[Dict[str, Any]], open_command: Optional[Dict[str, Any]],
                  force: bool, now: datetime) -> None:
    """Refuse a command that cannot or must not be carried out.

    Pure: the registry comes in, a refusal goes out. Each branch is a case an
    administrator can actually run into from the panel.
    """
    if action not in ACTIONS:
        raise CommandRefused(400, f"Unknown action {action!r}; expected one of {', '.join(ACTIONS)}.")
    if target is None:
        raise CommandRefused(404, "No such replica in the cluster.")
    if not is_alive(target.get("last_seen"), now):
        raise CommandRefused(
            409, f"The replica has not answered since {target.get('last_seen')}; "
                 f"a command would never be picked up.")
    if open_command is not None:
        raise CommandRefused(
            409, f"The replica has an unfinished command ({open_command.get('action')}, "
                 f"{open_command.get('status')}); wait for it.")
    if action == ACTION_RESTART and not target.get("can_restart"):
        raise CommandRefused(
            409, target.get("restart_blocker") or "This replica cannot restart itself.")
    if action == ACTION_STOP and target.get("state") == STATE_SERVING and not force:
        others = [m for m in members
                  if m.get("instance_id") != target.get("instance_id")
                  and m.get("state") == STATE_SERVING
                  and is_alive(m.get("last_seen"), now)]
        if not others:
            raise CommandRefused(
                409, "This is the last replica serving the cluster: stopping it takes "
                     "the whole product down. Repeat with force to do it anyway.")

# ================================ repository ===============================

_MEMBER_COLUMNS = ("instance_id", "instance_name", "host", "pid", "build", "started_at",
                   "last_seen", "state", "can_restart", "restart_blocker",
                   "plugins_running", "plugins_pending")


def publish_member(member: Dict[str, Any]) -> None:
    """Write this replica's row. Only the replica itself writes it, so an
    update-then-insert has no one to race with."""
    values = {column: member.get(column) for column in _MEMBER_COLUMNS}
    for column in ("plugins_running", "plugins_pending"):
        values[column] = json.dumps(values[column] or [])
    assignments = ", ".join(f"{c} = :{c}" for c in _MEMBER_COLUMNS if c != "instance_id")
    updated = DatabaseManagerV2.execute_commit(
        f"UPDATE cluster_members SET {assignments} WHERE instance_id = :instance_id", values)
    if not updated:
        columns = ", ".join(_MEMBER_COLUMNS)
        placeholders = ", ".join(f":{c}" for c in _MEMBER_COLUMNS)
        DatabaseManagerV2.execute_commit(
            f"INSERT INTO cluster_members ({columns}) VALUES ({placeholders})", values)


def _member_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    member = dict(row)
    for column in ("plugins_running", "plugins_pending"):
        try:
            member[column] = json.loads(member.get(column) or "[]")
        except (TypeError, ValueError):
            member[column] = []
    member["can_restart"] = bool(member.get("can_restart"))
    return member


def read_members() -> List[Dict[str, Any]]:
    rows = DatabaseManagerV2.execute(
        "SELECT * FROM cluster_members ORDER BY instance_name, instance_id", {})
    return [_member_from_row(row) for row in rows]


def read_member(instance_id: str) -> Optional[Dict[str, Any]]:
    row = DatabaseManagerV2.execute_one(
        "SELECT * FROM cluster_members WHERE instance_id = :id", {"id": instance_id})
    return _member_from_row(row) if row else None


def write_state(instance_id: str, state: str) -> None:
    DatabaseManagerV2.execute_commit(
        "UPDATE cluster_members SET state = :state WHERE instance_id = :id",
        {"state": state, "id": instance_id})


def record_command(instance_id: str, action: str, forced: bool, requested_by: Optional[int],
                   requested_by_name: Optional[str], now: datetime) -> Optional[int]:
    row = DatabaseManagerV2.execute_commit_returning(
        "INSERT INTO cluster_commands (instance_id, action, forced, status, requested_by, "
        "requested_by_name, requested_at) VALUES (:instance_id, :action, :forced, :status, "
        ":requested_by, :requested_by_name, :now)",
        {"instance_id": instance_id, "action": action, "forced": bool(forced),
         "status": STATUS_PENDING, "requested_by": requested_by,
         "requested_by_name": requested_by_name, "now": now})
    return row.get("id") if row else None


def open_command_for(instance_id: str) -> Optional[Dict[str, Any]]:
    return DatabaseManagerV2.execute_one(
        "SELECT * FROM cluster_commands WHERE instance_id = :id "
        "AND status IN (:pending, :executing) ORDER BY id LIMIT 1",
        {"id": instance_id, "pending": STATUS_PENDING, "executing": STATUS_EXECUTING})


def pending_commands_for(instance_id: str) -> List[Dict[str, Any]]:
    return DatabaseManagerV2.execute(
        "SELECT * FROM cluster_commands WHERE instance_id = :id AND status = :pending ORDER BY id",
        {"id": instance_id, "pending": STATUS_PENDING})


def claim_command(command_id: int, now: datetime) -> bool:
    """Take a command for execution; False when somebody already has.

    A conditional UPDATE rather than SELECT ... FOR UPDATE SKIP LOCKED, which
    is PostgreSQL-only -- and SQLite is the development mode.
    """
    claimed = DatabaseManagerV2.execute_commit(
        "UPDATE cluster_commands SET status = :executing, claimed_at = :now "
        "WHERE id = :id AND status = :pending",
        {"executing": STATUS_EXECUTING, "now": now, "id": command_id, "pending": STATUS_PENDING})
    return claimed == 1


def finish_command(command_id: int, status: str, result: str, now: datetime) -> None:
    DatabaseManagerV2.execute_commit(
        "UPDATE cluster_commands SET status = :status, result = :result, finished_at = :now "
        "WHERE id = :id",
        {"status": status, "result": result, "now": now, "id": command_id})


def executing_restarts_for(instance_id: str) -> List[Dict[str, Any]]:
    return DatabaseManagerV2.execute(
        "SELECT * FROM cluster_commands WHERE instance_id = :id AND action = :restart "
        "AND status = :executing ORDER BY id",
        {"id": instance_id, "restart": ACTION_RESTART, "executing": STATUS_EXECUTING})


def expire_commands(now: datetime) -> int:
    """Close commands that will never finish, so they stop blocking new ones."""
    cutoff = now - COMMAND_TTL
    expired = DatabaseManagerV2.execute_commit(
        "UPDATE cluster_commands SET status = :expired, finished_at = :now, "
        "result = :not_picked WHERE status = :pending AND requested_at < :cutoff",
        {"expired": STATUS_EXPIRED, "now": now, "cutoff": cutoff, "pending": STATUS_PENDING,
         "not_picked": "The replica did not pick the command up in time."})
    expired += DatabaseManagerV2.execute_commit(
        "UPDATE cluster_commands SET status = :expired, finished_at = :now, "
        "result = :not_back WHERE status = :executing AND claimed_at < :cutoff",
        {"expired": STATUS_EXPIRED, "now": now, "cutoff": cutoff, "executing": STATUS_EXECUTING,
         "not_back": "The replica did not report the command finished in time."})
    return expired


def prune_members(now: datetime) -> int:
    return DatabaseManagerV2.execute_commit(
        "DELETE FROM cluster_members WHERE last_seen < :cutoff",
        {"cutoff": now - MEMBER_RETENTION})


def recent_commands(limit: int = RECENT_COMMANDS) -> List[Dict[str, Any]]:
    return DatabaseManagerV2.execute(
        "SELECT * FROM cluster_commands ORDER BY id DESC LIMIT :limit", {"limit": int(limit)})

# ================================ controller ===============================

@dataclass
class PluginPicture:
    """What runs in this replica, and what its next start would change."""
    running: List[str]
    pending: List[str]


async def _no_plugins() -> PluginPicture:
    return PluginPicture([], [])


def _default_scheduler():
    from keepup.scheduler import get_scheduler
    return get_scheduler()


class ReplicaController:
    """This replica's side of the cluster: its state, heartbeat and commands.

    The heartbeat runs as its own asyncio task and not as a scheduler job: a
    stopped replica pauses the scheduler, and a heartbeat inside it would go
    silent and never hear ``start``.
    """

    def __init__(self, plugins: Callable[[], Awaitable[PluginPicture]] = _no_plugins,
                 build: Optional[str] = None,
                 scheduler: Callable[[], Any] = _default_scheduler,
                 clock: Callable[[], datetime] = utcnow,
                 instance_id: Optional[str] = None,
                 blocker: Optional[str] = None,
                 kill: Callable[[int, int], None] = os.kill,
                 execv: Callable[[str, List[str]], None] = os.execv):
        self.instance_id = instance_id or get_instance_id()
        self.instance_name = get_instance_name()
        self.state = STATE_SERVING
        self.started_at = clock()
        self.build = build
        self.blocker = restart_blocker() if blocker is None else (blocker or None)
        self._plugins = plugins
        self._scheduler = scheduler
        self._clock = clock
        self._kill = kill
        self._execv = execv
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self.restart_requested = False

    # ------------------------------------------------------------ lifecycle

    def restore(self) -> None:
        """Pick up where this replica was left: a stopped one stays stopped.

        Otherwise a restart -- ours, the container's, the host's -- would
        quietly put back into service a replica an administrator took out.
        """
        try:
            previous = read_member(self.instance_id)
        except Exception as error:
            logger.warning(f"Cluster: could not read this replica's previous state: {error}")
            return
        if previous and previous.get("state") == STATE_STOPPED:
            logger.warning("Cluster: this replica was stopped by an administrator and stays stopped")
            self.stop()
        self._close_restarts()

    def _close_restarts(self) -> None:
        """A restart command is finished by the process that comes back from it."""
        now = self._clock()
        for command in executing_restarts_for(self.instance_id):
            finish_command(command["id"], STATUS_DONE,
                           f"Restarted; the replica is back since {self.started_at.isoformat()}Z.",
                           now)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # The database may be briefly away; the next heartbeat retries.
                logger.warning(f"Cluster heartbeat failed: {error}")
            await asyncio.sleep(HEARTBEAT_SECONDS)

    def launch(self) -> None:
        try:
            self.restore()
        except Exception as error:
            logger.warning(f"Cluster: could not restore this replica's state: {error}")
        self._task = asyncio.create_task(self.run_forever())

    async def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def nudge(self) -> None:
        """Handle a command addressed to this very replica now, not at the next beat."""
        asyncio.create_task(self._tick_quietly())

    async def _tick_quietly(self) -> None:
        try:
            await self.tick()
        except Exception as error:
            logger.warning(f"Cluster: immediate command pass failed: {error}")

    # ------------------------------------------------------------ one pass

    async def tick(self) -> None:
        async with self._lock:
            picture = await self._plugins()
            now = self._clock()
            publish_member(self.as_member(picture, now))
            expire_commands(now)
            for command in pending_commands_for(self.instance_id):
                if claim_command(command["id"], now):
                    self.execute(command)
            prune_members(now)

    def as_member(self, picture: PluginPicture, now: datetime) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "instance_name": self.instance_name,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "build": self.build,
            "started_at": self.started_at,
            "last_seen": now,
            "state": self.state,
            "can_restart": self.blocker is None,
            "restart_blocker": self.blocker,
            "plugins_running": picture.running,
            "plugins_pending": picture.pending,
        }

    def execute(self, command: Dict[str, Any]) -> None:
        action = command.get("action")
        now = self._clock()
        if action == ACTION_STOP:
            if self.state == STATE_STOPPED:
                finish_command(command["id"], STATUS_DONE, "Already stopped.", now)
                return
            self.stop()
            finish_command(command["id"], STATUS_DONE, "Stopped: the API answers 503, the scheduler is paused.", now)
        elif action == ACTION_START:
            if self.state == STATE_SERVING:
                finish_command(command["id"], STATUS_DONE, "Already serving.", now)
                return
            self.start()
            finish_command(command["id"], STATUS_DONE, "Serving again.", now)
        elif action == ACTION_RESTART:
            if self.blocker:
                finish_command(command["id"], STATUS_FAILED, self.blocker, now)
                return
            # Left executing: the process that comes back closes it.
            self.request_restart()
        else:
            finish_command(command["id"], STATUS_FAILED, f"Unknown action {action!r}.", now)

    # ------------------------------------------------------------ actions

    def stop(self) -> None:
        self.state = STATE_STOPPED
        write_state(self.instance_id, STATE_STOPPED)
        scheduler = self._scheduler()
        if scheduler is not None and getattr(scheduler, "running", False):
            scheduler.pause()
        logger.warning("Cluster: this replica is stopped -- the API answers 503, the scheduler is paused")

    def start(self) -> None:
        self.state = STATE_SERVING
        write_state(self.instance_id, STATE_SERVING)
        scheduler = self._scheduler()
        if scheduler is not None and getattr(scheduler, "running", False):
            scheduler.resume()
        logger.warning("Cluster: this replica serves again")

    def request_restart(self) -> None:
        """Shut down the normal way, then run the same command line again.

        SIGTERM makes uvicorn go through the application's shutdown; the exec
        happens once the interpreter is on its way out, from atexit, so that
        nothing of the shutdown is skipped.
        """
        if self.restart_requested:
            return
        self.restart_requested = True
        atexit.register(self.exec_again)
        logger.warning("Cluster: restarting this replica on an administrator's command")
        self._kill(os.getpid(), signal.SIGTERM)

    def exec_again(self) -> None:
        argv = list(sys.orig_argv)
        try:
            self._execv(sys.executable, argv)
        except OSError as error:
            # The process simply ends; a supervisor, if any, takes it from here.
            logger.error(f"Cluster: could not exec {argv!r} again: {error}")


#: The controller of this process, once the application has started one.
_controller: Optional[ReplicaController] = None


def set_controller(controller: Optional[ReplicaController]) -> None:
    global _controller
    _controller = controller


def get_controller() -> Optional[ReplicaController]:
    return _controller


def current_state() -> str:
    return _controller.state if _controller is not None else STATE_SERVING

# ============================ gate and routes ==============================

class StoppedReplicaGate:
    """Pure ASGI middleware: what a stopped replica refuses.

    A gate rather than removing routes, which FastAPI cannot do on a running
    application -- and ``start`` has to put everything back. Installed inside
    the API version middleware, so it sees the registered path without /v1.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope.get("type") in ("http", "websocket")
                and current_state() == STATE_STOPPED
                and not allowed_while_stopped(scope.get("path", ""))):
            if scope["type"] == "http":
                response = JSONResponse({"detail": STOPPED_DETAIL}, status_code=503,
                                        headers={"Retry-After": str(HEARTBEAT_SECONDS)})
                await response(scope, receive, send)
            else:
                await send({"type": "websocket.close", "code": WS_CLOSE_TRY_AGAIN})
            return
        await self.app(scope, receive, send)


def _member_view(member: Dict[str, Any], now: datetime,
                 last_command: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    alive = is_alive(member.get("last_seen"), now)
    view = dict(member)
    view["alive"] = alive
    view["display_state"] = member.get("state") if alive else STATE_GONE
    view["last_command"] = last_command
    return view


async def cluster_overview(now: Optional[datetime] = None) -> Dict[str, Any]:
    """The registry as the panel shows it."""
    now = now or utcnow()
    members = read_members()
    commands = recent_commands()
    latest: Dict[str, Dict[str, Any]] = {}
    for command in commands:
        latest.setdefault(command["instance_id"], command)
    controller = get_controller()
    return {
        "answered_by": controller.instance_id if controller else get_instance_id(),
        "heartbeat_seconds": HEARTBEAT_SECONDS,
        "members": [_member_view(m, now, latest.get(m["instance_id"])) for m in members],
        "commands": commands,
    }


async def give_command(instance_id: str, request: Dict[str, Any], admin: Dict[str, Any],
                       now: Optional[datetime] = None) -> Dict[str, Any]:
    """Address a command to one replica; it collects the command itself."""
    now = now or utcnow()
    request = request or {}
    action = request.get("action")
    force = request.get("force") is True
    members = read_members()
    target = next((m for m in members if m["instance_id"] == instance_id), None)
    try:
        check_command(action, target, members,
                      open_command_for(instance_id) if target else None, force, now)
    except CommandRefused as refusal:
        raise HTTPException(status_code=refusal.status_code, detail=refusal.reason)

    command_id = record_command(instance_id, action, force, admin.get("id"),
                                admin.get("username"), now)
    logger.warning(f"Cluster: {admin.get('username')} ordered {action} of {instance_id}"
                   f"{' (forced)' if force else ''}")
    controller = get_controller()
    if controller is not None and controller.instance_id == instance_id:
        controller.nudge()
    return {"success": True, "command": {"id": command_id, "instance_id": instance_id,
                                         "action": action, "forced": force,
                                         "status": STATUS_PENDING}}


def register_cluster_routes(app) -> None:
    @app.get("/api/admin/cluster")
    async def cluster_overview_endpoint(admin: dict = Depends(get_current_admin)):
        """The replicas of this deployment, their plugins and commands (administrators only)."""
        return await cluster_overview()

    @app.post("/api/admin/cluster/{instance_id}/commands")
    async def give_command_endpoint(instance_id: str, request: Dict[str, Any],
                                    admin: dict = Depends(get_current_admin)):
        """Stop, start or restart one replica (administrators only)."""
        return await give_command(instance_id, request, admin)
