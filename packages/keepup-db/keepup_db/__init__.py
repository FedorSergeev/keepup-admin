"""The abstraction that holds SQLAlchemy: the table language, the pool, the dialect registry and the creation of declared tables.

This distribution provides `datasource` and declares one plugin,
`db`, in the `keepup.plugins` group. It depends on `keepup-admin`
for the constructor -- the descriptor, the catalogue, the service registry and
the lifecycle -- and on nothing of another capability.

`keepup.db` moved here in keepup-124, and the old name keeps working for one
release: `keepup.compat` answers for it and names this distribution when it is not
installed. The three names below are the declaration the moved module carried.
"""

__version__ = "0.4.0"

from keepup_db.manager import (  # noqa: E402 - after the version, as a public face
    DatabaseConfig,
    DatabaseManagerV2,
    db_config,
)

__all__ = ["DatabaseConfig", "DatabaseManagerV2", "db_config"]
