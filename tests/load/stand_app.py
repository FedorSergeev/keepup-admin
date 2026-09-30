"""The application the load stand runs: the framework and nothing of an application.

What is measured is keepup itself -- sign-in, the session check on every request,
the section catalogue, the event log, the audit of every request, the pools --
so nothing is added but one route that reports how full the two pools are, which
is what the driver samples while it runs. Started by uvicorn in each replica of
`compose.yaml`; `python stand_app.py init` creates the schema once, before the
replicas start, so they do not race each other to create it.
"""

import os
import sys

from fastapi import Depends

from keepup.auth.dependencies import get_current_admin
from keepup.db import DatabaseManagerV2
from keepup.factory import create_app
from keepup.instance import get_instance_id
from keepup.plugins.base import PluginManager
from keepup.settings import KeepupSettings

#: No plugins at all. An empty manager rather than none: without one the
#: framework does not register its sign-in routes, and the stand has to sign in.
PLUGINS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
os.makedirs(PLUGINS_DIR, exist_ok=True)

settings = KeepupSettings(
    title="keepup load stand",
    static_mounts=(),
    plugin_manager=PluginManager(PLUGINS_DIR),
    plugins_dir=PLUGINS_DIR,
    notification_channel=os.getenv("LOAD_NOTIFICATION_CHANNEL") or None,
)

app = create_app(settings)


@app.get("/loadtest/pools")
async def pools(admin: dict = Depends(get_current_admin)):
    """How full the database pool and the worker-thread pool of this replica are."""
    from anyio import to_thread
    threads = to_thread.current_default_thread_limiter().statistics()
    return {
        "instance": get_instance_id(),
        "database": DatabaseManagerV2.get_pool_status(),
        "threads": {"borrowed": threads.borrowed_tokens, "total": threads.total_tokens,
                    "waiting": threads.tasks_waiting},
    }


if __name__ == "__main__" and sys.argv[1:] == ["init"]:
    from keepup.schema import init_db
    init_db()
    print("schema ready")
