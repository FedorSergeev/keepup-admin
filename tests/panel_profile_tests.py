"""The panel profile in a real application: capabilities, services, declarations.

Task keepup-123's acceptance, and what keepup-124 has been waiting for. Until
now the catalogue ran only in checks; with the runtime raised inside `create_app`
and the deployment's dialect choosing the driver, a deployment that names the
`panel` profile brings its capabilities up: the abstraction publishes the data
source and every capability offers its declarations to be created.

This is the check that a declaration may leave `keepup.schema`: something in the
deployment now asks the capability for it.
"""

from fastapi.testclient import TestClient

from keepup import KeepupSettings, create_app


def panel_settings(**overrides):
    """An application whose catalogue is the framework's own panel profile."""
    values = dict(title="panel", static_mounts=(), plugin_manager=None, profile="panel")
    values.update(overrides)
    return KeepupSettings(**values)


def test_the_panel_profile_brings_the_capabilities_up():
    """The catalogue runs in the application, not only in the checks."""
    app = create_app(panel_settings())
    with TestClient(app):
        kernel = app.state.kernel
        assert kernel is not None and kernel.started is True
        assert kernel.services.has("datasource")
        assert kernel.services.has("audit")
        assert kernel.services.has("users")


def test_the_capabilities_offer_their_declarations_to_the_deployment():
    """What keepup-124 moves: a declaration reaches the abstraction from its owner."""
    app = create_app(panel_settings())
    with TestClient(app):
        kernel = app.state.kernel
        declared = {table.name for table in kernel.contributions.of("tables")}
    assert {"auth_session", "login_attempts", "users", "user_roles",
            "incoming_requests", "app_events", "system_metrics",
            "integration_logs"} <= declared


def test_the_driver_the_deployment_named_is_the_one_that_ran():
    """SQLite in a check, and the abstraction says so."""
    app = create_app(panel_settings())
    with TestClient(app):
        kernel = app.state.kernel
        assert kernel.services.require("datasource").dialect == "sqlite"
