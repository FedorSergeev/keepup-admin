"""The pieces the framework runs on: locks, the audit buffer, logging, settings.

All of it against SQLite and with no network: the point of moving these out of
the entry point was that they can be exercised without starting a deployment.

    python3 -m pytest keepup/tests/runtime_tests.py -v
"""

import logging

import pytest

from keepup import audit, logging_setup
from keepup.db import DatabaseManager
from keepup.instance import get_instance_id, get_instance_name
from keepup.locks import DatabaseLock, distributed_lock, with_distributed_lock
from keepup.schema import init_db
from keepup.settings import KeepupSettings


@pytest.fixture(scope="module", autouse=True)
def tables():
    """The framework's own tables, in the session's throwaway database."""
    init_db()


@pytest.fixture(autouse=True)
def no_locks_left_over():
    DatabaseManager.execute_commit_only("DELETE FROM distributed_locks", ())
    yield
    DatabaseManager.execute_commit_only("DELETE FROM distributed_locks", ())


# --- who this replica is ------------------------------------------------------

def test_the_instance_id_names_the_host_and_the_process():
    first = get_instance_id()
    assert "-" in first
    assert first.rsplit("-", 1)[1].isdigit()
    assert first == get_instance_id()


def test_the_instance_name_prefers_what_the_deployment_set(monkeypatch):
    monkeypatch.setenv("INSTANCE_NAME", "chosen")
    assert get_instance_name() == "chosen"

    monkeypatch.delenv("INSTANCE_NAME")
    monkeypatch.setenv("HOSTNAME", "from-the-container")
    assert get_instance_name() == "from-the-container"


# --- serialising work across replicas ----------------------------------------

async def test_a_lock_is_taken_and_given_back():
    lock = DatabaseLock("one-job")

    assert await lock.acquire() is True
    held = DatabaseManager.execute_sql(
        "SELECT * FROM distributed_locks WHERE lock_name = ?", ("one-job",))
    assert len(held) == 1

    await lock.release()
    assert DatabaseManager.execute_sql(
        "SELECT * FROM distributed_locks WHERE lock_name = ?", ("one-job",)) == []


async def test_a_second_replica_does_not_get_the_same_lock():
    """The whole reason the lock exists: the job must run once, not per replica."""
    first = DatabaseLock("shared-job")
    assert await first.acquire() is True

    second = DatabaseLock("shared-job", timeout=1)
    assert await second.acquire() is False

    await first.release()


async def test_the_decorated_task_is_skipped_while_the_lock_is_held():
    """Skipped, not queued: the other replica is already doing it."""
    ran = []

    @with_distributed_lock("nightly")
    async def job():
        ran.append(True)
        return "done"

    assert await job() == "done"
    assert len(ran) == 1

    holder = DatabaseLock("task_nightly")
    assert await holder.acquire() is True
    try:
        assert await job() is None
        assert len(ran) == 1
    finally:
        await holder.release()


async def test_the_context_manager_releases_even_when_the_body_raises():
    with pytest.raises(ValueError):
        async with distributed_lock("fragile"):
            raise ValueError("the job failed")

    assert DatabaseManager.execute_sql(
        "SELECT * FROM distributed_locks WHERE lock_name = ?", ("fragile",)) == []


# --- the audit of incoming requests -------------------------------------------

@pytest.fixture
def empty_buffer():
    audit.incoming_requests_buffer.clear()
    yield audit.incoming_requests_buffer
    audit.incoming_requests_buffer.clear()


def test_without_a_redaction_the_framework_keeps_the_names_and_hides_the_values(monkeypatch):
    """It does not know which field carries a key, so it assumes one might.

    Until keepup-11 the default was the other way round, and an application
    that simply installed the package wrote one-time links, invitation tokens
    and API keys from query strings into the table in full.
    """
    monkeypatch.setattr(audit, "redact", audit.names_only)
    assert audit.redact({"api_key": "secret", "page": 2}) == {
        "api_key": audit.HIDDEN, "page": audit.HIDDEN}


def test_an_application_can_still_ask_for_everything_but_has_to_say_so(monkeypatch):
    """By name, not by an absence that looks like every other absence."""
    monkeypatch.setattr(audit, "redact", audit.names_only)
    audit.configure(redaction=audit.keep_as_is)
    try:
        assert audit.redact({"api_key": "secret"}) == {"api_key": "secret"}
    finally:
        audit.configure(redaction=audit.names_only)


def test_the_application_supplies_what_counts_as_a_secret(monkeypatch):
    monkeypatch.setattr(audit, "redact", audit.names_only)
    audit.configure(redaction=lambda value: "***")
    try:
        assert audit.redact({"api_key": "secret"}) == "***"
    finally:
        audit.configure(redaction=audit.names_only)


async def test_a_request_is_buffered_rather_than_written_per_call(empty_buffer):
    """A row per request would put the audit on the request path."""
    request_id = await audit.IncomingRequestLogger.start_request(
        instance_id="test-instance", method="GET", endpoint="/api/x",
        host="plugin_api", request_data={"username": "tester"})

    assert request_id in empty_buffer
    assert empty_buffer[request_id]["endpoint"] == "/api/x"

    await audit.IncomingRequestLogger.end_request(
        request_id, duration_ms=12, http_status=200, response_data={"ok": True})
    assert empty_buffer[request_id]["http_status"] == 200
    assert empty_buffer[request_id]["duration_ms"] == 12


