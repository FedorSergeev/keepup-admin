"""Which declared plugins run, and where that decision came from.

Until this module the answer was the union of the roles' plugin lists in
modules.json: a plugin unrelated to any role -- the websocket channel, the
Telegram bot -- had to be granted to one or it silently did not start, and
four of twenty declared plugins ran. That glued two different decisions
together: what runs on the server, and what a role is shown.

Here the decision is explicit. A plugin runs when its own description says
``enabled: true`` or the deployment's environment names it; a role's list
means visibility only. Pure: configuration and environment come in as
mappings, decisions go out, nothing is read or loaded -- which is what lets
the cases be checked without an application.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping

#: Comma-separated plugin ids the deployment enables regardless of the file.
ENABLE_ENV = "PLUGINS_ENABLE"
#: Comma-separated plugin ids the deployment disables regardless of the file.
#: Wins over ENABLE_ENV: the safe outcome of a contradiction is "off".
DISABLE_ENV = "PLUGINS_DISABLE"

#: Where a decision came from.
SOURCE_CONFIG = "config"     # the plugin's own ``enabled`` flag in modules.json
SOURCE_ENV = "env"           # PLUGINS_ENABLE / PLUGINS_DISABLE
SOURCE_PANEL = "panel"       # an administrator's override, kept in the database
SOURCE_DEFAULT = "default"   # the flag is absent: not enabled

#: The flag in a plugin's description. Absent means "off": the alternative
#: would have switched on all twenty the day the mechanism changed.
ENABLED_FLAG = "enabled"


@dataclass(frozen=True)
class Decision:
    """Whether one declared plugin is on, and who decided it."""

    plugin_id: str
    enabled: bool
    source: str


@dataclass
class Resolution:
    """The decision for every declared plugin, plus ids nobody declared."""

    #: In declaration order, one per declared plugin.
    decisions: Dict[str, Decision] = field(default_factory=dict)
    #: Ids named in the environment that no declaration matches. Reported,
    #: never fatal: a typo in a deployment must not take the server down.
    unknown: List[str] = field(default_factory=list)


def parse_id_list(value: Any) -> List[str]:
    """Ids from a comma-separated variable, blanks dropped, order kept."""
    if not value:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def declared_plugins(config: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """The plugin descriptions of modules.json, in file order."""
    return [p for p in (config.get("plugins") or []) if isinstance(p, Mapping) and p.get("id")]


def decided_by_environment(plugin_id: str, environ: Mapping[str, Any]) -> bool:
    """Whether the deployment has already decided about this plugin.

    What the panel's refusal is built on: an override that the environment
    would overrule is a trap, so it is not accepted at all.
    """
    return (plugin_id in parse_id_list(environ.get(DISABLE_ENV))
            or plugin_id in parse_id_list(environ.get(ENABLE_ENV)))


def resolve(config: Mapping[str, Any], environ: Mapping[str, Any],
            overrides: Mapping[str, bool] = None) -> Resolution:
    """Decide, for every declared plugin, whether it runs.

    Three sources, strongest first. The environment belongs to one deployment
    and protects that stand, so it wins; disable wins over enable, because the
    safe outcome of a contradiction is "off". Then an administrator's override
    from the panel, kept in this deployment's database. Then the file, which is
    committed and shared between deployments. Absent everywhere means off.

    ``overrides`` for a plugin nobody declared is ignored, like an unknown id
    in the environment: a stale row must not conjure a plugin.
    """
    enable = parse_id_list(environ.get(ENABLE_ENV))
    disable = parse_id_list(environ.get(DISABLE_ENV))
    overrides = overrides or {}
    declared = declared_plugins(config)
    known = {p["id"] for p in declared}

    resolution = Resolution()
    for plugin in declared:
        pid = plugin["id"]
        if pid in disable:
            decision = Decision(pid, False, SOURCE_ENV)
        elif pid in enable:
            decision = Decision(pid, True, SOURCE_ENV)
        elif isinstance(overrides.get(pid), bool):
            decision = Decision(pid, overrides[pid], SOURCE_PANEL)
        elif isinstance(plugin.get(ENABLED_FLAG), bool):
            decision = Decision(pid, plugin[ENABLED_FLAG], SOURCE_CONFIG)
        else:
            decision = Decision(pid, False, SOURCE_DEFAULT)
        resolution.decisions[pid] = decision

    resolution.unknown = [pid for pid in enable + disable if pid not in known]
    return resolution


def enabled_ids(resolution: Resolution) -> List[str]:
    """The ids that run, in declaration order."""
    return [d.plugin_id for d in resolution.decisions.values() if d.enabled]


def summary_line(resolution: Resolution) -> str:
    """One line for the start-up log naming what did not get enabled.

    A plugin that does not start is otherwise invisible: the loader logs
    nothing about a plugin it was never asked to load.
    """
    enabled = enabled_ids(resolution)
    total = len(resolution.decisions)
    line = f"Plugins: enabled {len(enabled)} of {total} declared"
    silent = [pid for pid in resolution.decisions if pid not in enabled]
    if silent:
        line += "; not enabled: " + ", ".join(silent)
    return line


def status_report(config: Mapping[str, Any], resolution: Resolution, manager: Any,
                  desired: Resolution = None,
                  environ: Mapping[str, Any] = None) -> List[Dict[str, Any]]:
    """One row per declared plugin: the decision and what became of it.

    ``manager`` is the plugin manager: ``plugins`` holds what was loaded,
    ``loaded_plugins`` what initialised, and ``outcomes`` why each declared
    plugin ended up where it did. An enabled plugin that is loaded and not
    initialised is the case that used to be visible only in the log; it now
    carries its outcome and the reason it failed.

    ``resolution`` is the snapshot taken at start-up -- what is actually
    running, because routes are built once and not removed. ``desired`` is the
    decision in force now, overrides included; where the two differ, the row
    is marked as awaiting a restart. The mark is that difference and not a
    stored column, which would have to be cleared when an override is put back
    and would not be.
    """
    loaded = getattr(manager, "plugins", {}) or {}
    initialized = getattr(manager, "loaded_plugins", {}) or {}
    outcomes = getattr(manager, "outcomes", {}) or {}
    environ = environ or {}
    rows = []
    for plugin in declared_plugins(config):
        pid = plugin["id"]
        decision = resolution.decisions.get(pid) or Decision(pid, False, SOURCE_DEFAULT)
        wanted = decision if desired is None else (
            desired.decisions.get(pid) or Decision(pid, False, SOURCE_DEFAULT))
        rows.append({
            "id": pid,
            "name": plugin.get("name") or pid,
            "priority": plugin.get("priority") or 0,
            "enabled": decision.enabled,
            "source": decision.source,
            "loaded": pid in loaded,
            "initialized": pid in initialized,
            "desired_enabled": wanted.enabled,
            "desired_source": wanted.source,
            "pending_restart": wanted.enabled != decision.enabled,
            "decided_by_environment": decided_by_environment(pid, environ),
            # Why the plugin is in the state it is in. "loaded and not
            # initialized" is the case an administrator cannot otherwise tell
            # from "switched off": both answer 404 on the plugin's routes.
            "outcome": outcomes.get(pid, (None, None))[0],
            "failure_reason": outcomes.get(pid, (None, None))[1],
        })
    return rows
