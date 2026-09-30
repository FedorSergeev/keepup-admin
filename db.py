"""Access to the database: configuration and the pooled manager.

`DatabaseManagerV2` is the one way in -- a SQLAlchemy engine with a pool and a
session context manager, named parameters only. The legacy `DatabaseManager`,
a layer over raw drivers with positional parameters that opened a connection
per call, is gone (0.2.0); code written against a cursor takes one from the
pool with `DatabaseManagerV2.raw_connection()`.

Where the database is comes from the environment when `DB_TYPE` is set and from
`config/postgres.properties` otherwise. Both paths are read from the working
directory of the started application, which is how two applications on one
framework keep separate databases. SQLite exists for development only.
"""

import asyncio
import contextvars
import os
import threading
import warnings
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

def _reads_only(query: str) -> bool:
    """Whether the statement is a plain read: a SELECT that takes no lock."""
    body = query.lstrip().lstrip("(").lstrip()
    return body[:6].upper() == "SELECT" and "FOR UPDATE" not in query.upper() \
        and "FOR SHARE" not in query.upper()


#: The session of the shared_session() block running on this thread, if any,
#: with the thread it belongs to.
_SHARED_SESSION: contextvars.ContextVar = contextvars.ContextVar(
    "keepup_shared_session", default=None)

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "DatabaseConfig",
    "DatabaseManagerV2",
    "db_config",
]


