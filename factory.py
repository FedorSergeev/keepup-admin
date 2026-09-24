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
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from keepup import audit, cluster, logging_setup, notification_bus, web
from keepup.api_versions import ApiVersionMiddleware
from keepup.audit import (audit_retention_background, background_buffer_flusher,
                          init_incoming_requests_table)
from keepup.auth import dependencies as auth_dependencies
from keepup.auth import routes as auth_routes
from keepup.auth.oidc_routes import register_oidc_routes
from keepup.events import init_event_manager, register_event_api_routes
from keepup.instance import get_instance_id
from keepup.locks import register_lock_routes
from keepup.logging_setup import init_remote_logging
from keepup.metrics import register_metrics_routes, update_metrics_background
from keepup.metrics_retention import metrics_retention_background
from keepup.modules import register_module_routes
from keepup.plugins.registry import (
    get_plugins_status,
    initialize_plugins,
    register_plugin_admin_routes,
    run_post_construct_processors,
)
from keepup.scheduler import init_scheduler, register_scheduler_routes
from keepup.settings import KeepupSettings
from keepup.themes import config_service, register_theme_routes
from keepup.web import register_web_routes

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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """The response headers a browser needs in order to refuse things.

    The panel is an ordinary page, so without X-Frame-Options anybody can frame
    it and read a click as the administrator's. The framework carried none of
    these headers at all, which for a package installed on a public address is
    a default rather than an omission.
    """

    def __init__(self, app, https=False):
        super().__init__(app)
        self.https = https

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if self.https:
            # Only over TLS: sent over plain HTTP it would be ignored, and a
            # deployment on a local network without TLS must stay reachable.
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000")
        return response


def apply_settings(settings: KeepupSettings) -> None:
    """Hand each part of the framework the application's values.

    Done in one place so that what the application supplies is visible as a
    list, rather than as a configure() call hidden in each module's import.
    """
    logging_setup.configure(
        project_name=settings.project_name,
        remote_url=settings.remote_log_url,
        remote_token=settings.remote_log_token,
        flush_interval=settings.remote_log_flush_interval,
        batch_size=settings.remote_log_batch_size,
    )
    audit.configure(redaction=settings.audit_redaction)
    web.configure(
        static_dir=settings.static_dir,
        client_page=settings.client_page,
        version_file=settings.version_file,
        favicon_file=settings.favicon_file,
    )
    auth_dependencies.configure_panel_gate(pending=settings.pending_documents)
    auth_routes.configure(
        password=settings.password_rule,
        login_record=settings.record_login,
    )
    auth_routes.configure_public_config(settings.public_config)
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

        remote_handler = init_remote_logging()
        metrics_task = asyncio.create_task(update_metrics_background())
        # Snapshots are written every fifteen seconds and without this sweep
        # grow without bound. The sweep runs under a distributed lock: the work
        # is shared across every replica.
        retention_task = asyncio.create_task(metrics_retention_background())

        init_incoming_requests_table()
        flusher_task = asyncio.create_task(background_buffer_flusher())
        # The audit table had no sweep at all while snapshots and events both
        # had one, so it grew for as long as the deployment ran (keepup-11).
        audit_retention_task = asyncio.create_task(audit_retention_background())

        event_manager = init_event_manager()
        if event_manager:
            logger.info("Event manager started successfully")
        else:
            logger.warning("Event manager failed to start")

        register_event_api_routes(app, declared_event_types=settings.declared_event_types)

        # Before on_startup and the plugins: both subscribe to the bus while
        # initialising, and a bus created after them would have no subscribers.
        bus = await _start_notification_bus(settings.notification_channel)

        # Before the plugins: a plugin subscribes to the application's bus
        # while initialising, and a bus created after it would have no
        # subscribers.
        if settings.on_startup is not None:
            await settings.on_startup(app)

        if manager is not None:
            await initialize_plugins(app, manager, config_path=settings.plugins_config_path)

        scheduler = init_scheduler()
        scheduler.start()

        logger.info("Scheduler started with tasks:")
        for job in scheduler.get_jobs():
            logger.info(f" - {job.id} (next run: {job.next_run_time})")

        # After the scheduler: a replica an administrator stopped comes back
        # stopped, and stopping pauses the scheduler.
        controller = cluster.ReplicaController(plugins=_plugin_picture(manager),
                                               build=_build_label())
        cluster.set_controller(controller)
        controller.launch()

        if manager is not None:
            asyncio.create_task(run_post_construct_processors(manager))

        yield

        await controller.shutdown()
        cluster.set_controller(None)

        if manager is not None:
            await manager.cleanup_all()

        if settings.on_shutdown is not None:
            await settings.on_shutdown(app)

        await _stop_notification_bus(bus)

        flusher_task.cancel()
        metrics_task.cancel()
        retention_task.cancel()
        audit_retention_task.cancel()
        for task in (flusher_task, metrics_task, retention_task, audit_retention_task):
            try:
                await task
            except asyncio.CancelledError:
                # Each task is awaited on its own: in a shared try the first
                # cancellation left the block and the rest stayed uncancelled.
                pass

        await flush_remaining_logs()

        if scheduler:
            scheduler.shutdown()
            logger.info("Scheduler stopped")

        if remote_handler:
            remote_handler.wrapper.force_flush()

    return lifespan


