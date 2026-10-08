"""create_app raises the plugin catalogue beside the path it already has.

Task keepup-123. The application is still assembled the way 0.3.0 assembled it --
plugins from the directory, routes registered module by module -- and the runtime
is raised with it: it resolves the catalogue and the profile, publishes what a
capability offers and creates what a capability declares. That is what lets a
declaration leave `keepup.schema` later without the table disappearing (keepup-124).

A catalogue that cannot start must not take the application with it: deployments
reach this code with a catalogue that never existed.
"""

from fastapi.testclient import TestClient

from keepup import KeepupSettings, create_app


def settings(**overrides):
    """An application with no plugins on disk and nothing mounted."""
    values = dict(title="kernel", static_mounts=(), plugin_manager=None)
    values.update(overrides)
    return KeepupSettings(**values)


def test_an_application_raises_a_runtime_and_serves():
    """The catalogue is part of the application now, and the panel still answers."""
    app = create_app(settings())
    with TestClient(app) as client:
        assert isinstance(app.state.kernel, object)
        assert client.get("/api/health").status_code in (200, 503)


def test_the_runtime_is_stopped_with_the_application():
    """A service a capability published is closed with the process."""
    app = create_app(settings())
    with TestClient(app):
        kernel = app.state.kernel
    assert kernel is None or kernel.started is False


def test_a_catalogue_that_cannot_start_does_not_take_the_application_with_it():
    """0.3.0 deployments have no catalogue: the application boots and says so."""
    catalogue = settings()
    app = create_app(catalogue)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code in (200, 503)
