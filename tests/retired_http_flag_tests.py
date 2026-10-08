"""The retired flag is the profile, not a second application factory.

Task keepup-123. `disable_http_server` asked for a stand that serves /metrics and
a raw-request log and nothing else. 0.4.0 expresses that as a profile, so the
same code and the same catalogue assemble every stand; the flag keeps working for
one release and says where it went.
"""

import warnings
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


def test_the_flag_becomes_the_profile():
    """A stand that serves no panel is the metrics-only profile."""
    settings = bare_settings(disable_http_server=True)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        create_app(settings)
    assert settings.profile == PROFILE_METRICS_ONLY
    assert any("metrics-only" in str(item.message) for item in seen)
    assert any(issubclass(item.category, DeprecationWarning) for item in seen)


def test_an_explicit_profile_wins():
    """A deployment that named a profile meant it."""
    settings = bare_settings(disable_http_server=True, profile="panel")
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        create_app(settings)
    assert settings.profile == "panel"


def test_the_profile_the_flag_names_exists_in_the_catalogue():
    """A flag that maps onto a profile nobody declared is a flag that breaks."""
    import json
    from pathlib import Path

    catalogue = json.loads(
        (Path(__file__).resolve().parents[1] / "plugins" / "builtin.json").read_text(
            encoding="utf-8"))
    assert PROFILE_METRICS_ONLY in catalogue["profiles"]


def test_without_the_flag_nothing_is_said():
    """The mapping is about a retired flag, not about every application."""
    settings = bare_settings()
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        create_app(settings)
    assert settings.profile is None
    assert [item for item in seen if "disable_http_server" in str(item.message)] == []


def test_the_flag_still_builds_the_stripped_application():
    """The behaviour it asked for is unchanged for one release."""
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        app = create_app(bare_settings(disable_http_server=True))
    paths = {route.path for route in app.routes}
    assert "/metrics" in paths
    assert "/api/admin/plugins" not in paths
