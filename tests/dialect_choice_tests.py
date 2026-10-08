"""One database is configured, so one driver runs (keepup-123).

The framework ships two drivers of one required service. Which of them runs is a
deployment fact -- `DB_TYPE`, or `KeepupSettings.database_dialect` -- and not a
consequence of what happens to be installed: both enabled stops the start on
purpose (keepup-107), so the choice has to reach the kernel as data. The kernel
learns a dialect string, never a database library.
"""

import asyncio

from keepup.kernel import create_runtime


def settings(dialect):
    """Settings that name a dialect, and nothing else."""
    return type("Settings", (), {"database_dialect": dialect})()


def runtime(dialect):
    """The framework's own plugins, with both drivers declared enabled."""
    catalogue = {"plugins": [{"id": "db", "enabled": True},
                             {"id": "postgres", "enabled": True, "dialect": "postgresql"},
                             {"id": "sqlite", "enabled": True, "dialect": "sqlite"}]}
    return create_runtime(settings=settings(dialect), application_catalogue=catalogue)


def test_the_named_dialect_is_the_driver_that_runs():
    """Both offered, one running: the deployment's answer decides."""
    instance = runtime("sqlite")
    asyncio.run(instance.start())
    assert instance.states["sqlite"].enabled is True
    assert instance.states["postgres"].enabled is False
    assert instance.services.require("datasource").dialect == "sqlite"


def test_the_other_answer_gives_the_other_driver():
    """Nothing else changes: the same code, one setting."""
    instance = runtime("postgresql")
    asyncio.run(instance.start())
    assert instance.states["postgres"].enabled is True
    assert instance.states["sqlite"].enabled is False
    assert instance.services.require("datasource").dialect == "postgresql"


def test_without_an_answer_the_catalogue_decides_as_before():
    """A deployment that names no dialect keeps the rule it had."""
    catalogue = {"plugins": [{"id": "db", "enabled": True},
                             {"id": "postgres", "enabled": True, "dialect": "postgresql"},
                             {"id": "sqlite", "enabled": True, "dialect": "sqlite"}]}
    instance = create_runtime(application_catalogue=catalogue)
    try:
        asyncio.run(instance.start())
    except Exception as error:  # noqa: BLE001 - the duplicate rule is the point
        assert "datasource_driver" in str(error)
    else:
        raise AssertionError("both drivers ran with no dialect named")


def test_the_framework_catalogue_names_a_dialect_for_each_driver():
    """The choice is data in the catalogue, not knowledge in the kernel."""
    import json
    from pathlib import Path

    builtin = json.loads((Path(__file__).resolve().parents[1] / "plugins" / "builtin.json")
                         .read_text(encoding="utf-8"))
    dialects = {entry["id"]: entry.get("dialect") for entry in builtin["plugins"]
                if entry["id"] in ("postgres", "sqlite")}
    assert dialects == {"postgres": "postgresql", "sqlite": "sqlite"}