class DatabaseConfig:
    """Database configuration."""

    def __init__(self):
        self.db_type = 'sqlite'
        self.db_host = 'localhost'
        self.db_port = '5432'
        self.db_name = 'keepup_app'
        self.db_user = 'postgres'
        self.db_password = ''
        self.db_path = 'data/keepup_app.db'
        # The defaults come first and the configuration over them: the other way
        # round, DB_POOL_SIZE and db.pool.size were read and then written over
        # with these very numbers, and never applied (keepup-38).
        self.pool_size = 5
        self.pool_max_overflow = 10
        self.pool_timeout = 30
        self.pool_recycle = 3600
        self._load_config()

    def apply_pool(self, size=None, max_overflow=None, timeout=None, recycle=None) -> bool:
        """Take the application's pool values over the deployment's; True if any changed."""
        changed = False
        for field_name, value in (("pool_size", size), ("pool_max_overflow", max_overflow),
                                  ("pool_timeout", timeout), ("pool_recycle", recycle)):
            if value is not None and getattr(self, field_name) != int(value):
                setattr(self, field_name, int(value))
                changed = True
        return changed

    def _load_config(self):
        self.db_type = os.getenv('DB_TYPE', '').lower()
        if not self.db_type:
            self._load_from_file()
        else:
            self._load_from_env()

    def _load_from_env(self):
        self.db_host = os.getenv('DB_HOST', 'localhost')
        self.db_port = os.getenv('DB_PORT', '5432')
        self.db_name = os.getenv('DB_NAME', 'keepup')
        self.db_user = os.getenv('DB_USER', 'postgres')
        self.db_password = os.getenv('DB_PASSWORD', '')
        self.db_path = os.getenv('DB_PATH', 'data/keepup.db')

        self.pool_size = int(os.getenv('DB_POOL_SIZE', '5'))
        self.pool_max_overflow = int(os.getenv('DB_POOL_MAX_OVERFLOW', '10'))
        self.pool_timeout = int(os.getenv('DB_POOL_TIMEOUT', '30'))
        self.pool_recycle = int(os.getenv('DB_POOL_RECYCLE', '3600'))

        if self.db_type == 'sqlite':
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    def _load_from_file(self):
        self.config_file = "config/postgres.properties"
        config = {}
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if '=' in line:
                            key, value = line.split('=', 1)
                            config[key.strip()] = value.strip()

        self.db_type = config.get('db.type', 'sqlite').lower()
        self.db_host = config.get('db.host', 'localhost')
        self.db_port = config.get('db.port', '5432')
        self.db_name = config.get('db.name', 'keepup')
        self.db_user = config.get('db.user', 'postgres')
        # The file names where the database is; the password never lives in it.
        self.db_password = os.getenv('DB_PASSWORD') or config.get('db.password', '')
        self.db_path = config.get('db.path', 'data/keepup.db')

        self.pool_size = int(config.get('db.pool.size', '5'))
        self.pool_max_overflow = int(config.get('db.pool.max_overflow', '10'))
        self.pool_timeout = int(config.get('db.pool.timeout', '30'))
        self.pool_recycle = int(config.get('db.pool.recycle', '3600'))

        if self.db_type == 'sqlite':
            db_dir = os.path.dirname(self.db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

    def get_connection_string(self) -> str:
        if self.db_type == 'postgres':
            # The driver is named, not left to SQLAlchemy: from 2.1 a bare
            # postgresql:// means psycopg 3, which this package does not
            # install, and every fresh installation failed to reach the
            # database at all (keepup-84). psycopg2 is the declared dependency.
            return (f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
                    f"@{self.db_host}:{self.db_port}/{self.db_name}")
        else:
            return f"sqlite:///{self.db_path}"

    def get_sqlalchemy_engine_params(self) -> dict:
        params = {}
        if self.is_postgres():
            params.update({
                'pool_size': self.pool_size,
                'max_overflow': self.pool_max_overflow,
                'pool_timeout': self.pool_timeout,
                'pool_recycle': self.pool_recycle,
                'pool_pre_ping': True,
                'echo': os.getenv('SQL_ECHO', 'false').lower() == 'true'
            })
        else:
            params.update({
                'pool_size': self.pool_size,
                'pool_timeout': self.pool_timeout,
            })
            params['connect_args'] = {'check_same_thread': False}
        return params

    def is_postgres(self) -> bool:
        return self.db_type == 'postgres'

    def is_sqlite(self) -> bool:
        return self.db_type == 'sqlite'

    def __str__(self):
        if self.is_postgres():
            return (f"DatabaseConfig(type={self.db_type}, host={self.db_host}, "
                    f"port={self.db_port}, db={self.db_name}, user={self.db_user}, "
                    f"pool_size={self.pool_size})")
        else:
            return f"DatabaseConfig(type={self.db_type}, path={self.db_path})"



class DatabaseManagerV2:
    """Database access through SQLAlchemy with a connection pool."""

    _engine = None
    _session_factory = None

    @classmethod
    def initialize(cls):
        """Initialise the SQLAlchemy engine."""
        if cls._engine is None:
            connection_string = db_config.get_connection_string()
            engine_params = db_config.get_sqlalchemy_engine_params()
            cls._engine = create_engine(connection_string, **engine_params)
            cls._session_factory = sessionmaker(bind=cls._engine)
            import logging
            logging.getLogger(__name__).info(f"DatabaseManagerV2 initialized with pool_size={db_config.pool_size}")

    @classmethod
    @contextmanager
    def get_session(cls) -> Session:
        """Context manager yielding a session, committing or rolling back on exit.

        Inside ``shared_session()`` on the same thread it yields that block's
        session instead, and leaves committing to the block.
        """
        shared = _SHARED_SESSION.get()
        if shared is not None and shared[0] == threading.get_ident():
            yield shared[1]
            return
        if cls._engine is None:
            cls.initialize()
        session = cls._session_factory()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    @classmethod
    @contextmanager
    def shared_session(cls):
        """Run every query of this block on one session: one connection, one commit.

        Each call of the query helpers takes a connection from the pool on its
        own -- a checkout, a ping, the query and a commit -- and a path that
        asks three questions paid that three times. The session check on every
        request did: it was half of what a replica spent (keepup-85). Nested
        blocks join the outer one. Bound to the thread that opened it, so work
        handed to another thread from inside the block takes a session of its
        own rather than sharing one across threads.
        """
        shared = _SHARED_SESSION.get()
        if shared is not None and shared[0] == threading.get_ident():
            yield shared[1]
            return
        with cls.get_session() as session:
            token = _SHARED_SESSION.set((threading.get_ident(), session))
            try:
                yield session
            finally:
                _SHARED_SESSION.reset(token)

    # --- the same calls, awaitable ------------------------------------------
    #
    # Every query here blocks its thread until the database answers, and in a
    # coroutine that thread is the event loop's: while it waits, the process
    # serves nobody. These run the synchronous call in a worker thread
    # (asyncio.to_thread) and await it, so async code never calls the database
    # on the loop. They call the synchronous methods by name, so whatever
    # replaces one of those (a test's stand-in, a recorder) is used here too.

    @classmethod
    async def execute_async(cls, query: str, params: Optional[dict] = None) -> List[Dict]:
        return await asyncio.to_thread(cls.execute, query, params)

    @classmethod
    async def execute_one_async(cls, query: str, params: Optional[dict] = None) -> Optional[Dict]:
        return await asyncio.to_thread(cls.execute_one, query, params)

    @classmethod
    async def execute_commit_async(cls, query: str, params: Optional[dict] = None) -> int:
        return await asyncio.to_thread(cls.execute_commit, query, params)

    @classmethod
    async def execute_many_async(cls, query: str, params_list: List[dict]) -> int:
        return await asyncio.to_thread(cls.execute_many, query, params_list)

    @classmethod
    async def execute_commit_returning_async(cls, query: str, params: Optional[dict] = None,
                                             returning: str = "id") -> Optional[Dict]:
        return await asyncio.to_thread(cls.execute_commit_returning, query, params, returning)

    @classmethod
    def test_connection(cls) -> Dict[str, Any]:
        """Whether the database answers, and which one it is; never raises."""
        try:
            if db_config.is_postgres():
                version = cls.execute_one("SELECT version() AS version")
                db_type = "PostgreSQL"
            else:
                version = cls.execute_one("SELECT sqlite_version() AS version")
                db_type = "SQLite"
            return {
                "success": True,
                "database_type": db_type,
                "version": (version or {}).get("version", "Unknown"),
                "config": str(db_config),
            }
        except Exception as e:
            return {"success": False, "error": str(e), "config": str(db_config)}

    @classmethod
    def raw_connection(cls):
        """A driver connection out of the pool, for code written against a cursor.

        The application's table hook and the first-start accounts take a
        cursor; the statements run on it are the driver's own (``?`` on
        SQLite, ``%s`` on PostgreSQL) and rows come back as the driver makes
        them. ``close()`` hands the connection back to the pool.
        """
        if cls._engine is None:
            cls.initialize()
        return cls._engine.raw_connection()

    @classmethod
    @contextmanager
    def _statement_scope(cls, query: str):
        """Where one statement of execute()/execute_one() runs.

        A plain read runs on a connection in autocommit: it wrote nothing, and
        the transaction around it cost a BEGIN and a COMMIT -- a round trip to
        the database for every read (keepup-87). Anything else, and anything
        inside shared_session(), keeps its transaction as before: these helpers
        are also called with INSERT ... RETURNING, and a SELECT ... FOR UPDATE
        means its lock.
        """
        shared = _SHARED_SESSION.get()
        if _reads_only(query) and not (shared is not None and shared[0] == threading.get_ident()):
            if cls._engine is None:
                cls.initialize()
            with cls._engine.connect() as connection:
                yield connection.execution_options(isolation_level="AUTOCOMMIT")
            return
        with cls.get_session() as session:
            yield session

    @classmethod
    def execute(cls, query: str, params: Optional[dict] = None) -> List[Dict]:
        """Run a SQL query.

        NOTE: takes NAMED parameters (:param_name), not positional ones.
        """
        with cls._statement_scope(query) as session:
            result = session.execute(text(query), params or {})
            if result.returns_rows:
                result = [dict(row._mapping) for row in result.fetchall()]
                return result
            return []

    @classmethod
    def execute_one(cls, query: str, params: Optional[dict] = None) -> Optional[Dict]:
        """Run a SQL query and return a single row.

        NOTE: takes NAMED parameters (:param_name), not positional ones.
        """
        with cls._statement_scope(query) as session:
            result = session.execute(text(query), params or {})
            if result.returns_rows:
                row = result.fetchone()
                return dict(row._mapping) if row else None
            return None

    @classmethod
    def execute_commit(cls, query: str, params: Optional[dict] = None) -> int:
        """Run a SQL query and commit.

        NOTE: takes NAMED parameters (:param_name), not positional ones.
        """
        with cls.get_session() as session:
            result = session.execute(text(query), params or {})
            return result.rowcount

    @classmethod
    def column_exists(cls, table: str, column: str) -> bool:
        """Whether the column is already on the table, asked of the database.

        Each dialect is asked the way it answers: PostgreSQL keeps a catalogue,
        SQLite answers a pragma. Neither reads an error message, which is the
        whole point -- see add_column_if_missing.
        """
        if db_config.is_postgres():
            found = cls.execute_one(
                "SELECT 1 AS present FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column",
                {"table": table, "column": column})
            return found is not None

        # PRAGMA takes no parameters, so the name is inlined -- and therefore
        # checked: see keepup.tables.identifier for why this is a refusal and
        # not a quoting.
        from keepup.tables import identifier

        rows = cls.execute(f"PRAGMA table_info({identifier(table)})")
        return any(str(row.get("name")) == column for row in rows or [])

    @classmethod
    def add_column_if_missing(cls, table: str, column: str,
                              definition: str) -> bool:
        """Add a column unless it is already there. True if this call added it.

        There are no migrations in this project: every start re-runs its DDL, so
        an ALTER that has already been applied has to be a no-op rather than an
        error. That used to be decided by matching the text of the failure
        against "duplicate column" -- which is what SQLite says and PostgreSQL
        does not. PostgreSQL answers `(psycopg2.errors.DuplicateColumn) column
        "x" of relation "y" already exists`: no such substring, so the exception
        was re-raised, the plugin failed to initialise, and every one of its
        routes answered 404 while the application itself reported healthy. On
        the stand that happened on the second start after any new column -- the
        first one added it, the next one fell over.

        A method rather than a function beside the class on purpose: the test
        harnesses swap this module and rebind the manager by name, so a helper
        of its own would quietly talk to a different database than its caller.
        """
        from keepup.tables import identifier

        if db_config.is_postgres():
            # PostgreSQL can say it in the statement, and then a repeat start
            # raises nothing at all -- no aborted transaction to recover from.
            cls.execute_commit(
                f"ALTER TABLE {identifier(table)} "
                f"ADD COLUMN IF NOT EXISTS {identifier(column)} {definition}")
            return True

        # SQLite has no such form: try, and ask the catalogue if it failed.
        try:
            cls.execute_commit(
                f"ALTER TABLE {identifier(table)} ADD COLUMN {identifier(column)} {definition}")
            return True
        except Exception:
            if cls.column_exists(table, column):
                return False
            raise

    @classmethod
    def execute_many(cls, query: str, params_list: List[dict]) -> int:
        """Run a SQL query for many parameter sets.

        NOTE: takes NAMED parameters (:param_name), not positional ones.
        """
        total = 0
        with cls.get_session() as session:
            for params in params_list:
                result = session.execute(text(query), params)
                total += result.rowcount
        return total

    @classmethod
    def get_pool_status(cls) -> Dict[str, Any]:
        """Return the connection pool status."""
        if cls._engine is None:
            cls.initialize()
        pool = cls._engine.pool
        return {
            'size': pool.size(),
            'checked_in': pool.checkedin(),
            'overflow': pool.overflow(),
            'total': pool.size() + pool.overflow(),  # total = size + overflow
            'checked_out': pool.checkedout(),
        }

    @classmethod
    def dispose(cls):
        """Close every connection."""
        if cls._engine:
            cls._engine.dispose()
            cls._engine = None
            cls._session_factory = None

    @classmethod
    def get_last_insert_rowid(cls, session=None) -> int:
        """Return the id of the last inserted row.

        Works on both PostgreSQL and SQLite -- but only with the session that
        made the insert. Without one it opens a new session, which may be
        another pooled connection, and answers another insert's id or 0
        (keepup-82); that form is deprecated. Use execute_commit_returning().
        """
        if session is None:
            warnings.warn(
                "get_last_insert_rowid() without the inserting session may answer "
                "another insert's id; use execute_commit_returning()",
                DeprecationWarning, stacklevel=2)
        if cls._engine is None:
            cls.initialize()

        if db_config.is_postgres():
            query = "SELECT lastval() as id"
            result = cls.execute_one(query)
            return result["id"] if result else 0
        else:
            if session:
                result = session.execute(text("SELECT last_insert_rowid() as id"))
                row = result.fetchone()
                return row[0] if row else 0
            else:
                with cls.get_session() as sess:
                    result = sess.execute(text("SELECT last_insert_rowid() as id"))
                    row = result.fetchone()
                    return row[0] if row else 0

    @classmethod
    def execute_commit_returning(cls, query: str, params: Optional[dict] = None, returning: str = "id") -> Optional[
        Dict]:
        """Run a SQL query and return the inserted record.

        Uses RETURNING on PostgreSQL and last_insert_rowid on SQLite.
        """
        if db_config.is_postgres():
            modified_query = f"{query} RETURNING {returning}"
            with cls.get_session() as session:
                result = session.execute(text(modified_query), params or {})
                row = result.fetchone()
                if row:
                    return dict(row._mapping)
                return None
        else:
            with cls.get_session() as session:
                session.execute(text(query), params or {})
                result = session.execute(text(f"SELECT last_insert_rowid() as {returning}"))
                row = result.fetchone()
                if row:
                    return {returning: row[0]}
                return None

    @classmethod
    def execute_commit_with_positional(cls, query: str, params: tuple) -> int:
        """Run a SQL query with POSITIONAL parameters (?) and commit.

        Deprecated: a leftover of the transition from the manager that took
        positional parameters, removed in 0.2.0 (keepup-55). Named parameters
        are what this manager takes; statements still written with `?` go
        through the application's own `positional()`. Removed in the next minor
        release, once the applications that still call it have moved.
        """
        warnings.warn(
            "DatabaseManagerV2.execute_commit_with_positional is deprecated; write "
            "the statement with named parameters", DeprecationWarning, stacklevel=2)
        with cls.get_session() as session:
            # SQLAlchemy only binds named parameters, so ? placeholders are rewritten to :paramN.
            if '?' in query:
                param_count = query.count('?')
                for i in range(param_count):
                    query = query.replace('?', f':param{i}', 1)

                named_params = {f'param{i}': params[i] for i in range(len(params))}
                result = session.execute(text(query), named_params)
            else:
                result = session.execute(text(query), params or {})
            return result.rowcount


db_config = DatabaseConfig()

# Log the resolved configuration on import, so a mis-pointed run is visible early.

if __name__ != "__main__":
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"Database configuration loaded: {db_config}")