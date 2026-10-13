"""Building the application.

Everything that used to happen while ``app/main.py`` was being imported happens
inside ``create_app`` instead: the application is created, its static
directories mounted, its middleware added and its own endpoints registered.
That is what makes a second application on this framework possible, and it is
also what makes the first one testable -- a test can build one with its own
settings instead of importing a module that builds it as a side effect.

The order of the start-up is not free, and two steps in it are the reason this
file exists rather than a list of calls in each application:

* the signing key is demanded before anything else, so a deployment that would
  accept tokens anyone could sign never starts;
* ``on_startup`` runs before the plugins, because a plugin subscribes to the
  application's bus while initialising and a bus started afterwards would have
  no subscribers.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from keepup import audit
from keepup import notification_bus
from keepup_cluster import cluster
from keepup_http import web
from keepup_modules import catalogue as modules
from keepup_http.api_docs import register_api_documentation
from keepup.api_versions import ApiVersionMiddleware
from keepup.audit import init_incoming_requests_table
from keepup_auth import dependencies as auth_dependencies
from keepup_auth import routes as auth_routes
from keepup_auth import panel_session, socket_sessions
from keepup_auth.oidc_routes import register_oidc_routes
from keepup_auth.identity import runtime as identity_runtime
from keepup.events import init_event_manager
from keepup_http.events_api import register_event_api_routes
from keepup.instance import get_instance_id
from keepup_http.lock_routes import register_lock_routes
from keepup_metrics.api import register_metrics_routes
from keepup_modules.catalogue import register_module_routes
from keepup.plugins.registry import (
    initialize_plugins,
    register_plugin_admin_routes,
    run_post_construct_processors,
)
from keepup_tasks.scheduler import register_scheduler_routes
from keepup.settings import KeepupSettings
from keepup_ui.themes import config_service, register_theme_routes
from keepup_http.web import register_web_routes

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "create_app",
    "require_signing_key",
]

logger = logging.getLogger(__name__)


def require_signing_key() -> None:
    """Stop the start-up when the token signing key is not configured.

    Here rather than at import time: importing a module to generate the API
    documentation, or to reach one of its utilities, is not a deployment and
    has no business demanding a production secret. Starting the server is, and
    a server that came up with the placeholder key from the setup examples
    would accept a token anybody could sign for any account.
    """
    from keepup.auth.signing_key import SigningKeyUnavailable, signing_key_problem

    problem = signing_key_problem()
    if problem:
        logger.critical(problem)
        raise SigningKeyUnavailable(problem)


class SecurityHeadersMiddleware:
    """The response headers a browser needs in order to refuse things.

    The panel is an ordinary page, so without X-Frame-Options anybody can frame
    it and read a click as the administrator's. The framework carried none of
    these headers at all, which for a package installed on a public address is
    a default rather than an omission.

    The Content-Security-Policy arrives here already written -- the framework's
    own or the application's (``KeepupSettings.content_security_policy``,
    keepup-93) -- and is sent as a policy or, with ``csp_report_only``, as a
    report the browser does not act on. A response that carries a policy of its
    own keeps it, like every other header here.

    Plain ASGI: it only adds headers to the start of a response. Written on
    BaseHTTPMiddleware it ran every request through a task and a body stream
    of its own, the most expensive kind of middleware Starlette has (keepup-88).
    A header the response already carries is left as it is.
    """

    def __init__(self, app, https=False, csp=None, csp_report_only=False):
        self.app = app
        headers = [(b"x-frame-options", b"SAMEORIGIN"),
                   (b"x-content-type-options", b"nosniff"),
                   (b"referrer-policy", b"same-origin")]
        if https:
            # Only over TLS: sent over plain HTTP it would be ignored, and a
            # deployment on a local network without TLS must stay reachable.
            headers.append((b"strict-transport-security", b"max-age=31536000"))
        if csp:
            name = (b"content-security-policy-report-only" if csp_report_only
                    else b"content-security-policy")
            # Headers travel as latin-1 in ASGI; a policy is written in ASCII.
            headers.append((name, csp.encode("latin-1", "replace")))
        self.headers = headers

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                missing = [(name, value) for name, value in self.headers if name not in present]
                if missing:
                    message = {**message, "headers": list(message.get("headers", [])) + missing}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def apply_performance(performance) -> None:
    """The pool from ``KeepupSettings.performance`` (keepup-38).

    The pool may already exist: applications touch the database before
    create_app (their init_db). It is closed then, and the next query opens one
    with the application's values -- safe at start-up, before any request.
    """
    from keepup.db import DatabaseManagerV2, db_config
    if db_config.apply_pool(performance.db_pool_size, performance.db_pool_max_overflow,
                            performance.db_pool_timeout, performance.db_pool_recycle,
                            performance.db_pool_ping_after_idle):
        DatabaseManagerV2.dispose()
    from keepup import cache
    cache.set_ttl(performance.catalogue_cache_seconds)


def apply_settings(settings: KeepupSettings) -> None:
    """Hand each part of the framework the application's values.

    Done in one place so that what the application supplies is visible as a
    list, rather than as a configure() call hidden in each module's import.
    """
    performance = settings.performance
    audit.configure(redaction=settings.audit_redaction,
                    flush_interval=performance.audit_flush_interval,
                    max_size=performance.audit_buffer_size)
    apply_performance(performance)
    web.configure(
        static_dir=settings.static_dir,
        client_page=settings.client_page,
        version_file=settings.version_file,
        favicon_file=settings.favicon_file,
    )
    auth_dependencies.configure_panel_gate(pending=settings.pending_documents)
    if not settings.database_dialect:
        # The framework's configuration is the default; naming the dialect lets
        # the kernel choose a driver without importing a database (keepup-123).
        from keepup.db import db_config as _db_config
        settings.database_dialect = "postgresql" if _db_config.is_postgres() else "sqlite"
    # Who is calling and what they may do, behind the two names the kernel owns
    # (keepup-119). Until keepup-auth is a plugin of its own this is the
    # framework's own sign-in; afterwards it is that plugin's register().
    from keepup.auth.identity import access as identity_access
    from keepup.kernel import observability, security
    # What is written down about a call, behind the two names the kernel owns
    # (keepup-111). Until keepup-audit is a plugin this is the framework's own,
    # and it is built here rather than imported from the plugin file: the
    # framework's own plugins are data of the package, not a module of it.
    from keepup.audit import IncomingRequestLogger, log_api_request
    observability.set_recording(observability.Recording(
        start=log_api_request, end=IncomingRequestLogger.end_request, name="keepup audit"))
    security.set_identity(security.Identity(
        subject_dependency=auth_dependencies.get_panel_user,
        checker=identity_access.check,
        name="keepup sign-in",
    ))
    auth_routes.configure(
        password=settings.password_rule,
        login_record=settings.record_login,
    )
    auth_routes.configure_public_config(settings.public_config)
    # The panel's plugin report reads the deployment's own catalogue, wherever
    # the application keeps it -- not the default path (keepup-93).
    try:
        from keepup.plugins import admin as plugins_admin

        plugins_admin.configure(settings.plugins_config_path)
    except ImportError:
        # The decision about a plugin is the modules capability's (keepup-113);
        # without it the declared file and the environment decide.
        logger.info("keepup-modules is not installed: no plugin overrides to read")
    # The catalogue of panel sections and their grants is seeded from the same
    # file, which until keepup-98 was only the default one.
    modules.configure(settings.plugins_config_path)
    # Themes are declared here and not when the themes module is imported:
    # there is no application at that point and nothing for it to declare --
    # which is exactly why a declared theme never reached the database. The
    # service brings itself up on this call.
    config_service.register_built_in_themes(settings.built_in_themes)
    config_service.initialize()


class GatedStaticFiles(StaticFiles):
    """Static files the browser rechecks on every load, some of them gated.

    Starlette sends ETag and Last-Modified but no Cache-Control. Without one a
    browser falls back to heuristic freshness -- roughly a tenth of the file's
    age -- and stops asking the server at all, so an edited module keeps
    serving its old copy until a hard reload. `no-cache` still permits caching;
    it only requires revalidation, which the ETag answers with an empty 304.

    A gated page is served only while the plugin behind it runs: a page that
    says a payment succeeded, reachable by its address on a deployment with no
    payments, says something untrue.
    """

    def __init__(self, *args, gated_pages=None, plugin_manager=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.gated_pages = gated_pages or {}
        self.plugin_manager = plugin_manager

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

    async def get_response(self, path, scope):
        plugin_id = self.gated_pages.get(str(path).lstrip("/"))
        if plugin_id and not self._plugin_running(plugin_id):
            from starlette.exceptions import HTTPException as StarletteHTTPException
            raise StarletteHTTPException(status_code=404)
        return await super().get_response(path, scope)

    def _plugin_running(self, plugin_id: str) -> bool:
        plugin = self.plugin_manager.get_plugin(plugin_id) if self.plugin_manager else None
        return bool(plugin and getattr(plugin, "initialized", False))


def _build_lifespan(settings: KeepupSettings):
    """The start-up and shutdown of one application built from these settings."""

    manager = settings.plugin_manager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        require_signing_key()

        # The plugin catalogue, raised beside the path this application already
        # has (keepup-123). Everything the runtime does is additive: it resolves
        # the catalogue and the profile, publishes the services a capability
        # offers and creates the tables a capability declares. A deployment whose
        # catalogue cannot start -- a required plugin missing, two providers of
        # one service -- is reported and the application boots anyway, because
        # 0.3.0 deployments reach this code with a catalogue that never existed.
        from keepup.kernel import create_runtime

        kernel = create_runtime(settings)
        try:
            await kernel.start()
        except Exception as error:  # noqa: BLE001 - a catalogue is not the application
            logger.warning("The plugin catalogue did not start: %s", error)
            kernel = None
        app.state.kernel = kernel

        # Shipping is loaded only for an application that names a collector
        # (keepup/log_shipping.py, keepup-24).
        # Table creation is a round of statements against the database; off the
        # loop, where the tasks started above already run (keepup-54).
        await asyncio.to_thread(init_incoming_requests_table)

        # The catalogue the application named reaches the panel here as well as
        # The catalogue is imported by the capability that owns it, on the
        # freeze (keepup-113): it is the union of the framework's own sections,
        # the application's file and what the running plugins declare, and the
        # last of those is only known once every plugin has been loaded.
        # The four loops the framework used to start here -- the audit buffer,
        # the audit sweep, the event log's sweep and the socket sweep -- belong
        # to the capabilities that own them and start with their plugins
        # (keepup-109). The kernel starts none of them.
        event_manager = await asyncio.to_thread(init_event_manager)
        if event_manager:
            logger.info("Event manager started successfully")
        else:
            logger.warning("Event manager failed to start")

        register_event_api_routes(app, declared_event_types=settings.declared_event_types)

        # Before on_startup and the plugins: both subscribe to the bus while
        # initialising, and a bus created after them would have no subscribers.
        bus = await _create_notification_bus(settings.notification_channel)

        # Before the plugins: a plugin subscribes to the application's bus
        # while initialising, and a bus created after it would have no
        # subscribers.
        if settings.on_startup is not None:
            await settings.on_startup(app)

        if manager is not None:
            await initialize_plugins(app, manager, config_path=settings.plugins_config_path)

        # The log handlers the running capabilities contribute -- the shipping
        # one, when a collector is named (keepup-118). A deployment that
        # installs none attaches nothing.
        attached_handlers = _attach_log_handlers(kernel)

        # The delivery between replicas is the database driver's (keepup-117):
        # the bus existed before the plugins, and takes its transport once they
        # have all registered the services they publish.
        await _start_notification_bus(bus, kernel)

        # The scheduler belongs to the tasks capability (keepup-109) and is
        # running by the time this line is reached: it starts with its plugin
        # (priority 80), before the cluster (85), which is the order a replica
        # an administrator stopped needs -- it comes back stopped, and stopping
        # pauses the scheduler.
        # This replica's controller is the cluster capability's now (keepup-117):
        # it is built and launched with that plugin, which starts after this
        # line because it only wants the scheduler (80 against 85).
        if manager is not None:
            asyncio.create_task(run_post_construct_processors(manager))

        # Whatever start-up read into the theme and section caches, it may have
        # changed since: an application's one-off changesets run after the
        # framework's schema and can switch the active theme or take a section
        # from a role. The first request reads afresh.
        from keepup import cache
        cache.invalidate_all()

        yield

        _detach_log_handlers(attached_handlers)

        if kernel is not None:
            # The services a capability published are closed with the process
            # that published them (keepup-106).
            await kernel.stop()

        if manager is not None:
            await manager.cleanup_all()

        if settings.on_shutdown is not None:
            await settings.on_shutdown(app)

        await identity_runtime.shutdown()

        await _stop_notification_bus(bus)

    return lifespan


def _attach_log_handlers(kernel):
    """Attach what the running capabilities contribute to the application's log.

    The contribution is the contract (keepup-118): the capability builds the
    handler and says so, and the application -- which owns the root logger --
    attaches it. Until `keepup-http` mounts contributions itself this is the
    bridge, and it has the same shape as the routes one (keepup-121).
    """
    contributions = getattr(kernel, "contributions", None)
    handlers = list(contributions.of("logging")) if contributions is not None else []
    root = logging.getLogger()
    for handler in handlers:
        root.addHandler(handler)
    if handlers:
        logging.getLogger(__name__).info("Attached %s contributed log handler(s)",
                                         len(handlers))
    return handlers


def _detach_log_handlers(handlers):
    """Take them off again: a second application in one process must not double-log."""
    root = logging.getLogger()
    for handler in handlers or []:
        root.removeHandler(handler)


async def _create_notification_bus(channel):
    """Install the replicas' bus on the application's channel, if it named one.

    Created before the plugins and started after them: a plugin subscribes to the
    bus while it initialises, and the transport that carries an envelope to the
    other replicas is a service only the drivers publish (keepup-117).
    """
    if not channel:
        return None
    bus = notification_bus.NotificationBus(get_instance_id(), channel)
    notification_bus.set_notification_bus(bus)
    # The other replicas' changes to the theme and the section catalogue.
    from keepup import cache
    cache.attach_to_bus(bus)
    socket_sessions.attach_to_bus(bus)
    return bus


async def _start_notification_bus(bus, kernel=None):
    """Give the bus the delivery its driver published, and start listening."""
    if bus is None:
        return None
    services = getattr(kernel, "services", None)
    if services is not None and services.has(notification_bus.SERVICE_NOTIFY_TRANSPORT):
        bus.set_transport(services.require(notification_bus.SERVICE_NOTIFY_TRANSPORT))
    await bus.start()
    return bus


async def _stop_notification_bus(bus):
    if bus is None:
        return
    await bus.stop()
    socket_sessions.detach_from_bus()
    if notification_bus.get_notification_bus() is bus:
        notification_bus.set_notification_bus(None)








def create_app(settings: KeepupSettings = None) -> FastAPI:
    """Build an application from these settings.

    The plugin manager, when the settings carry one, is left on
    ``app.state.plugin_manager``: the entry point re-exports it, because that
    is the object plugins reach for.
    """
    settings = settings or KeepupSettings()
    apply_settings(settings)
    # Before any route exists: a provider that cannot be built stops the start
    # here, and one that can is what the routes below reach (keepup-91).
    identity_runtime.configure(settings.identity_provider)

    # /docs belongs to the public gateway reference, so the interactive API docs
    # live under /api. FastAPI's own schema and pages are switched off: the
    # schema describes every route and FastAPI would hand it to anybody. The
    # framework registers its own right here, ahead of any other route, which is
    # the place FastAPI's constructor gave them (keepup/api_docs.py, keepup-96).
    app = FastAPI(
        title=settings.title,
        lifespan=_build_lifespan(settings),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    register_api_documentation(app, settings)
    app.state.settings = settings
    app.state.plugin_manager = settings.plugin_manager

    for mount in settings.static_mounts:
        app.mount(
            mount.path,
            GatedStaticFiles(
                directory=mount.directory,
                gated_pages=settings.gated_pages,
                plugin_manager=settings.plugin_manager,
            ),
            name=mount.name,
        )

    # The package's own shell, always mounted and never from the working
    # directory: an installed keepup renders its panel out of its own files,
    # and an application that ships none still gets one (keepup-3).
    app.mount(
        settings.shell_mount,
        GatedStaticFiles(
            directory=web.SHELL_DIR,
            gated_pages={},
            plugin_manager=None,
        ),
        name="keepup-shell",
    )

    # Empty unless the application named origins: see KeepupSettings.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        # The panel session is a cookie now. With credentials allowed, Starlette
        # answers a request carrying cookies with that request's own origin, and
        # any site could read the panel's answers in the user's name.
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if settings.security_headers:
        app.add_middleware(SecurityHeadersMiddleware,
                           https=os.getenv("SSL_ENABLED", "false").lower() == "true",
                           csp=settings.content_security_policy,
                           csp_report_only=settings.csp_report_only)

    # A panel signed in before the CSRF value was bound to the session gets the
    # bound one on its next read (keepup/auth/panel_session.py, keepup-72).
    app.add_middleware(panel_session.CsrfCookieRefresh)

    # A replica an administrator stopped refuses the API (keepup/cluster.py).
    # Added before the version middleware, so it runs inside it and sees the
    # registered path.
    app.add_middleware(cluster.StoppedReplicaGate)

    # The largest body a route accepts, checked before the route reads it
    # (keepup/body_limit.py). Inside the version middleware, so it matches the
    # registered path; it finds the route itself, before routing, since a body
    # read by the route is a body already received.
    from keepup.body_limit import BodyLimitMiddleware
    app.add_middleware(BodyLimitMiddleware, router_of=app,
                       default_limit=(settings.max_upload_bytes
                                      if settings.max_upload_bytes is not None
                                      else settings.max_json_bytes))

    # Versioned paths (/api/v1, /ws/v1) reach the same handlers; added last, so it
    # runs first and every middleware and route below sees the registered path.
    app.add_middleware(ApiVersionMiddleware)

    if os.getenv('SSL_ENABLED', 'false').lower() == 'true' \
            and os.getenv('FORCE_HTTPS', 'false').lower() == 'true':
        from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware

        app.add_middleware(HTTPSRedirectMiddleware)
        logger.info("HTTPS redirect middleware enabled - HTTP requests will be redirected to HTTPS")

    register_web_routes(app)
    register_module_routes(app)
    # Registered only when the application configured a provider; without one
    # the paths do not exist at all.
    register_oidc_routes(app, settings)
    # The transport that mounts what a capability declares is keepup-http, and
    # until it exists the application registers this capability's routes itself
    # -- the same bridge the other capabilities still travel (keepup-121).
    register_metrics_routes(app, public=settings.metrics_public)
    register_lock_routes(app)
    register_scheduler_routes(app)
    cluster.register_cluster_routes(app)
    register_theme_routes(app, config_service)
    # Sign-in is registered whatever the application: one without plugins used
    # to get a panel nobody could sign into (keepup-89). What needs plugins --
    # telling them an account was blocked -- has none to tell then.
    auth_routes.register_auth_routes(app, settings.plugin_manager)
    if settings.plugin_manager is not None:
        try:
            register_plugin_admin_routes(app, settings.plugin_manager)
        except ImportError:
            # The plugin report belongs to the modules capability (keepup-113).
            logger.info("keepup-modules is not installed: no plugin report in the panel")

    return app
