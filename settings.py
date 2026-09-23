"""What an application tells the framework about itself.

Every value here is one the framework would otherwise have had to invent, and
inventing it is what makes a framework a copy of the application it grew out
of. Nothing in ``keepup`` reads ``config/modules.json`` for a product name or
assumes a page is called ``main.html``: it is told.

The callables are the other half of the same idea. The framework knows that a
secret must be hidden in the audit, that a password may be refused, that a
sign-in may be worth recording -- and it knows none of the rules, because they
belong to the application.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "KeepupSettings",
    "OidcSettings",
    "StaticMount",
]


@dataclass
class StaticMount:
    """One directory of the front end, served under one URL path."""

    path: str
    directory: str
    name: str


def _default_mounts() -> Sequence[StaticMount]:
    return (
        StaticMount("/static", "static", "static"),
        StaticMount("/js", "static/js", "static-js"),
        StaticMount("/css", "static/css", "static-css"),
        StaticMount("/images", "static/images", "static-images"),
        StaticMount("/modules/js", "static/modules/js", "modules-static-js"),
        StaticMount("/modules/css", "static/modules/css", "modules-static-css"),
        StaticMount("/modules/images", "static/modules/images", "modules-static-images"),
    )


@dataclass
class OidcSettings:
    """The external identity provider this application signs people in with.

    Only the issuer is named: the addresses of the authorisation, token and key
    endpoints are read from the provider's discovery document, so changing
    provider is one line and there is never half a configuration left over from
    the previous one.
    """

    issuer: str
    client_id: str
    client_secret: str
    #: Where the provider sends the person back; must match what is registered
    #: with the provider.
    redirect_uri: str
    scopes: Sequence[str] = ("openid", "profile", "email")
    #: The claim holding the person's roles at the provider. Which one it is
    #: differs by provider and by installation, so the application names it.
    roles_claim: str = "groups"
    #: Role for someone whose claims map to no privileged permission.
    default_role: str = "CLIENT"
    #: Where the browser lands after a successful sign-in.
    after_login_path: str = "/selfcare"
    #: Seconds to wait for the provider; it is on the sign-in path.
    timeout_seconds: float = 5.0


@dataclass
class KeepupSettings:
    """The application's half of the contract with the framework."""

    # --- identity -------------------------------------------------------
    title: str = "KeepUP"
    project_name: str = "keepup"
    docs_url: Optional[str] = "/api/docs"
    redoc_url: Optional[str] = "/api/redoc"

    # --- the outside edge -----------------------------------------------
    #: Origins allowed to read answers from a browser. Empty by default: a
    #: package cannot know whose pages should be able to read the application
    #: it is installed into, and "*" is a decision, not an absence of one. An
    #: application that serves a browser API from another origin says so here.
    cors_origins: Sequence[str] = ()
    #: Whether /metrics answers without credentials. Closed by default: the
    #: collection carries the load of the host and the names of the replicas,
    #: which is reconnaissance for whoever is choosing a moment.
    metrics_public: bool = False
    #: Response headers the framework adds. The panel is a page, so without
    #: X-Frame-Options it can be framed by anybody.
    security_headers: bool = True
    #: The largest body an upload route accepts, in bytes. None removes the
    #: limit, which is a thing an application may want and should have to say.
    max_upload_bytes: Optional[int] = 256 * 1024 * 1024

    # --- plugins --------------------------------------------------------
    #: The manager the application built; it knows where its plugins live.
    plugin_manager: Any = None
    plugins_dir: Optional[str] = None
    plugins_config_path: str = "config/modules.json"

    # --- front end ------------------------------------------------------
    #: Where the package serves its own panel shell from. The shell is data of
    #: the package, not of the deployment: an installed keepup has to be able
    #: to render its panel without the application shipping a copy of
    #: main_new.js. The application's own files keep their own mounts.
    shell_mount: str = "/keepup-static"
    static_dir: str = "static"
    static_mounts: Sequence[StaticMount] = field(default_factory=_default_mounts)
    client_page: str = "main.html"
    version_file: str = "version.json"
    favicon_file: str = "favicon.ico"
    #: Pages served only while a given plugin runs: {"page.html": "plugin_id"}.
    gated_pages: Dict[str, str] = field(default_factory=dict)

    #: The themes this application ships: (name, page file, brand, logo). The
    #: framework deliberately ships none -- a theme is the face of a product,
    #: and a default here would put somebody else's brand in the corner of
    #: every application built on it.
    built_in_themes: Sequence[tuple] = ()

    # --- logging and audit ----------------------------------------------
    remote_log_url: Optional[str] = None
    #: Credential the collector expects from this application. The framework
    #: carries none: without it nothing is shipped anywhere.
    remote_log_token: Optional[str] = None
    remote_log_flush_interval: Optional[int] = None
    remote_log_batch_size: Optional[int] = None
    #: Given a request or response body, returns it with secrets removed.
    audit_redaction: Optional[Callable] = None

    # --- schema ---------------------------------------------------------
    #: init_app_tables(cursor, types, db_config) -- the application's own DDL.
    app_tables: Optional[Callable] = None
    #: Runs before the framework opens its connection, for table modules of
    #: the application that open their own.
    extra_setup: Optional[Callable] = None

    # --- signing in through an external provider -------------------------
    #: The provider to sign people in with. Without it the sign-in routes are
    #: not registered at all: the possibility is off, not broken.
    oidc: Optional[OidcSettings] = None
    #: oidc_account_policy(claims) -> decision about somebody this application
    #: has never seen. Without it the framework refuses: switching the provider
    #: on must not turn into open registration for a product that did not ask
    #: for it. Ready-made decisions live in keepup.auth.oidc_policy.
    oidc_account_policy: Optional[Callable] = None

    # --- policies the framework asks about but does not hold -------------
    #: password_rule(password, username) -> problem text or None.
    password_rule: Optional[Callable] = None
    #: record_login(user) -- awaited after a successful sign-in.
    record_login: Optional[Callable] = None
    #: pending_documents(user) -> documents the user has yet to accept.
    pending_documents: Optional[Callable] = None
    #: declared_event_types() -> event types the application may emit.
    declared_event_types: Optional[Callable] = None
    #: public_config() -> what else the sign-in screen needs before anyone has
    #: signed in: the text of the terms, where a forgotten password is reset.
    #: The framework serves the endpoint and answers what it knows itself
    #: (whether self-registration is open); this fills in the rest. Awaited if
    #: it returns a coroutine.
    public_config: Optional[Callable] = None

    # --- lifecycle ------------------------------------------------------
    #: The application's channel for notifications between replicas. When set,
    #: the framework creates the bus (keepup/notification_bus.py) before
    #: on_startup and the plugins, and stops it after them; None means no bus.
    notification_channel: Optional[str] = None
    #: on_startup(app) -- awaited before the plugins are initialised, which is
    #: where a bus the plugins subscribe to has to be started.
    on_startup: Optional[Callable] = None
    #: on_shutdown(app) -- awaited after the plugins are cleaned up.
    on_shutdown: Optional[Callable] = None

    # --- deployment -----------------------------------------------------
    #: The stripped application: /metrics and a raw-request log, nothing else.
    disable_http_server: bool = False
