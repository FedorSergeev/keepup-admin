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


#: See keepup_audit.audit for why None cannot mean "leave it as it was": an
#: application with no collector must be able to say so.
_UNSET = object()


def configure(log_dir=_UNSET):
    """Supply the application's local logging values before the handlers are built.

    The collector's values -- its address, token, batching and the project name
    logs are filed under -- belong to the shipping capability
    (`keepup-log-shipping`), which is loaded only when there is something to
    ship to (keepup-118). This module keeps the framework's own logging.
    """
    global LOG_DIR
    if log_dir is not _UNSET and log_dir is not None:
        LOG_DIR = log_dir


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