# --- logging ------------------------------------------------------------------

def test_without_a_collector_nothing_is_shipped(monkeypatch):
    """A framework with no deployment has nowhere to send logs, and invents none."""
    monkeypatch.setattr(logging_setup, "REMOTE_LOG_URL", None)
    assert logging_setup.init_remote_logging() is None


def test_the_application_names_the_collector_and_itself():
    previous = (logging_setup.PROJECT_NAME, logging_setup.REMOTE_LOG_URL)
    try:
        logging_setup.configure(project_name="second-product",
                                remote_url="http://collector.invalid/logs")
        assert logging_setup.PROJECT_NAME == "second-product"
        assert logging_setup.REMOTE_LOG_URL == "http://collector.invalid/logs"
    finally:
        logging_setup.configure(project_name=previous[0], remote_url=previous[1])


def test_a_wrapper_without_an_address_ships_nothing(monkeypatch):
    """No default address, so nothing is shipped and nothing is invented.

    The wrapper used to fall back to the address of the machine the framework
    was written on. On any other machine that is a closed port, and shipping
    into it looks exactly like shipping.
    """
    monkeypatch.setattr(logging_setup, "REMOTE_LOG_URL", None)
    monkeypatch.setattr(logging_setup, "REMOTE_LOG_TOKEN", "any-token")

    posted = []
    monkeypatch.setattr(logging_setup.requests, "post",
                        lambda *a, **kw: posted.append(a) or None)

    wrapper = logging_setup.RemoteLoggerWrapper()
    assert wrapper.remote_url is None
    assert wrapper.flush_thread is None

    wrapper.emit(logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None))
    wrapper.flush()
    assert posted == []


def test_the_collector_credential_comes_from_the_application(monkeypatch):
    """The framework carries no token of its own."""
    monkeypatch.setattr(logging_setup, "REMOTE_LOG_URL", None)
    monkeypatch.setattr(logging_setup, "REMOTE_LOG_TOKEN", None)
    assert logging_setup.RemoteLoggerWrapper().token is None

    logging_setup.configure(remote_token="the-application-s-token")
    assert logging_setup.RemoteLoggerWrapper().token == "the-application-s-token"


def test_registering_with_the_collector_needs_an_address(monkeypatch):
    monkeypatch.setattr(logging_setup, "REMOTE_LOG_URL", None)
    posted = []
    monkeypatch.setattr(logging_setup.requests, "post",
                        lambda *a, **kw: posted.append(a) or None)

    assert logging_setup.create_logger_token("admin-token") is None
    assert posted == []


def test_the_application_says_what_it_is_called_and_what_it_is(monkeypatch):
    """Name and description are the application's, not the framework's.

    The framework used to register every deployment as "Main KeepUP Tracker
    application".
    """
    monkeypatch.setattr(logging_setup, "REMOTE_LOG_URL", "http://collector.invalid/logs")

    class Refused:
        status_code = 403
        text = "no"

    sent = {}

    def capture(url, json=None, headers=None, **kw):
        sent["url"] = url
        sent["json"] = json
        return Refused()

    monkeypatch.setattr(logging_setup.requests, "post", capture)

    logging_setup.create_logger_token("admin-token", app_name="second-product",
                                      description="A second application")

    assert sent["url"] == "http://collector.invalid/admin/applications/register"
    assert sent["json"]["name"] == "second-product"
    assert sent["json"]["description"] == "A second application"


def test_setting_up_logging_leaves_one_console_and_one_file_handler():
    """The root handlers are put back by hand afterwards.

    setup_logging() closes every handler the root logger had, and the one
    pytest captures output with is among them. Calling it a second time to
    "restore" the state closes the new ones too and leaves the rest of the
    session logging into a closed file -- which is how this test first made
    unrelated tests fail.
    """
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    try:
        logging_setup.setup_logging()
        kinds = [type(handler).__name__ for handler in root.handlers]
        assert "StreamHandler" in kinds
        assert "RotatingFileHandler" in kinds
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
        for handler in saved:
            root.addHandler(handler)
        root.setLevel(level)


# --- settings -----------------------------------------------------------------

def test_the_defaults_name_no_product():
    settings = KeepupSettings()

    assert settings.title == "KeepUP"
    assert settings.plugin_manager is None
    assert settings.gated_pages == {}
    assert settings.audit_redaction is None
    assert settings.app_tables is None


def test_every_field_can_be_overridden():
    settings = KeepupSettings(title="Other", project_name="other",
                              static_dir="front", client_page="start.html",
                              plugins_config_path="config/other.json")

    assert settings.title == "Other"
    assert settings.project_name == "other"
    assert settings.static_dir == "front"
    assert settings.client_page == "start.html"
    assert settings.plugins_config_path == "config/other.json"


def test_two_settings_do_not_share_their_mounts():
    """A mutable default would make one application's mounts the other's."""
    first = KeepupSettings()
    second = KeepupSettings()

    assert first.static_mounts is not second.static_mounts or first.static_mounts == second.static_mounts
    assert first.gated_pages is not second.gated_pages
