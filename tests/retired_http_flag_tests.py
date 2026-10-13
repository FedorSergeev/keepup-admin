"""The retired flag is gone; the metrics-only profile is the way (keepup-122).

`disable_http_server` asked for a stand that serves /metrics and a raw-request
log and nothing else. 0.4.0 expressed that as a profile and kept the flag
working for one release, with a warning; 0.5.0 removes the flag, the warning and
the second application factory it built -- the same code and the same catalogue
assemble every stand.

    python3 -m pytest tests/retired_http_flag_tests.py -v
"""

import json
from pathlib import Path

from keepup import KeepupSettings, create_app
from keepup.kernel.lifecycle import PROFILE_METRICS_ONLY

PACKAGE = Path(__file__).resolve().parents[1]


def bare_settings(**overrides):
    """Settings that build nothing but the framework's own application."""
    values = dict(title="probe", project_name="probe", plugins_dir="",
                  static_mounts=(), plugin_manager=None)
    values.update(overrides)
    return KeepupSettings(**values)


def test_the_flag_is_gone():
    """A setting that no longer exists is not quietly ignored."""
    import dataclasses

    names = {field.name for field in dataclasses.fields(KeepupSettings)}
    assert "disable_http_server" not in names
    try:
        KeepupSettings(disable_http_server=True)
    except TypeError:
        pass
    else:
        raise AssertionError("the removed flag is still accepted")
    assert not (PACKAGE / "factory.py").exists(), "the base still assembles an application"
    source = (PACKAGE / "packages" / "keepup-http" / "keepup_http" / "factory.py").read_text(encoding="utf-8")
    assert "disable_http_server" not in source
    assert "_create_stripped_app" not in source
    assert "prometheus_client" not in source, (
        "the framework still carries the collector library of the metrics capability")


def test_the_profile_is_the_way_to_a_metrics_only_stand():
    """The stand the flag asked for comes from the same code and the same catalogue.

    The profile chooses the capabilities: the metrics one is up and the panel's
    are not. Routes are a separate matter until the transport mounts what the
    plugins declare (keepup-121).
    """
    from fastapi.testclient import TestClient
    from keepup_metrics.plugin import SERVICE_METRICS

    app = create_app(bare_settings(profile=PROFILE_METRICS_ONLY))
    assert "/metrics" in {route.path for route in app.routes}
    with TestClient(app):
        kernel = app.state.kernel
        assert kernel.services.has(SERVICE_METRICS), "the stand's own capability is not up"
        assert not kernel.services.has("catalogue"), "the panel came up in a metrics-only stand"


def test_the_profile_the_flag_named_exists_in_the_catalogue():
    """A profile nobody declared is a profile that breaks."""
    catalogue = json.loads(
        (PACKAGE / "plugins" / "builtin.json").read_text(encoding="utf-8"))
    assert PROFILE_METRICS_ONLY in catalogue["profiles"]


def test_without_a_profile_nothing_is_stripped():
    """The stand is chosen by the profile, and by nothing else."""
    settings = bare_settings()
    app = create_app(settings)
    assert settings.profile is None
    assert "/api/health" in {route.path for route in app.routes}
