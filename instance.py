"""Identity of a single running replica.

The application runs as several replicas against one database, so almost
everything written down -- a lock, a metric, an event -- has to say which
replica wrote it.
"""

import os
import socket

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "get_instance_id",
    "get_instance_name",
]

DEFAULT_INSTANCE_NAME = "default-instance"


def get_instance_id() -> str:
    """Return a unique identifier for this instance."""
    return f"{socket.gethostname()}-{os.getpid()}"


def get_instance_name() -> str:
    """Return the instance name used in logs and events.

    Precedence:
    1. INSTANCE_NAME environment variable
    2. HOSTNAME environment variable
    3. The system host name
    4. "default-instance"
    """
    instance_name = os.environ.get('INSTANCE_NAME')
    if instance_name:
        return instance_name

    hostname = os.environ.get('HOSTNAME')
    if hostname:
        return hostname

    try:
        return socket.gethostname()
    except Exception:
        return DEFAULT_INSTANCE_NAME
