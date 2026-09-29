"""Local logging: the console and rotated files.

Shipping logs to an external collector is `keepup/log_shipping.py` (keepup-24):
it brings a queue, a background thread, retries, a back-off after failures and
registration with the collector -- none of which somebody who only needs a file
with rotation should have to load. ``configure()`` stays the one place an
application supplies its logging values; the collector's are handed on to the
shipping module, which is imported only when there is a collector.
"""

import logging
import logging.handlers
import os
import sys
from datetime import datetime


#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "configure",
    "for_log",
    "setup_logging",
]

logger = logging.getLogger(__name__)

#: How much of a value from outside is written into one log line.
FOR_LOG_LIMIT = 500


def for_log(value, limit: int = FOR_LOG_LIMIT) -> str:
    """A value from outside, fit to be written into one log line.

    Control characters are written as escapes: a request path is decoded before
    anything sees it, so ``%0a`` in an address became a line break in the log
    and the rest of the address a line of its own -- whatever the sender wanted
    the log to say (keepup-76). Long values are cut, with the cut marked.
    """
    text = "" if value is None else str(value)
    escaped = "".join(
        ch if ch.isprintable() or ch == " " else ch.encode("unicode_escape").decode("ascii")
        for ch in text)
    if len(escaped) > limit:
        return escaped[:limit] + f"...[{len(escaped) - limit} more]"
    return escaped


#: Where the local log files go; set by the application through configure().
LOG_DIR = "logs"


#: The collector's values as the application gave them to configure(), for
#: keepup/log_shipping.py to take when it is loaded.
SHIPPING_VALUES = {}

#: See keepup.audit for why None cannot mean "leave it as it was": an
#: application with no collector must be able to say so.
_UNSET = object()


def configure(project_name=_UNSET, remote_url=_UNSET, flush_interval=_UNSET,
              batch_size=_UNSET, log_dir=_UNSET, remote_token=_UNSET):
    """Supply the application's logging values before the handlers are built.

    The directory is this module's; the collector's values -- its address,
    token, batching and the project name logs are filed under -- belong to the
    shipping module (keepup/log_shipping.py), which is loaded only when there
    is something to tell it: an application with no collector never loads it.
    """
    global LOG_DIR
    if log_dir is not _UNSET and log_dir is not None:
        LOG_DIR = log_dir
    remote = {"project_name": project_name, "remote_url": remote_url,
              "flush_interval": flush_interval, "batch_size": batch_size,
              "remote_token": remote_token}
    given = {key: value for key, value in remote.items() if value is not _UNSET}
    # Kept here and taken by the shipping module when it is first imported;
    # handed on at once when it already is.
    SHIPPING_VALUES.update(given)
    shipping = sys.modules.get("keepup.log_shipping")
    if shipping is not None:
        shipping.configure(**given)


def setup_logging():
    """Configure logging with file rotation."""

    formatter = logging.Formatter(
        fmt='%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # The directory the application named, not /tmp. LOG_DIR was declared and
    # then used nowhere, so the log went to a world-readable path with a
    # predictable name: on a host with more than one user, or in a container
    # somebody can get a shell in, that is the whole log of the application
    # (task keepup-13).
    os.makedirs(LOG_DIR, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join(
            LOG_DIR, f"app_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
        encoding='utf-8',
        maxBytes=10 * 1024 * 1024,
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


#: Names that moved to keepup/log_shipping.py (keepup-24). Still answered here
#: for code written against 0.1, with a warning naming the new place; looked up
#: on access, so local logging does not load the shipping.
_MOVED_TO_SHIPPING = {
    "RemoteLoggerWrapper", "AsyncRemoteLogHandler", "RemoteLogHandler",
    "setup_remote_logging", "init_remote_logging", "create_logger_token",
    "PROJECT_NAME", "REMOTE_LOG_URL", "REMOTE_LOG_TOKEN", "REMOTE_FLUSH_INTERVAL",
    "REMOTE_BATCH_SIZE",
}


def __getattr__(name):
    if name in _MOVED_TO_SHIPPING:
        import warnings
        from keepup import log_shipping
        warnings.warn(f"keepup.logging_setup.{name} moved to keepup.log_shipping.{name}",
                      DeprecationWarning, stacklevel=2)
        return getattr(log_shipping, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
