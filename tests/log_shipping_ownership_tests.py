"""Shipping the application's log is a capability, and the local log is not (keepup-118).

`log_shipping.py` lived in the kernel, switched on by four fields of
`KeepupSettings`, so every deployment carried the queue, the retries and the
backoff of a collector most of them do not have. The capability owns the
shipping; the kernel keeps the console, the rotating file and the redaction
helper, because every deployment has a log.

    python3 -m pytest tests/log_shipping_ownership_tests.py -v
"""

import asyncio
import logging
import warnings
from pathlib import Path


from keepup import logging_setup
from keepup.kernel import contributions
from keepup_log_shipping.plugin import Log_shippingPlugin, SERVICE_LOG_SHIPPING

PACKAGE = Path(__file__).resolve().parents[1]


class Services:
    def __init__(self):
        self.published = {}

    def provide(self, name, instance=None, **options):
        self.published[name] = instance


class Manager:
    """A plugin manager with the settings a deployment carries."""

    def __init__(self, settings=None):
        self.settings = settings


class Settings:
    remote_log_url = "http://collector.invalid/logs"
    remote_log_token = "t"
    remote_log_flush_interval = 5
    remote_log_batch_size = 10


def test_the_kernel_keeps_the_local_log_and_nothing_of_the_shipping():
    """The collector's values, the queue and `requests` are the capability's."""
    assert "SHIPPING_VALUES" not in dir(logging_setup)
    source = (PACKAGE / "logging_setup.py").read_text(encoding="utf-8")
    assert "import requests" not in source
    assert "remote_url=_UNSET" not in source, "the kernel still carries the collector's values"
    # The shim that answered the old names went with 0.5.0 (keepup-122).
    assert "_MOVED_TO_SHIPPING" not in source
    assert "for_log" in logging_setup.__all__ and "setup_logging" in logging_setup.__all__


def test_logging_is_a_contribution_point():
    assert "logging" in contributions.CONTRIBUTION_KINDS
    assert contributions.GETTERS["logging"] == "get_log_handlers"


def test_without_a_collector_there_is_no_handler():
    """An unconfigured collector is not a queue that fills up."""
    plugin = Log_shippingPlugin({"remote_log_url": None})
    assert asyncio.run(plugin.initialize()) is True
    assert plugin.get_log_handlers() == []


def test_the_capability_builds_the_handler_from_its_own_settings(monkeypatch):
    built = {}

    def build(url, interval, size):
        built.update(url=url, interval=interval, size=size)
        return logging.NullHandler()

    monkeypatch.setattr("keepup_log_shipping.log_shipping.init_remote_logging", build)
    plugin = Log_shippingPlugin({"remote_log_url": "http://own.invalid/logs",
                                 "remote_log_flush_interval": 7,
                                 "remote_log_batch_size": 11})
    plugin.plugin_manager = Manager(Settings())
    assert asyncio.run(plugin.initialize()) is True
    assert built == {"url": "http://own.invalid/logs", "interval": 7, "size": 11}
    assert len(plugin.get_log_handlers()) == 1, "the handler is not contributed"


def test_the_old_settings_fields_still_work_and_say_they_moved(monkeypatch):
    """They go away in 0.6.0; until then a deployment that named them keeps shipping."""
    built = {}
    monkeypatch.setattr("keepup_log_shipping.log_shipping.init_remote_logging",
                        lambda url, interval, size: built.update(url=url) or logging.NullHandler())
    plugin = Log_shippingPlugin({})
    plugin.plugin_manager = Manager(Settings())

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert asyncio.run(plugin.initialize()) is True

    assert built["url"] == "http://collector.invalid/logs"
    assert any("KeepupSettings.remote_log_url" in str(w.message) for w in caught), (
        "reading the old field does not say it moved")


def test_the_application_attaches_what_the_capabilities_contribute():
    """The contribution is the contract; the root logger is the application's."""
    from keepup_http.factory import _attach_log_handlers, _detach_log_handlers

    contributions_object = contributions.Contributions()
    contributions_object.by_kind["logging"].append(
        {"plugin_id": "log_shipping", "values": [logging.NullHandler()]})

    class Kernel:
        pass

    kernel = Kernel()
    kernel.contributions = contributions_object
    attached = _attach_log_handlers(kernel)
    try:
        assert attached and attached[0] in logging.getLogger().handlers
    finally:
        _detach_log_handlers(attached)
    assert attached[0] not in logging.getLogger().handlers


def test_the_plugin_is_declared_where_the_catalogue_says():
    from keepup.plugins import registry

    descriptor = Log_shippingPlugin.descriptor
    assert descriptor.id == "log_shipping"
    assert descriptor.distribution == "keepup-log-shipping"
    assert ("log_shipping", 1) in descriptor.provides
    assert "logging" in descriptor.contributions
    assert SERVICE_LOG_SHIPPING == "log_shipping"
    assert callable(registry.get_plugins_status)
