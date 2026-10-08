"""One runtime: the catalogue resolved, the plugins running, the services published.

This is the constructor the specification describes in
``doc/plugin_constructor.md``. It belongs to an instance and not to the module:
two runtimes in one process resolve, publish and report separately, which is what
the framework claims it supports and what 0.3.0's module-level configuration
prevented.

The order is the contract (section 4.8 of the specification), and it exists for
a reason that is invisible in the code: the administrator's decision about
plugins lives in a table, the table lives in a database, and the database is a
plugin. So the required set is resolved from files and the environment alone, is
registered and initialised, and only then is the administrator asked.
"""

import asyncio
import inspect
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from keepup.kernel import catalogue as catalogue_module
from keepup.kernel.descriptor import (
    KIND_OPTIONAL,
    KIND_REQUIRED,
    KIND_TRANSPORT,
    PluginDescriptor,
)
from keepup.kernel.datasource import SERVICE_DATASOURCE
from keepup.kernel.contributions import (
    Contributions,
    collect as collect_contributions,
    mount,
)
from keepup.kernel.loader import USE_BUILTIN, Candidate, PluginLoader
from keepup.kernel.services import ServiceRegistry
from keepup.kernel.transports import route_specs_of, serve_all, transports_of
from keepup.plugins import enablement
from keepup.plugins.base import (
    OUTCOME_DISABLED,
    OUTCOME_FAILED,
    OUTCOME_INITIALIZED,
    OUTCOME_NOT_FOUND,
)

logger = logging.getLogger(__name__)

__all__ = [
    "KernelError",
    "OUTCOME_UNSATISFIED",
    "PluginState",
    "Runtime",
    "create_runtime",
    "maybe_await",
]

#: The outcome of a plugin whose requirement no provider satisfies.
OUTCOME_UNSATISFIED = "unsatisfied"

#: The profile that replaces the retired ``disable_http_server`` (section 5).
PROFILE_METRICS_ONLY = "metrics-only"

#: The two phases the start-up runs in.
PHASE_REQUIRED = "required"
PHASE_OPTIONAL = "optional"


class KernelError(Exception):
    """The deployment cannot be what it says it is, and the start stops."""


def maybe_await(value: Any) -> Any:
    """Await a value when it is awaitable, and return it otherwise.

    Args:
        value: whatever a hook returned.

    Returns:
        A coroutine that resolves to the value.
    """

    async def _resolve() -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    return _resolve()


@dataclass
class PluginState:
    """What became of one declared plugin.

    Attributes:
        plugin_id: the identifier.
        name: what the panel calls it.
        kind: the effective kind -- the code's, when the code is there.
        distribution: the distribution that carries it.
        priority: the order it initialises in.
        enabled: whether the deployment decided to run it.
        source: who decided -- config, env, panel, installed, not-installed.
        phase: the phase it was decided in.
        descriptor: what it declared.
        instance: the plugin, once constructed.
        config: the configuration it was given.
        loaded: whether a class was found and constructed.
        initialized: whether ``initialize()`` said yes.
        outcome: initialized, disabled, not_found, failed or unsatisfied.
        reason: why, when it is not initialized.
        missing_wants: the soft requirements nobody satisfied.
    """

    plugin_id: str
    name: str = ""
    kind: str = KIND_OPTIONAL
    distribution: str = ""
    priority: int = 0
    enabled: bool = False
    source: str = ""
    origin: str = ""
    phase: str = PHASE_OPTIONAL
    descriptor: Optional[PluginDescriptor] = None
    instance: Any = None
    config: Mapping[str, Any] = field(default_factory=dict)
    loaded: bool = False
    initialized: bool = False
    outcome: str = OUTCOME_DISABLED
    reason: str = ""
    missing_wants: List[str] = field(default_factory=list)


