"""Shipping this application's own log to a collector (keepup-118).

This distribution provides the `log_shipping` capability and declares one
plugin, `log_shipping`, in the `keepup.plugins` group. It depends on
`keepup-admin` for the constructor and on `requests`, which is what carries the
batches; the framework's own logging -- the console, the rotating file, the
redaction helper -- stays in the kernel, because every deployment has a log and
not every one has a collector.
"""

__version__ = "0.5.0"

__all__ = ["Log_shippingPlugin", "SERVICE_LOG_SHIPPING", "create_logger_token",
           "init_remote_logging", "setup_remote_logging"]

#: Both are answered on access and never on import: the kernel imports this
#: package to read the plugin's descriptor, and pulling `requests` in for that
#: would make every deployment pay for a collector it may not have (keepup-118).
_FROM_SHIPPING = {"create_logger_token", "init_remote_logging", "setup_remote_logging"}
_FROM_PLUGIN = {"Log_shippingPlugin", "SERVICE_LOG_SHIPPING"}


def __getattr__(name):
    if name in _FROM_PLUGIN:
        from keepup_log_shipping import plugin

        return getattr(plugin, name)
    if name in _FROM_SHIPPING:
        from keepup_log_shipping import log_shipping

        return getattr(log_shipping, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
