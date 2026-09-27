"""Performance settings in one place, and the ones that silently did nothing (keepup-38).

What is promised: the database pool takes the deployment's values instead of
writing over them, and the application's values over both -- even after the pool
was created; the audit buffer and the metrics interval come from the same
place; and a replica writes its metrics under its own name.

    python3 -m pytest keepup/tests/performance_settings_tests.py -v
"""

import pytest
from keepup import audit, db, factory, metrics
from keepup.settings import KeepupSettings, PerformanceSettings


@pytest.fixture
def pool(monkeypatch):
    """The process's pool settings, put back as they were after the test."""
    saved = {name: getattr(db.db_config, name)
             for name in ("pool_size", "pool_max_overflow", "pool_timeout", "pool_recycle")}
    yield db.db_config
    for name, value in saved.items():
        setattr(db.db_config, name, value)


# --- the pool --------------------------------------------------------------------

def test_the_deployments_pool_values_are_applied_not_written_over(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_TYPE", "sqlite")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("DB_POOL_SIZE", "12")
    monkeypatch.setenv("DB_POOL_MAX_OVERFLOW", "3")
    config = db.DatabaseConfig()
    assert (config.pool_size, config.pool_max_overflow) == (12, 3)


def test_the_properties_file_is_applied_as_well(monkeypatch, tmp_path):
    monkeypatch.delenv("DB_TYPE", raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "postgres.properties").write_text(
        "db.type=sqlite\ndb.path=" + str(tmp_path / "y.db") + "\ndb.pool.size=7\n")
    monkeypatch.chdir(tmp_path)
    assert db.DatabaseConfig().pool_size == 7


def test_nothing_configured_keeps_the_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_TYPE", "sqlite")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "z.db"))
    for name in ("DB_POOL_SIZE", "DB_POOL_MAX_OVERFLOW", "DB_POOL_TIMEOUT", "DB_POOL_RECYCLE"):
        monkeypatch.delenv(name, raising=False)
    config = db.DatabaseConfig()
    assert (config.pool_size, config.pool_max_overflow, config.pool_timeout,
            config.pool_recycle) == (5, 10, 30, 3600)


def test_the_applications_pool_wins_and_an_existing_engine_is_rebuilt(pool, monkeypatch):
    disposed = []
    monkeypatch.setattr(db.DatabaseManagerV2, "dispose", classmethod(lambda cls: disposed.append(1)))

    factory.apply_performance(PerformanceSettings(db_pool_size=pool.pool_size + 15,
                                                  db_pool_timeout=9))

    assert pool.pool_timeout == 9
    assert disposed == [1], "the pool created before create_app must not live on"


def test_settings_that_change_nothing_leave_the_engine_alone(pool, monkeypatch):
    disposed = []
    monkeypatch.setattr(db.DatabaseManagerV2, "dispose", classmethod(lambda cls: disposed.append(1)))

    factory.apply_performance(PerformanceSettings())
    factory.apply_performance(PerformanceSettings(db_pool_size=pool.pool_size))

    assert disposed == []


# --- audit and metrics ---------------------------------------------------------------

def test_the_audit_buffer_and_the_metrics_interval_come_from_the_same_place(pool, monkeypatch):
    interval, size = audit.BUFFER_FLUSH_INTERVAL, audit.BUFFER_MAX_SIZE
    collection = metrics.metrics_collector.collection_interval
    monkeypatch.setattr(factory.config_service, "register_built_in_themes", lambda themes: None)
    monkeypatch.setattr(factory.config_service, "initialize", lambda: None)
    try:
        factory.apply_settings(KeepupSettings(performance=PerformanceSettings(
            audit_flush_interval=11, audit_buffer_size=7, metrics_interval=42)))
        assert (audit.BUFFER_FLUSH_INTERVAL, audit.BUFFER_MAX_SIZE) == (11, 7)
        assert metrics.metrics_collector.collection_interval == 42
    finally:
        audit.BUFFER_FLUSH_INTERVAL, audit.BUFFER_MAX_SIZE = interval, size
        metrics.metrics_collector.collection_interval = collection


def test_a_replica_writes_its_metrics_under_its_own_name(monkeypatch):
    from keepup.instance import get_instance_name
    monkeypatch.delenv("APP_INSTANCE", raising=False)
    assert metrics.SystemMetricsCollector().app_instance == get_instance_name() != "main"
    monkeypatch.setenv("APP_INSTANCE", "replica-2")
    assert metrics.SystemMetricsCollector().app_instance == "replica-2"