class Runtime:
    """The plugins of one deployment, their services and their life.

    Attributes:
        settings: what the application told the framework, when it told it.
        environ: the environment the decisions are read from.
        services: what the plugins published.
        states: one :class:`PluginState` per declared plugin, in catalogue order.
    """

    def __init__(
        self,
        settings: Any = None,
        *,
        builtin_catalogue: Optional[Mapping[str, Any]] = None,
        application_catalogue: Optional[Mapping[str, Any]] = None,
        profile: Optional[str] = None,
        environ: Optional[Mapping[str, str]] = None,
        overrides_reader: Optional[Callable[[], Mapping[str, bool]]] = None,
        plugins_dir: Optional[str] = None,
        builtin_dir: Any = USE_BUILTIN,
        entry_points: Optional[Iterable[Any]] = None,
        loader: Optional[PluginLoader] = None,
        table_setup: Optional[Callable[["Runtime"], Any]] = None,
        mounters: Sequence[Callable[["Runtime"], Any]] = (),
    ) -> None:
        self.settings = settings
        self.environ = dict(environ if environ is not None else os.environ)
        self.services = ServiceRegistry()
        self.states: Dict[str, PluginState] = {}
        self._init_order: List[str] = []
        self._loaded: Dict[str, Any] = {}
        self._started = False
        self._builtin_catalogue = builtin_catalogue
        self._application_catalogue = application_catalogue
        self._profile = profile if profile is not None else getattr(settings, "profile", None)
        self._overrides_reader = overrides_reader
        self._table_setup = table_setup
        self._mounters = list(mounters)
        self.plugins_dir = plugins_dir if plugins_dir is not None else getattr(
            settings, "plugins_dir", None
        )
        self.loader = loader or PluginLoader(self.plugins_dir, entry_points, builtin_dir=builtin_dir)
        self.catalogue: Dict[str, Any] = {}
        self.contributions = Contributions()

    # --- what a plugin reaches for ------------------------------------------

    @property
    def plugins(self) -> Dict[str, Any]:
        """Every constructed plugin, by identifier."""
        return dict(self._loaded)

    @property
    def loaded_plugins(self) -> Dict[str, Any]:
        """The initialised plugins, by identifier -- what ``get_plugin`` answers."""
        return {plugin_id: self._loaded[plugin_id] for plugin_id in self._init_order}

    def transports(self) -> List[Tuple[str, Any]]:
        """The initialised transport plugins, in the order they started."""
        return transports_of(self)

    def route_specs(self) -> List[Any]:
        """Every declared route, as a transport-neutral specification."""
        return route_specs_of(self)

    async def serve_all(self) -> None:
        """Serve on every enabled transport, returning when they have all stopped."""
        await serve_all(self)

    async def run_forever(self) -> None:
        """Start, serve, and stop when the serving is over.

        A deployment with no transport is a worker: it starts its plugins and
        waits, because that is what a process whose work is a scheduled job or
        a subscription does. Cancelling this coroutine stops the runtime.
        """
        await self.start()
        try:
            transports = self.transports()
            if transports:
                await serve_all(self, transports)
            else:
                logger.info("A worker with no transport: waiting until cancelled")
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            logger.info("Runtime cancelled")
            raise
        finally:
            await self.stop()

    def mount(self, registrars: Mapping[str, Any]) -> Dict[str, int]:
        """Hand the collected contributions to their consumers.

        Args:
            registrars: a callable per contribution kind, as
                :func:`keepup.kernel.contributions.mount` describes.

        Returns:
            How many contributions of each kind were mounted.
        """
        return mount(self.contributions, registrars, self)

    def get_plugin(self, plugin_id: str) -> Any:
        """Return an initialised plugin by identifier, or None.

        Args:
            plugin_id: the identifier the catalogue declares.

        Returns:
            The plugin, when it initialised.
        """
        if plugin_id in self._init_order:
            return self._loaded.get(plugin_id)
        return None

    def get_outcome(self, plugin_id: str) -> Tuple[Optional[str], str]:
        """Return ``(outcome, reason)`` for a declared plugin."""
        state = self.states.get(plugin_id)
        if state is None:
            return (None, "")
        return (state.outcome, state.reason)

    # --- the start ----------------------------------------------------------

    async def start(self) -> "Runtime":
        """Resolve, load, register, initialise and mount, in that order.

        Returns:
            This runtime, for chaining.

        Raises:
            CatalogueError: when the catalogue contradicts itself.
            KernelError: when the deployment cannot be what it says it is.
        """
        if self._started:
            return self
        candidates = self.loader.candidates()
        self.catalogue = self._compose_catalogue(candidates)

        required = self._decide(PHASE_REQUIRED, candidates)
        self._register(required, candidates)
        self.services.check_duplicates()
        self._refuse_substitutes(required)
        await self._initialize(required)
        await self._check_the_required(required)
        self._require_the_required(required)

        await self._create_tables(required)

        optional = self._decide(PHASE_OPTIONAL, candidates)
        # Which services are required is known from the descriptors of everything
        # that is enabled, before any of the optional plugins is constructed: a
        # service an enabled plugin cannot run without is required whether or not
        # that plugin has come up yet, and the second phase is the last moment at
        # which a decision about its provider can still be refused.
        self._mark_required_services(required + optional)
        for state in optional:
            self._refuse_a_provider_the_panel_chose(state)
        self._register(optional, candidates)
        await self._initialize(optional)

        await self._create_tables(optional)
        self._adopt_the_identity()

        self.services.freeze()

        # What the plugins contribute, collected once, after every plugin that
        # could contribute has initialised and before anything is mounted: the
        # transport mounts routes, sections, middleware and jobs from here
        # (kernel/contributions.py).
        self.contributions = collect_contributions(self)
        # Collecting builds a new collection, so every plugin that was handed the
        # old one is handed this: a capability reads the sections of the others
        # through it (keepup-104).
        for state in self.states.values():
            if state.instance is not None:
                state.instance.contributions = self.contributions

        for mounter in self._mounters:
            await maybe_await(mounter(self))

        await self._post_construct()
        self._started = True
        logger.info("Runtime: %s", self.summary_line())
        return self

    async def stop(self) -> None:
        """Clean the plugins up in the reverse of the order they started."""
        for plugin_id in reversed(self._init_order):
            instance = self._loaded.get(plugin_id)
            cleanup = getattr(instance, "cleanup", None)
            if not callable(cleanup):
                continue
            try:
                await maybe_await(cleanup())
            except Exception as error:  # noqa: BLE001 - one plugin must not stop the rest
                logger.error("Plugin %s did not clean up: %s", plugin_id, error)
        self.services.close()
        self._init_order.clear()
        self._started = False

    # --- the catalogue ------------------------------------------------------

    def _compose_catalogue(self, candidates: Mapping[str, Candidate]) -> Dict[str, Any]:
        """The framework's catalogue, the application's, a profile -- and what is installed.

        A distribution that is installed and declared nowhere is still offered:
        this is what "a required or transport plugin is enabled by being
        installed" means (doc/plugin_constructor.md section 4.9), and a plugin
        optional one found this way is offered and not enabled, because
        installing a capability is not the same as asking for it. A framework
        that ships two drivers of one required service says so in its catalogue
        -- ``plugins/builtin.json`` -- which is where a deployment's choice
        belongs (keepup-107).
        """
        builtin = self._builtin_catalogue
        if builtin is None:
            builtin = catalogue_module.builtin()
        application = self._application_catalogue
        if application is None:
            application = catalogue_module.read(
                getattr(self.settings, "plugins_config_path", None)
            )
        composed = catalogue_module.compose(builtin, application, self._profile)
        declared = {entry["id"] for entry in catalogue_module.declarations(composed)}
        for plugin_id, candidate in candidates.items():
            if plugin_id in declared:
                continue
            descriptor = candidate.descriptor
            composed["plugins"].append({
                "id": plugin_id,
                "name": descriptor.name or plugin_id,
                "kind": descriptor.kind,
                "priority": descriptor.priority,
                "_discovered": candidate.source,
            })
        return composed

    def _decide(self, phase: str, candidates: Mapping[str, Candidate]) -> List[PluginState]:
        """Decide the plugins of one phase, and record every one of them.

        Args:
            phase: :data:`PHASE_REQUIRED` or :data:`PHASE_OPTIONAL`.
            candidates: what the loader found.

        Returns:
            The states of this phase, in catalogue order.

        Raises:
            KernelError: when the environment disables a required plugin, or the
                catalogue enables one that is not installed.
        """
        wanted = (KIND_REQUIRED,) if phase == PHASE_REQUIRED else (KIND_OPTIONAL, KIND_TRANSPORT)
        decisions = self._optional_decisions() if phase == PHASE_OPTIONAL else {}
        states: List[PluginState] = []

        for entry in catalogue_module.declarations(self.catalogue):
            plugin_id = str(entry["id"])
            candidate = candidates.get(plugin_id)
            kind = candidate.descriptor.kind if candidate is not None else catalogue_module.kind_of(entry)
            if kind not in wanted:
                continue
            if phase == PHASE_OPTIONAL and plugin_id in self.states:
                continue
            priority = int(entry.get("priority") or (candidate.descriptor.priority if candidate else 0))
            state = PluginState(
                plugin_id=plugin_id,
                name=(candidate.name if candidate else str(entry.get("name") or plugin_id)),
                kind=kind,
                distribution=(candidate.descriptor.distribution if candidate else ""),
                priority=priority,
                phase=phase,
                descriptor=(candidate.descriptor if candidate else None),
                config=dict(entry.get("config") or {}),
            )
            state.origin = candidate.source if candidate is not None else ""
            state.enabled, state.source = self._enabled(entry, kind, candidate, decisions, plugin_id)
            if state.enabled and candidate is None:
                state.outcome = OUTCOME_NOT_FOUND
                state.reason = f"enabled but not installed: {plugin_id}"
                self.states[plugin_id] = state
                if kind == KIND_REQUIRED and entry.get("enabled") is True:
                    raise KernelError(
                        f"the required plugin {plugin_id} is enabled in the catalogue "
                        "but no distribution provides it"
                    )
                logger.warning(
                    "%s is enabled but not installed; the deployment runs without it", plugin_id
                )
                continue
            if not state.enabled:
                state.outcome = OUTCOME_DISABLED
                state.reason = state.source
            elif candidate is None:
                state.outcome = OUTCOME_NOT_FOUND
                state.reason = self.loader.outcome_for_missing(plugin_id)[1]
            self.states[plugin_id] = state
            states.append(state)
        return states

    def _adopt_the_identity(self) -> None:
        """Let a plugin's sign-in answer for this process, if it published one.

        The kernel's two names are services; the HTTP dependency FastAPI was
        handed is a module-level callable, and the seam in
        ``keepup/kernel/security.py`` is where the two meet until the transport
        owns the application (keepup-123). A deployment whose plugin answered
        wins over whatever the composition root put there before it.
        """
        from keepup.kernel import security

        if not self.services.has(security.SERVICE_AUTH):
            return
        identity = self.services.require(security.SERVICE_AUTH)
        security.set_identity(identity)
        logger.info("The sign-in is answered by %s", getattr(identity, "name", "a plugin"))

    def _mark_required_services(self, states: Sequence[PluginState]) -> None:
        """Remember every service an enabled plugin cannot run without.

        Args:
            states: the plugins decided about, in either phase.
        """
        for state in states:
            if not state.enabled or state.descriptor is None:
                continue
            for requirement in state.descriptor.requires:
                self.services.mark_required(requirement.name)

    def _refuse_a_provider_the_panel_chose(self, state: PluginState) -> None:
        """A right the deployment has, not the panel: who provides a required service.

        An administrator decides whether an optional capability runs. Which
        database the application talks to, which audit sink it writes to, is a
        decision made in code and reviewed like code -- a panel that could change
        it would turn "an administrator of the panel" into "somebody who reads
        every row of it" (doc/plugin_constructor.md section 4.9).

        Args:
            state: the plugin just decided about.

        Raises:
            KernelError: when an administrator's override would enable a plugin
                that provides a service some enabled plugin cannot run without.
        """
        if state.source != enablement.SOURCE_PANEL or not state.enabled:
            return
        descriptor = state.descriptor
        if descriptor is None:
            return
        refused = sorted(set(descriptor.service_names) & set(self.services.required_services()))
        if refused:
            raise KernelError(
                f"{state.plugin_id} provides {', '.join(refused)}, which this deployment "
                "cannot run without: a provider is chosen in the catalogue, not in the panel"
            )

    def _refuse_substitutes(self, states: Sequence[PluginState]) -> None:
        """A provider of a required service has to be an installed distribution.

        A driver sees every row and every secret. A file dropped into the plugins
        directory is not in ``requirements``, not in a lock file and not in an
        audit, so it may provide an optional capability and may not provide the
        database (doc/plugin_constructor.md section 4.9).

        Args:
            states: the plugins of the required phase.

        Raises:
            KernelError: naming the file and the service it tried to provide.
        """
        required = self.services.required_services()
        for state in states:
            if not state.loaded:
                continue
            descriptor = state.descriptor
            if descriptor is None or state.origin != "directory":
                continue
            refused = sorted(set(descriptor.service_names) & set(required))
            if refused:
                raise KernelError(
                    f"{state.plugin_id} is a file of the application and provides "
                    f"{', '.join(refused)}: a provider of a required service must come "
                    "from an installed distribution"
                )

    async def _create_tables(self, states: Sequence[PluginState]) -> None:
        """Create the tables a phase's plugins declared, through the data source.

        Phase 4 of the specification's order: the framework's own tables and the
        required plugins' tables are created once the data source is up, and the
        optional plugins' as they come up. A plugin declares its tables --
        ``get_declared_tables()`` -- and never creates them itself, so the
        abstraction can create them in the order the foreign keys need, and a
        deployment whose data source is absent creates nothing and says so by the
        plugins it did not start.

        Args:
            states: the plugins of the phase just initialised.
        """
        if self._table_setup is not None:
            if self.services.has(SERVICE_DATASOURCE):
                await maybe_await(self._table_setup(self))
            return
        if not self.services.has(SERVICE_DATASOURCE):
            return
        ensure = getattr(self.services.require(SERVICE_DATASOURCE), "ensure_tables", None)
        if not callable(ensure):
            return
        for state in sorted(states, key=lambda item: item.priority):
            if not state.initialized or state.instance is None:
                continue
            declared = getattr(state.instance, "get_declared_tables", None)
            if not callable(declared):
                continue
            tables = list(declared())
            if not tables:
                continue
            logger.info("%s: ensuring %s declared table(s)", state.plugin_id, len(tables))
            await maybe_await(ensure(*tables))

    async def _check_the_required(self, states: Sequence[PluginState]) -> None:
        """Ask each required plugin whether the deployment can work at all.

        An ``initialize()`` that answered yes says the plugin came up. A
        ``check()`` answers the harder question -- does a connection open, is the
        schema there -- and its refusal is a start that stops, because a
        deployment that cannot work is worse than one that does not start
        (doc/plugin_constructor.md section 4.7).
        """
        for state in sorted(states, key=lambda item: item.priority):
            if not state.initialized or state.instance is None:
                continue
            check = getattr(state.instance, "check", None)
            if not callable(check):
                continue
            try:
                answer = await maybe_await(check())
            except Exception as error:  # noqa: BLE001 - an outcome, and a start that stops
                self._stopped(state, f"check failed: {type(error).__name__}: {error}")
                continue
            if answer is False:
                self._stopped(state, "check() returned false")

    def _stopped(self, state: PluginState, reason: str) -> None:
        """Record a required plugin that is not running, and take it out of the picture."""
        state.outcome = OUTCOME_FAILED
        state.reason = reason
        state.initialized = False
        if state.plugin_id in self._init_order:
            self._init_order.remove(state.plugin_id)
        logger.error("%s is not running: %s", state.plugin_id, reason)

    def _enabled(
        self,
        entry: Mapping[str, Any],
        kind: str,
        candidate: Optional[Candidate],
        decisions: Mapping[str, Any],
        plugin_id: str,
    ) -> Tuple[bool, str]:
        """Whether one plugin runs, and who decided it."""
        dialect = getattr(self.settings, "database_dialect", None)
        offered = entry.get("dialect")
        if dialect and offered:
            # One database is configured, so one driver runs: the choice is the
            # deployment's and not a consequence of what is installed
            # (doc/plugin_constructor.md section 4.9, keepup-123).
            return str(offered).lower() == str(dialect).lower(), enablement.SOURCE_CONFIG
        if kind in (KIND_REQUIRED, KIND_TRANSPORT):
            if plugin_id in enablement.parse_id_list(self.environ.get(enablement.DISABLE_ENV)):
                if candidate is not None:
                    raise KernelError(
                        f"{enablement.DISABLE_ENV} disables the required plugin {plugin_id}; "
                        "a deployment is not a deployment without it"
                    )
                return False, "not-installed"
            if entry.get("enabled") is False:
                return False, enablement.SOURCE_CONFIG
            if plugin_id in enablement.parse_id_list(self.environ.get(enablement.ENABLE_ENV)):
                return True, enablement.SOURCE_ENV
            if candidate is not None:
                return True, "installed"
            if entry.get("enabled") is True:
                # Asked for by name and not installed: the caller stops the start.
                return True, enablement.SOURCE_CONFIG
            return False, "not-installed"
        decision = decisions.get(plugin_id)
        if decision is None:
            return False, enablement.SOURCE_DEFAULT
        return bool(decision.enabled), str(decision.source)

    def _optional_decisions(self) -> Dict[str, Any]:
        """The administrator's and the deployment's decision about the rest."""
        overrides: Mapping[str, bool] = {}
        if self._overrides_reader is not None:
            try:
                overrides = self._overrides_reader() or {}
            except Exception as error:  # noqa: BLE001 - the report says so; the start goes on
                logger.error("The administrator's decisions are unreadable: %s", error)
                overrides = {}
        resolution = enablement.resolve(self.catalogue, self.environ, overrides)
        for unknown in resolution.unknown:
            logger.warning(
                "%s/%s name an undeclared plugin: %s",
                enablement.ENABLE_ENV,
                enablement.DISABLE_ENV,
                unknown,
            )
        return resolution.decisions

    # --- loading, services, initialisation ----------------------------------

    def _register(self, states: Sequence[PluginState], candidates: Mapping[str, Candidate]) -> None:
        """Construct the plugins of a phase and let them publish their services."""
        for state in states:
            if not state.enabled:
                continue
            candidate = candidates.get(state.plugin_id)
            if candidate is None:
                continue
            try:
                instance = self.loader.instantiate(candidate, state.config, state.priority, self)
            except Exception as error:  # noqa: BLE001 - one plugin must not stop the rest
                state.outcome, state.reason = self.loader.failure(state.plugin_id, error)
                if state.kind == KIND_REQUIRED:
                    raise KernelError(f"{state.plugin_id}: {state.reason}") from error
                continue
            instance.services = self.services
            # The live collection, so a plugin that offers sections or themes can
            # see what the others offered without asking the kernel (keepup-104).
            instance.contributions = self.contributions
            state.instance = instance
            state.loaded = True
            self._loaded[state.plugin_id] = instance
            self._publish(state)

    def _publish(self, state: PluginState) -> None:
        """Let a plugin publish what it provides, and remember what it needs."""
        descriptor = state.descriptor or PluginDescriptor(id=state.plugin_id)
        register = getattr(state.instance, "register", None)
        if callable(register):
            try:
                register(self.services)
            except Exception as error:  # noqa: BLE001
                state.outcome, state.reason = self.loader.failure(state.plugin_id, error)
                state.loaded = False
                self._loaded.pop(state.plugin_id, None)
                if state.kind == KIND_REQUIRED:
                    raise KernelError(f"{state.plugin_id}: {state.reason}") from error
                return
        for requirement in descriptor.requires:
            self.services.mark_required(requirement.name)

    async def _initialize(self, states: Sequence[PluginState]) -> None:
        """Initialise a phase in priority order, ties keeping catalogue order."""
        for state in sorted(states, key=lambda item: item.priority):
            if state.instance is None or not state.loaded:
                continue
            descriptor = state.descriptor or PluginDescriptor(id=state.plugin_id)
            missing = [r for r in descriptor.requires if not self.services.has(r.name, r.minimum)]
            if missing:
                state.outcome = OUTCOME_UNSATISFIED
                state.reason = "missing " + ", ".join(str(item) for item in missing)
                logger.error("%s is unsatisfied: %s", state.plugin_id, state.reason)
                continue
            state.missing_wants = [
                str(item) for item in descriptor.wants if not self.services.has(item.name, item.minimum)
            ]
            if state.missing_wants:
                logger.warning(
                    "%s runs degraded: no %s", state.plugin_id, ", ".join(state.missing_wants)
                )
            try:
                initialized = await maybe_await(state.instance.initialize())
            except Exception as error:  # noqa: BLE001 - an outcome, not a crash
                state.outcome = OUTCOME_FAILED
                state.reason = f"{type(error).__name__}: {error}"
                logger.exception("Plugin %s failed to initialise", state.plugin_id)
                continue
            if not initialized:
                state.outcome = OUTCOME_FAILED
                state.reason = "initialize() returned false"
                continue
            state.initialized = True
            state.outcome = OUTCOME_INITIALIZED
            self._init_order.append(state.plugin_id)

    def _require_the_required(self, states: Sequence[PluginState]) -> None:
        """Refuse to go on when a required plugin did not come up.

        Raises:
            KernelError: naming every required plugin that is not running.
        """
        broken = [
            f"{state.plugin_id} ({state.outcome}: {state.reason or 'no reason given'})"
            for state in states
            if state.kind == KIND_REQUIRED and state.enabled and not state.initialized
        ]
        if broken:
            raise KernelError("required plugins are not running: " + "; ".join(broken))

    async def _post_construct(self) -> None:
        """Run the self-checks that need everything else to be up."""
        for plugin_id in list(self._init_order):
            instance = self._loaded[plugin_id]
            hook = getattr(instance, "post_construct", None)
            if not callable(hook):
                continue
            try:
                await maybe_await(hook())
            except Exception as error:  # noqa: BLE001 - a self-check is not a crash
                logger.error("Post-construct of %s failed: %s", plugin_id, error)

    # --- reporting ----------------------------------------------------------

    def report(self) -> List[Dict[str, Any]]:
        """One row per declared plugin: the decision, the outcome and what it gives.

        Returns:
            The rows, in catalogue order -- what the admin plugin list shows.
        """
        rows = []
        for state in self.states.values():
            descriptor = state.descriptor
            rows.append(
                {
                    "id": state.plugin_id,
                    "name": state.name,
                    "kind": state.kind,
                    "distribution": state.distribution,
                    "priority": state.priority,
                    "enabled": state.enabled,
                    "source": state.source,
                    "origin": state.origin,
                    "phase": state.phase,
                    "loaded": state.loaded,
                    "initialized": state.initialized,
                    "outcome": state.outcome,
                    "reason": state.reason,
                    "degraded": list(state.missing_wants),
                    "provides": [
                        f"{name}>={version}" for name, version in (descriptor.provides if descriptor else ())
                    ],
                    "requires": [str(item) for item in (descriptor.requires if descriptor else ())],
                    "wants": [str(item) for item in (descriptor.wants if descriptor else ())],
                }
            )
        return rows

    def summary_line(self) -> str:
        """One line for the start-up log: what ran, what did not, and why."""
        running = [state.plugin_id for state in self.states.values() if state.initialized]
        silent = [
            f"{state.plugin_id} ({state.outcome})"
            for state in self.states.values()
            if not state.initialized and state.outcome != OUTCOME_DISABLED
        ]
        line = f"{len(running)} of {len(self.states)} declared plugins run"
        if silent:
            line += "; not running: " + ", ".join(silent)
        return line


def create_runtime(settings: Any = None, **options: Any) -> Runtime:
    """Build a runtime from what the application told the framework.

    Args:
        settings: a :class:`keepup.settings.KeepupSettings`, or None for a
            deployment assembled entirely from the arguments below.
        **options: what :class:`Runtime` accepts.

    Returns:
        The runtime, not yet started.
    """
    return Runtime(settings, **options)
