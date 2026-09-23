"""Serialising background work across replicas.

The application runs as several replicas against one database, so a job that
must run once -- a feed sync, a billing pass -- cannot rely on being
alone in its process. The lock is a row in ``distributed_locks``: taking it is
an insert, and a holder that died without releasing is cleared by age, which is
why every lock carries both how long to wait for it and how long it may be held.
"""

import asyncio
import functools
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Query, status

from keepup.auth.dependencies import get_current_admin
from keepup.db import DatabaseManager, DatabaseManagerV2
from keepup.instance import get_instance_id
from keepup.roles import ROLE_ADMIN

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "DatabaseLock",
    "distributed_lock",
    "with_distributed_lock",
]

logger = logging.getLogger(__name__)


class DatabaseLock:
    """A lock held in the database, so that replicas can see each other's."""

    def __init__(self, lock_name: str, timeout: int = 300, max_lock_time: int = None):
        self.lock_name = lock_name
        self.timeout = timeout  # how long to wait for the lock
        self.max_lock_time = max_lock_time or (timeout * 2)  # how long the lock may be held
        self.instance_id = get_instance_id()
        self.acquired = False

    async def acquire(self) -> bool:
        """Take the lock, clearing stale holders first."""
        try:
            await self._cleanup_stale_locks()
            result = DatabaseManager.execute_commit_only('''
            INSERT INTO distributed_locks (lock_name, acquired_at, instance_id)
            VALUES (?, CURRENT_TIMESTAMP, ?)
            ''', (self.lock_name, self.instance_id))

            if result:
                self.acquired = True
                logger.info(f"Lock '{self.lock_name}' acquired by {self.instance_id}")
                return True

        except Exception as e:
            if "unique constraint" in str(e).lower() or "duplicate" in str(e).lower():
                return await self._try_acquire_existing()
            logger.error(f"Error acquiring lock '{self.lock_name}': {str(e)}")

        return False

    async def _cleanup_stale_locks(self):
        """Clear locks held longer than max_lock_time."""
        try:
            cutoff_time = datetime.utcnow() - timedelta(seconds=self.max_lock_time)
            cleanup_query = '''
            DELETE FROM distributed_locks 
            WHERE lock_name = :lock_name 
            AND acquired_at < :cutoff_time
            '''

            params = {
                "lock_name": self.lock_name,
                "cutoff_time": cutoff_time
            }

            deleted_count = DatabaseManagerV2.execute_commit(cleanup_query, params)

            if deleted_count > 0:
                logger.warning(f"Cleaned up {deleted_count} stale lock(s) for '{self.lock_name}' "
                               f"(older than {self.max_lock_time} seconds)")

        except Exception as e:
            logger.error(f"Error cleaning up stale locks for '{self.lock_name}': {str(e)}")

    async def _try_acquire_existing(self) -> bool:
        """Take over an existing lock when it has expired."""
        try:
            lock = DatabaseManager.execute_sql_one(
                "SELECT * FROM distributed_locks WHERE lock_name = ?",
                (self.lock_name,)
            )

            if not lock:
                return False

            lock_time = lock['acquired_at']
            if isinstance(lock_time, str):
                lock_time = lock_time.replace('Z', '')
                lock_time = datetime.fromisoformat(lock_time)

            time_diff = (datetime.utcnow() - lock_time).total_seconds()

            if time_diff > self.max_lock_time:
                logger.warning(f"Lock '{self.lock_name}' expired (held for {time_diff:.1f}s, "
                               f"max: {self.max_lock_time}s), attempting to acquire...")

                DatabaseManager.execute_commit_only(
                    "DELETE FROM distributed_locks WHERE lock_name = ?",
                    (self.lock_name,)
                )

                return await self.acquire()

        except Exception as e:
            logger.error(f"Error trying to acquire existing lock '{self.lock_name}': {str(e)}")

        return False

    async def renew(self):
        """Move the holder's timestamp forward while the work is still running.

        Without this the lock had one fixed life and no way to say "still
        here": a background task that ran longer than max_lock_time -- ten
        minutes by default -- had its row deleted by the next replica, which
        then started the same work beside it. For a billing pass or a call to
        somebody else's API, that is the work done twice (task keepup-15).

        Returns:
            Whether the row was still ours to move.
        """
        if not self.acquired:
            return False
        try:
            moved = DatabaseManagerV2.execute_commit(
                "UPDATE distributed_locks SET acquired_at = :now "
                "WHERE lock_name = :name AND instance_id = :instance",
                {"now": datetime.utcnow(), "name": self.lock_name,
                 "instance": self.instance_id})
            if not moved:
                # Somebody took it over already: saying so is the point, because
                # the caller is now doing work it no longer holds the lock for.
                logger.warning(f"Lock '{self.lock_name}' is no longer held by "
                               f"{self.instance_id}")
                self.acquired = False
            return bool(moved)
        except Exception as e:
            logger.error(f"Error renewing lock '{self.lock_name}': {str(e)}")
            return False

    async def release(self):
        """Release the lock."""
        if not self.acquired:
            return

        try:
            DatabaseManager.execute_commit_only(
                "DELETE FROM distributed_locks WHERE lock_name = ? AND instance_id = ?",
                (self.lock_name, self.instance_id)
            )
            self.acquired = False
            logger.info(f"Lock '{self.lock_name}' released by {self.instance_id}")
        except Exception as e:
            logger.error(f"Error releasing lock '{self.lock_name}': {str(e)}")

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()