async def _start_notification_bus(channel):
    """Install and start the replicas' bus on the application's channel, if it named one."""
    if not channel:
        return None
    bus = notification_bus.NotificationBus(get_instance_id(), channel)
    notification_bus.set_notification_bus(bus)
    await bus.start()
    return bus


async def _stop_notification_bus(bus):
    if bus is None:
        return
    await bus.stop()
    if notification_bus.get_notification_bus() is bus:
        notification_bus.set_notification_bus(None)


def _plugin_picture(manager):
    """What runs in this replica and what awaits its restart, for the cluster registry.

    The same report /api/admin/plugins answers with, so the two cannot disagree.
    """
    async def picture():
        if manager is None:
            return cluster.PluginPicture([], [])
        try:
            rows = (await get_plugins_status(manager)).get("plugins", [])
        except Exception as error:
            # The replica still has to publish itself; an unreadable plugin
            # report must not make it look gone.
            logger.warning(f"Cluster: plugin report unavailable: {error}")
            rows = []
        return cluster.PluginPicture(
            running=[row["id"] for row in rows if row.get("initialized")],
            pending=[row["id"] for row in rows if row.get("pending_restart")])
    return picture


def _build_label():
    """The build this replica runs, from the version file build_image.sh writes."""
    try:
        with open(os.path.join(web.STATIC_DIR, web.VERSION_FILE), encoding="utf-8") as f:
            version = json.load(f)
    except (OSError, ValueError):
        return None
    label = str(version.get("version") or "")
    if version.get("build_number"):
        label += f" #{version['build_number']}"
    return label or None


async def flush_remaining_logs():
    """Ship the remaining incoming-request logs at shutdown."""
    try:
        logger.info("Flushing remaining incoming request logs...")
        count = await audit.IncomingRequestLogger.flush_buffer()
        logger.info(f"Flushed {count} remaining logs")
    except Exception as e:
        logger.error(f"Error flushing remaining logs: {str(e)}")


def _create_stripped_app(settings: KeepupSettings) -> FastAPI:
    """The application DISABLE_HTTP_SERVER asks for: metrics and a raw log.

    Not a smaller version of the real one -- a different one. Routes are absent
    rather than disabled, which is why a request that 404s here is expected and
    not a fault to look for elsewhere.
    """
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    from fastapi.responses import Response as FastAPIResponse

    app = FastAPI(title=settings.title)

    @app.middleware("http")
    async def log_raw_request(request: Request, call_next):
        """Middleware logging raw HTTP requests."""
        logger.info("=" * 80)
        logger.info("📥 RAW REQUEST RECEIVED")
        logger.info(f"Method: {request.method}")
        logger.info(f"URL: {request.url}")
        logger.info(f"Path: {request.url.path}")
        logger.info(f"Query params: {dict(request.query_params)}")

        # Header names only, and never their values. This used to write every
        # header out in full -- Authorization and Cookie among them -- and then
        # the same thing again as text and as hex, and RemoteLoggerWrapper
        # shipped the lot to a collector. One environment variable turned a
        # replica into a device that records other people's credentials
        # (task keepup-13).
        logger.info(f"Header names: {', '.join(request.headers.keys())}")

        body = await request.body()
        if body:
            try:
                logger.info(f"Body: {len(body)} bytes")
            except Exception:
                logger.info(f"Body (binary): {body.hex()[:1000]}")

        logger.info("=" * 80)

        response = await call_next(request)
        logger.info(f"📤 RESPONSE: {response.status_code}")
        return response

    @app.get("/metrics")
    async def metrics():
        return FastAPIResponse(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def create_app(settings: KeepupSettings = None) -> FastAPI:
    """Build an application from these settings.

    The plugin manager, when the settings carry one, is left on
    ``app.state.plugin_manager``: the entry point re-exports it, because that
    is the object plugins reach for.
    """
    settings = settings or KeepupSettings()
    apply_settings(settings)

    if settings.disable_http_server:
        return _create_stripped_app(settings)

    # /docs belongs to the public gateway reference, so the interactive API docs
    # live under /api. FastAPI registers them in the constructor, ahead of any
    # app route, so a page at /docs could not shadow them otherwise.
    app = FastAPI(
        title=settings.title,
        lifespan=_build_lifespan(settings),
        docs_url=settings.docs_url,
        redoc_url=settings.redoc_url,
    )
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
                           https=os.getenv("SSL_ENABLED", "false").lower() == "true")

    # A replica an administrator stopped refuses the API (keepup/cluster.py).
    # Added before the version middleware, so it runs inside it and sees the
    # registered path.
    app.add_middleware(cluster.StoppedReplicaGate)

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
    register_metrics_routes(app, public=settings.metrics_public)
    register_lock_routes(app)
    register_scheduler_routes(app)
    cluster.register_cluster_routes(app)
    register_theme_routes(app, config_service)
    if settings.plugin_manager is not None:
        register_auth_routes = auth_routes.register_auth_routes
        register_auth_routes(app, settings.plugin_manager)
        register_plugin_admin_routes(app, settings.plugin_manager)

    return app
