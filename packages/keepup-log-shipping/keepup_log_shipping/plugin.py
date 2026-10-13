"""The application's log, sent to a collector that wants it (keepup-118).

`log_shipping.py` lived in the kernel and was switched on by four fields of
`KeepupSettings`, so every deployment carried the queue, the retries and the
backoff of a collector most of them do not have; `logging_setup.py` kept the
values aside for it and handed them on.

The capability owns the shipping now, and the kernel keeps the local log:

* its settings are its own -- the address, the credential, the flush interval
  and the batch size are the plugin's `config` (the four settings fields are
  still read, with a warning, so a deployment that named them keeps shipping
  until keepup-122 removes them);
* it contributes its handler through the `logging` contribution point, and the
  application attaches what the running plugins contribute -- a deployment that
  never installed this distribution attaches nothing and ships nowhere;
* without an address there is no handler at all: an unconfigured collector is
  not a queue that fills up.
"""

import logging
from typing import Any, Dict, List

from keepup.kernel.descriptor import KIND_OPTIONAL, PluginDescriptor
from keepup.plugins.base import BasePlugin

logger = logging.getLogger(__name__)

__all__ = ["Log_shippingPlugin", "SERVICE_LOG_SHIPPING"]

#: What a plugin asks for to ship a batch of its own, if it ever needs to.
SERVICE_LOG_SHIPPING = "log_shipping"

#: The settings fields the kernel used to keep for this capability. Read once,
#: with a warning, until keepup-122 takes them away.
_DEPRECATED_FIELDS = ("remote_log_url", "remote_log_token",
                      "remote_log_flush_interval", "remote_log_batch_size")


class Log_shippingPlugin(BasePlugin):
    """The application's log, queued and shipped to the collector it names."""

    descriptor = PluginDescriptor(
        id="log_shipping",
        name="Log shipping",
        version="0.4.0",
        distribution="keepup-log-shipping",
        kind=KIND_OPTIONAL,
        priority=90,
        provides=(f"{SERVICE_LOG_SHIPPING}>=1",),
        contributions=("logging",),
    )

    def __init__(self, config=None):
        super().__init__("log_shipping", "Log shipping", config)
        self.handler = None

    def _setting(self, name: str) -> Any:
        """One value: the plugin's own config first, the old kernel field second."""
        if name in self.config:
            return self.config[name]
        settings = getattr(self.plugin_manager, "settings", None)
        value = getattr(settings, name, None)
        if value is not None:
            import warnings

            warnings.warn(
                f"KeepupSettings.{name} moved to the log_shipping plugin's own setting "
                f"({name}) and goes away in 0.6.0",
                DeprecationWarning, stacklevel=3)
        return value

    async def initialize(self):
        """Build the handler when the deployment named a collector, and no earlier.

        Imported here and not at the top of the module: the kernel imports this
        module to read the descriptor, and `requests` is only needed by a
        deployment that actually ships somewhere (keepup-118).
        """
        url = self._setting("remote_log_url")
        if not url:
            logger.info("No collector named: this deployment ships its log nowhere")
            return True
        from keepup_log_shipping.log_shipping import init_remote_logging

        self.handler = init_remote_logging(
            url,
            self._setting("remote_log_flush_interval") or 120,
            self._setting("remote_log_batch_size") or 100_000)
        return True

    async def cleanup(self):
        """Ship what the queue still holds, and stop shipping."""
        handler, self.handler = self.handler, None
        if handler is None:
            return
        try:
            handler.wrapper.force_flush()
        except Exception as error:  # noqa: BLE001 - shutdown must not raise
            logger.warning("Could not flush the shipped log: %s", error)

    def get_api_routes(self) -> List[Dict[str, Any]]:
        """No routes: the capability ships what the application logs, and answers none."""
        return []

    def get_log_handlers(self) -> List[Any]:
        """What the application attaches to the root logger, if there is a collector."""
        return [self.handler] if self.handler is not None else []

    def get_handlers(self) -> Dict[str, Any]:
        return {"handler": self.handler}
