"""A made-up automated system with an identity subsystem of its own (keepup-91).

It stands for the larger system an application on keepup gets installed into.
Deliberately unlike anything keepup speaks: opaque session strings rather than
JWTs, its own header for the calling service's key, its own shapes of answers,
"groups" and "grants" rather than roles and permissions, and verdicts rather
than booleans. A provider plugin has to translate all of it -- which is what a
test through this system actually checks.

The ``/control`` routes are the test's hands on the system: revoke a session,
change somebody's groups, take the whole system down for maintenance, read how
often it was asked. A real system has its own ways of doing each.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

SESSION_PREFIX = "hg_"
KEY_HEADER = "X-AS-Key"
SESSION_HEADER = "X-AS-Session"


@dataclass
class Person:
    uid: str
    login: str
    password: str
    display_name: str
    mail: str
    groups: List[str] = field(default_factory=list)
    #: (action, object id or "*") pairs this person is granted.
    grants: List[tuple] = field(default_factory=list)


@dataclass
class State:
    service_key: str
    people: Dict[str, Person] = field(default_factory=dict)
    sessions: Dict[str, dict] = field(default_factory=dict)
    down: bool = False
    introspections: int = 0
    decisions: int = 0
    session_minutes: int = 30

    def principal(self, person: Person) -> dict:
        return {"uid": person.uid, "login": person.login, "displayName": person.display_name,
                "mail": person.mail, "groups": list(person.groups),
                "grants": [f"{action}@{target}" for action, target in person.grants]}


def build_service(service_key: str) -> FastAPI:
    """The system, with two people in it and no sessions yet."""
    state = State(service_key=service_key)
    for person in (
        Person("u-100", "anna", "anna-as-pass", "Anna Smirnova", "anna@as.example",
               groups=["AS_OPERATORS"], grants=[("things.read", "*")]),
        Person("u-200", "boris", "boris-as-pass", "Boris Orlov", "boris@as.example",
               groups=["AS_ADMINISTRATORS", "AS_OPERATORS"], grants=[("things.read", "7")]),
    ):
        state.people[person.login] = person

    app = FastAPI(title="Homegrown AS")
    app.state.as_state = state

    @app.middleware("http")
    async def maintenance(request: Request, call_next):
        if state.down and request.url.path.startswith("/api/"):
            return JSONResponse({"error": "maintenance"}, status_code=502)
        return await call_next(request)

    def check_key(key: Optional[str]):
        if key != state.service_key:
            raise HTTPException(status_code=403, detail={"error": "unknown_service"})

    def live_session(token: Optional[str]) -> Optional[dict]:
        session = state.sessions.get(token or "")
        if session is None or session["valid_until"] <= datetime.now(timezone.utc):
            return None
        return session

    @app.post("/api/sessions")
    async def open_session(body: dict, x_as_key: Optional[str] = Header(None)):
        check_key(x_as_key)
        person = state.people.get(body.get("login", ""))
        if person is None or not secrets.compare_digest(person.password, body.get("secret", "")):
            return JSONResponse({"error": "bad_credentials"}, status_code=401)
        token = SESSION_PREFIX + secrets.token_urlsafe(24)
        valid_until = datetime.now(timezone.utc) + timedelta(minutes=state.session_minutes)
        state.sessions[token] = {"login": person.login, "valid_until": valid_until}
        return {"session": token, "validUntil": valid_until.isoformat(),
                "principal": state.principal(person)}

    @app.get("/api/sessions/current")
    async def introspect(x_as_key: Optional[str] = Header(None),
                         x_as_session: Optional[str] = Header(None)):
        check_key(x_as_key)
        state.introspections += 1
        session = live_session(x_as_session)
        if session is None:
            return JSONResponse({"error": "no_session"}, status_code=404)
        person = state.people[session["login"]]
        return {"principal": state.principal(person),
                "validUntil": session["valid_until"].isoformat()}

    @app.post("/api/decisions")
    async def decide(body: dict, x_as_key: Optional[str] = Header(None)):
        check_key(x_as_key)
        state.decisions += 1
        person = next((p for p in state.people.values() if p.uid == body.get("uid")), None)
        if person is None:
            return {"verdict": "deny", "reason": "unknown_principal"}
        target = str(body.get("object") or "*")
        permitted = any(action == body.get("action") and granted in ("*", target)
                        for action, granted in person.grants)
        return {"verdict": "permit" if permitted else "deny"}

    # --- the test's hands ------------------------------------------------------

    @app.delete("/control/sessions/{token}")
    async def revoke(token: str):
        return {"revoked": state.sessions.pop(token, None) is not None}

    @app.put("/control/people/{login}/groups")
    async def regroup(login: str, groups: List[str]):
        state.people[login].groups = list(groups)
        return {"groups": groups}

    @app.put("/control/down")
    async def take_down(body: dict):
        state.down = bool(body.get("down"))
        return {"down": state.down}

    @app.get("/control/stats")
    async def stats():
        return {"introspections": state.introspections, "decisions": state.decisions}

    return app
