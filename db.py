"""Access to the database: configuration and two managers.

`DatabaseManagerV2` is the one to use -- a SQLAlchemy engine with a pool and a
session context manager, named parameters only. `DatabaseManager` is the legacy
layer over raw drivers with positional parameters, kept because the plugins
that still use it are converted one at a time, not because two ways of reaching
the database are wanted.

Where the database is comes from the environment when `DB_TYPE` is set and from
`config/postgres.properties` otherwise. Both paths are read from the working
directory of the started application, which is how two applications on one
framework keep separate databases. SQLite exists for development only.
"""

import os
import sqlite3
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

import psycopg2
from psycopg2.extras import RealDictCursor
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "DatabaseConfig",
    "DatabaseManager",
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
        self._load_config()
        self.pool_size = 5
        self.pool_max_overflow = 10
        self.pool_timeout = 30
        self.pool_recycle = 3600

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
            return f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"
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


db_config = DatabaseConfig()


class DatabaseManagerV2:
    """Database access through SQLAlchemy with a connection pool.

    Preferred over the legacy DatabaseManager below.
    """

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
        """Context manager yielding a session, committing or rolling back on exit."""
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
    def execute(cls, query: str, params: Optional[dict] = None) -> List[Dict]:
        """Run a SQL query.

        NOTE: takes NAMED parameters (:param_name), not positional ones.
        """
        with cls.get_session() as session:
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
        with cls.get_session() as session:
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

        Works on both PostgreSQL and SQLite.
        """
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
        """Run a SQL query with POSITIONAL parameters (?) and commit."""
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


class DatabaseManager:
    """Legacy database manager covering both SQLite and PostgreSQL."""

    @staticmethod
    def initialize(config: Optional[DatabaseConfig] = None):
        """Initialise the manager (kept for backwards compatibility)."""
        global db_config
        if config:
            db_config = config

    @staticmethod
    def get_connection():
        """Return a database connection."""
        if db_config.is_postgres():
            return DatabaseManager._get_postgres_connection()
        else:
            return DatabaseManager._get_sqlite_connection()

    @staticmethod
    def _get_sqlite_connection():
        """Return a SQLite connection."""
        conn = sqlite3.connect(db_config.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        return conn

    @staticmethod
    def _get_postgres_connection():
        """Return a PostgreSQL connection."""
        try:
            conn = psycopg2.connect(
                host=db_config.db_host,
                port=int(db_config.db_port),
                database=db_config.db_name,
                user=db_config.db_user,
                password=db_config.db_password,
                cursor_factory=RealDictCursor,
                connect_timeout=10
            )
            conn.autocommit = False
            return conn
        except psycopg2.Error as e:
            raise ConnectionError(f"Failed to connect to PostgreSQL: {str(e)}")

    @staticmethod
    def _adapt_query(query: str, params: tuple = None):
        """Adapt a query and its parameters to the configured engine."""
        if db_config.is_postgres():
            adapted_query = query.replace('?', '%s')
            return adapted_query, params
        else:
            return query, params

    @staticmethod
    def execute_query(query: str, params: tuple = None, fetch_one: bool = False):
        """Run a SQL query."""
        conn = DatabaseManager.get_connection()
        cursor = conn.cursor()

        try:
            adapted_query, adapted_params = DatabaseManager._adapt_query(query, params)
            cursor.execute(adapted_query, adapted_params or ())

            if fetch_one:
                result = cursor.fetchone()
            else:
                result = cursor.fetchall()

            conn.commit()

            if result is None:
                return None

            if fetch_one:
                return DatabaseManager._row_to_dict(result)
            else:
                return [DatabaseManager._row_to_dict(row) for row in result]

        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def execute_commit(query: str, params: tuple = None):
        """Run a query, commit, and return lastrowid."""
        conn = DatabaseManager.get_connection()
        cursor = conn.cursor()

        try:
            adapted_query, adapted_params = DatabaseManager._adapt_query(query, params)

            cursor.execute(adapted_query, adapted_params or ())
            conn.commit()

            if db_config.is_postgres():
                if "RETURNING id" in query.upper():
                    result = cursor.fetchone()
                    return result['id'] if result else None
                else:
                    cursor.execute("SELECT LASTVAL()")
                    result = cursor.fetchone()
                    return result['lastval'] if result else None
            else:
                return cursor.lastrowid

        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def execute_sql(query: str, params: tuple = None):
        """Run a SQL query and return every row."""
        conn = DatabaseManager.get_connection()
        cursor = conn.cursor()

        try:
            if db_config.is_postgres():
                query = query.replace('?', '%s')

            cursor.execute(query, params or ())
            result = cursor.fetchall()
            conn.commit()

            return [DatabaseManager._row_to_dict(row) for row in result]

        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def execute_sql_one(query: str, params: tuple = None):
        """Run a SQL query and return a single row."""
        conn = DatabaseManager.get_connection()
        cursor = conn.cursor()

        try:
            if db_config.is_postgres():
                query = query.replace('?', '%s')

            cursor.execute(query, params or ())
            result = cursor.fetchone()
            conn.commit()

            if result:
                return DatabaseManager._row_to_dict(result)
            return None

        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def execute_commit_only(query: str, params: tuple = None) -> int:
        """Run a SQL query, commit, and return the number of affected rows."""
        conn = DatabaseManager.get_connection()
        cursor = conn.cursor()

        try:
            if db_config.is_postgres():
                query = query.replace('?', '%s')

            cursor.execute(query, params or ())
            conn.commit()
            return cursor.rowcount

        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def executemany_commit(query: str, params_list: list):
        """Run a query for many parameter sets and commit."""
        try:
            adapted_query, adapted_params = DatabaseManager._adapt_query(query, None)
            conn = DatabaseManager.get_connection()
            cursor = conn.cursor()
            cursor.executemany(adapted_query, params_list)
            conn.commit()
            return True
        except Exception as e:
            import logging
            logging.error(f"Error in executemany_commit: {str(e)}")
            return False

    @staticmethod
    def _row_to_dict(row):
        """Convert a result row to a dict."""
        if row is None:
            return None

        if hasattr(row, '_asdict'):  # namedtuple
            return dict(row)
        elif hasattr(row, 'keys'):  # psycopg2 RealDictRow or sqlite3.Row
            return {key: row[key] for key in row.keys()}
        elif isinstance(row, tuple):
            # Unnamed tuples: fall back to positional column names.
            return {f'col_{i}': value for i, value in enumerate(row)}
        else:
            return dict(row)

    @staticmethod
    def test_connection():
        """Check that the database is reachable."""
        try:
            conn = DatabaseManager.get_connection()
            cursor = conn.cursor()

            if db_config.is_postgres():
                cursor.execute("SELECT version();")
                result = cursor.fetchone()
                db_version = result['version'] if result else "Unknown"
                db_type = "PostgreSQL"
            else:
                cursor.execute("SELECT sqlite_version();")
                result = cursor.fetchone()
                db_version = result[0] if result else "Unknown"
                db_type = "SQLite"

            cursor.close()
            conn.close()

            return {
                "success": True,
                "database_type": db_type,
                "version": db_version,
                "config": str(db_config)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "config": str(db_config)
            }

    @staticmethod
    def execute_in_transaction(queries: list):
        """Run a list of queries inside one transaction."""
        conn = DatabaseManager.get_connection()
        cursor = conn.cursor()

        try:
            for query, params in queries:
                if db_config.is_postgres():
                    query = query.replace('?', '%s')
                cursor.execute(query, params or ())

            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()


db_config = DatabaseConfig()

# Log the resolved configuration on import, so a mis-pointed run is visible early.

if __name__ != "__main__":
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"Database configuration loaded: {db_config}")