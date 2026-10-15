"""The applications behind the gateway, as the administrator sees them.

A route marked ``application`` stands for a keepup application of its own -- a
headless subsystem, a microservice built on the framework. The administrator
wants to see it the way the panel shows its own runtime: which plugins it runs,
which of them failed and why, whether it is up.

The subsystem may have no panel, no `keepup-auth` and no `keepup-modules`, so the
panel's own `/api/admin/plugins` -- guarded by the panel's sign-in -- is not
something to count on. What it has instead is `keepup-gateway`, installed with
the same `identity_secret`: it answers `GET /api/gateway/self` to the gateway's
signed identity of an administrator, with its runtime's own report. The gateway
asks every address of such a route for that, for `/api/health` (public in every
keepup application) and for `/metrics`, at once and with a short timeout -- a
card for a dead address says so instead of holding the page.
"""

import asyncio
from typing import Any, Dict, List, Optional

import httpx

from keepup_gateway.identity import Caller, sign_identity
from keepup_gateway.headers import request_id_of

__all__ = [
    "SELF_PATH",
    "HEALTH_PATH",
    "METRICS_PATH",
    "PROBE_TIMEOUT_SECONDS",
    "ADMIN_ROLE",
    "describe_self",
    "Prober",
]

SELF_PATH = "/api/gateway/self"
HEALTH_PATH = "/api/health"
METRICS_PATH = "/metrics"
#: A card is a look, not a request somebody waits on: an address that does not
#: answer in this long is shown as not answering.
PROBE_TIMEOUT_SECONDS = 3.0
#: The role whose signed identity may read a runtime's report.
ADMIN_ROLE = "ADMIN"
#: What of a plugin's row the card shows.
_PLUGIN_FIELDS = ("id", "name", "kind", "distribution", "enabled", "initialized",
                  "outcome", "reason", "degraded")


def describe_self(manager: Any, settings: Any = None) -> Dict[str, Any]:
    """What this application says about itself to the gateway.

    Args:
        manager: the runtime the plugin was loaded by (or a 0.3.0 manager).
        settings: the application's settings, when known.

    Returns:
        Its name, this replica, the framework's version and every plugin's state.
    """
    import keepup
    from keepup.instance import get_instance_id

    settings = settings if settings is not None else getattr(manager, "settings", None)
    return {
        "application": getattr(settings, "project_name", None) or getattr(settings, "title", None)
        or "keepup application",
        "title": getattr(settings, "title", None),
        "instance": get_instance_id(),
        "keepup": getattr(keepup, "__version__", None),
        "plugins": _plugins_of(manager),
    }


def _plugins_of(manager: Any) -> List[Dict[str, Any]]:
    """Every plugin's state, from the runtime's report or from a manager's plugins."""
    report = getattr(manager, "report", None)
    if callable(report):
        return [{field: row.get(field) for field in _PLUGIN_FIELDS} for row in report()]
    plugins = getattr(manager, "plugins", None) or {}
    return [{"id": plugin_id, "name": getattr(plugin, "name", plugin_id),
             "initialized": bool(getattr(plugin, "initialized", False)),
             "outcome": "running" if getattr(plugin, "initialized", False) else None}
            for plugin_id, plugin in dict(plugins).items()]


class Prober:
    """Asks the addresses of application routes what they are.

    Args:
        client: the gateway's HTTP client.
        secret: what the administrator's identity is signed with.
        timeout: seconds per question.
    """

    def __init__(self, client: httpx.AsyncClient, secret: str,
                 timeout: float = PROBE_TIMEOUT_SECONDS):
        self.client = client
        self.secret = secret
        self.timeout = timeout

    async def cards(self, routes, balancer, caller: Caller) -> List[Dict[str, Any]]:
        """One card per application route, each with its addresses.

        Args:
            routes: the table.
            balancer: whose view of the addresses the card shows beside the answers.
            caller: the administrator who asked, signed into each question.
        """
        applications = sorted((route for route in routes if route.application),
                              key=lambda item: item.prefix)
        asked = [self.card(address, caller) for route in applications
                 for address in route.upstreams]
        answers = iter(await asyncio.gather(*asked))
        out = []
        for route in applications:
            out.append({
                "route": route.id,
                "prefix": route.prefix,
                "addresses": [{**next(answers), **balancer.state(route.id, address)}
                              for address in route.upstreams],
            })
        return out

    async def card(self, address: str, caller: Caller) -> Dict[str, Any]:
        """What one address answers to the three questions."""
        own, health, metrics = await asyncio.gather(
            self._get(address, SELF_PATH, caller),
            self._get(address, HEALTH_PATH, None),
            self._get(address, METRICS_PATH, caller),
        )
        card: Dict[str, Any] = {"address": address, "reachable": any(
            answer["status"] is not None for answer in (own, health, metrics))}
        card["self"] = _json_or_error(own)
        card["health"] = {"status": health["status"], "error": health["error"],
                          "state": (_json(health) or {}).get("status")}
        card["metrics"] = {"status": metrics["status"], "error": metrics["error"],
                           "series": _series(metrics) if metrics["status"] == 200 else None}
        return card

    async def _get(self, address: str, path: str, caller: Optional[Caller]) -> Dict[str, Any]:
        """One question: the status and the body, or the reason there is none."""
        request_id = request_id_of({})
        headers = {"X-Request-Id": request_id}
        if caller is not None and self.secret:
            headers.update(sign_identity(caller, self.secret, request_id))
        try:
            answer = await self.client.get(address + path, headers=headers,
                                           timeout=self.timeout)
        except httpx.HTTPError as error:
            return {"status": None, "body": b"", "error": str(error)[:200] or type(error).__name__}
        return {"status": answer.status_code, "body": answer.content, "error": None}


def _json(answer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The body as an object, or None."""
    import json

    try:
        value = json.loads(answer["body"] or b"")
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _json_or_error(answer: Dict[str, Any]) -> Dict[str, Any]:
    """The application's own report, or why it could not be read."""
    if answer["status"] == 200 and _json(answer) is not None:
        return {"status": 200, **_json(answer)}
    reason = answer["error"]
    if reason is None:
        reason = {401: "the address does not accept the gateway's identity -- is keepup-gateway "
                       "installed there with the same identity_secret?",
                  403: "the identity was accepted, the role was not",
                  404: "the address has no keepup-gateway, or it has no identity_secret"
                  }.get(answer["status"], f"answered {answer['status']}")
    return {"status": answer["status"], "error": reason}


def _series(answer: Dict[str, Any]) -> int:
    """How many samples a Prometheus answer carries."""
    text = (answer["body"] or b"").decode("utf-8", "replace")
    return sum(1 for line in text.splitlines() if line and not line.startswith("#"))