#: How much of max_lock_time may pass between renewals. A third leaves room
#: for two missed beats before the row looks abandoned.
RENEWAL_FRACTION = 3


@asynccontextmanager
async def distributed_lock(lock_name: str, timeout: int = 300, max_lock_time: int = None):
    """Context manager taking a distributed lock, renewing it while it is held.

    The renewal is what makes the lock usable for work of unknown length: the
    holder says "still here" on a beat, and only a holder that stopped saying
    it is taken over. Before this, a pass that ran past max_lock_time was
    simply overtaken.
    """
    lock = DatabaseLock(lock_name, timeout, max_lock_time)
    renewal = None
    try:
        acquired = await lock.acquire()
        if not acquired:
            raise Exception(f"Could not acquire lock '{lock_name}'")

        async def keep_saying_still_here():
            beat = max(1, lock.max_lock_time // RENEWAL_FRACTION)
            while True:
                await asyncio.sleep(beat)
                await lock.renew()

        renewal = asyncio.create_task(keep_saying_still_here())
        yield lock
    finally:
        if renewal is not None:
            renewal.cancel()
            try:
                await renewal
            except asyncio.CancelledError:
                pass
        await lock.release()


def with_distributed_lock(lock_key: str, timeout: int = 300, max_lock_time: int = None):
    """Decorator taking a distributed lock around a background task."""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            lock_name = f"task_{lock_key}"
            actual_max_lock_time = max_lock_time or (timeout * 2)

            try:
                async with distributed_lock(lock_name, timeout, actual_max_lock_time):
                    logger.info(f"Acquired lock '{lock_name}' for task {func.__name__}")
                    start_time = time.time()

                    try:
                        result = await func(*args, **kwargs)
                        execution_time = time.time() - start_time
                        logger.info(f"Task {func.__name__} completed in {execution_time:.2f}s")
                        return result
                    except Exception as e:
                        execution_time = time.time() - start_time
                        logger.error(f"Task {func.__name__} failed after {execution_time:.2f}s: {str(e)}")
                        raise

            except Exception as e:
                if "Could not acquire lock" in str(e):
                    logger.info(f"Task {func.__name__} skipped - lock '{lock_name}' already acquired")
                    return None
                logger.error(f"Error in distributed lock for task {func.__name__}: {str(e)}")
                raise

        return wrapper

    return decorator


def register_lock_routes(app):
    """Register the administrative lock endpoints on the application."""

    @app.get("/api/admin/locks")
    async def get_active_locks(admin: dict = Depends(get_current_admin)):
        """Return the active locks (administrators only)."""
        try:
            if admin["role"] != ROLE_ADMIN:
                logger.warning(f"Non-admin user {admin['username']} attempted to access locks endpoint")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin access required"
                )

            locks = DatabaseManager.execute_sql(
                "SELECT * FROM distributed_locks ORDER BY acquired_at DESC"
            )

            logger.info(f"Admin {admin['username']} viewed active locks, count: {len(locks)}")
            return {"locks": locks, "count": len(locks)}

        except Exception as e:
            logger.error(f"Error retrieving locks: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error retrieving lock information"
            )


    @app.delete("/api/admin/locks/{lock_name}")
    async def force_release_lock(
            lock_name: str,
            admin: dict = Depends(get_current_admin),
            confirm: bool = Query(False, description="Confirm force release")
    ):
        """Force-release a lock (administrators only)."""
        try:
            if admin["role"] != ROLE_ADMIN:
                logger.warning(f"Non-admin user {admin['username']} attempted to release lock {lock_name}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin access required"
                )

            existing_lock = DatabaseManager.execute_sql_one(
                "SELECT * FROM distributed_locks WHERE lock_name = ?",
                (lock_name,)
            )

            if not existing_lock:
                logger.info(f"Admin {admin['username']} attempted to release non-existent lock: {lock_name}")
                return {
                    "success": False,
                    "message": f"Lock '{lock_name}' not found"
                }

            if not confirm:
                return {
                    "success": False,
                    "message": "Confirmation required. Use ?confirm=true to force release the lock.",
                    "lock_info": {
                        "lock_name": existing_lock["lock_name"],
                        "instance_id": existing_lock["instance_id"],
                        "acquired_at": existing_lock["acquired_at"]
                    }
                }

            result = DatabaseManager.execute_commit_only(
                "DELETE FROM distributed_locks WHERE lock_name = ?",
                (lock_name,)
            )

            if result:
                logger.warning(
                    f"Lock '{lock_name}' force-released by admin {admin['username']}. "
                    f"Was held by instance: {existing_lock['instance_id']}, "
                    f"acquired at: {existing_lock['acquired_at']}"
                )

                try:
                    DatabaseManager.execute_commit_only('''
                    INSERT INTO system_metrics (metric_name, metric_value, app_instance, tags)
                    VALUES (?, ?, ?, ?)
                    ''', (
                        "lock_force_released",
                        1,
                        get_instance_id(),
                        f"lock_name:{lock_name},admin:{admin['username']}"
                    ))
                except Exception as metrics_error:
                    logger.error(f"Failed to log lock release metric: {metrics_error}")

                return {
                    "success": True,
                    "message": f"Lock '{lock_name}' force-released successfully",
                    "released_lock": {
                        "lock_name": existing_lock["lock_name"],
                        "instance_id": existing_lock["instance_id"],
                        "acquired_at": existing_lock["acquired_at"]
                    }
                }
            else:
                return {
                    "success": False,
                    "message": "Failed to release lock"
                }

        except Exception as e:
            logger.error(f"Error force-releasing lock '{lock_name}': {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error releasing lock"
            )


    @app.get("/api/admin/locks/stats")
    async def get_locks_stats(admin: dict = Depends(get_current_admin)):
        """Return lock statistics (administrators only)."""
        try:
            if admin["role"] != ROLE_ADMIN:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin access required"
                )

            total_locks = DatabaseManager.execute_sql_one(
                "SELECT COUNT(*) as count FROM distributed_locks"
            )

            locks_by_instance = DatabaseManager.execute_sql('''
                SELECT instance_id, COUNT(*) as lock_count 
                FROM distributed_locks 
                GROUP BY instance_id 
                ORDER BY lock_count DESC
            ''')

            oldest_locks = DatabaseManager.execute_sql('''
                SELECT lock_name, instance_id, acquired_at,
                       (JULIANDAY('now') - JULIANDAY(acquired_at)) * 24 * 60 * 60 as seconds_held
                FROM distributed_locks 
                ORDER BY acquired_at ASC 
                LIMIT 10
            ''')

            stats = {
                "total_active_locks": total_locks["count"] if total_locks else 0,
                "locks_by_instance": locks_by_instance,
                "oldest_locks": oldest_locks,
                "current_instance": get_instance_id()
            }

            logger.info(f"Admin {admin['username']} viewed locks statistics")
            return stats

        except Exception as e:
            logger.error(f"Error retrieving locks stats: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error retrieving lock statistics"
            )
