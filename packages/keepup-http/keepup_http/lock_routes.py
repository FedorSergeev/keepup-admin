"""The panel's lock endpoints (keepup-121).

They were the tail of the kernel's `locks.py`, which is why the base imported
FastAPI for a panel it does not otherwise serve. The panel is the HTTP
transport's; what stays in the kernel is the machinery -- the advisory locks, the
counter of forced releases and the samples the metrics capability asks for.
"""

import logging

from fastapi import Depends, HTTPException, Query, status

from keepup import locks as lock_manager
from keepup_auth import user_roles
from keepup_auth.dependencies import get_current_admin
from keepup.roles import ROLE_ADMIN

logger = logging.getLogger(__name__)

__all__ = ["register_lock_routes"]


def register_lock_routes(app):
    """Register the administrative lock endpoints on the application."""

    @app.get("/api/admin/locks")
    def get_active_locks(admin: dict = Depends(get_current_admin)):
        """Return the active locks (administrators only)."""
        from keepup_db import DatabaseManagerV2
        try:
            if not user_roles.has_role(admin, ROLE_ADMIN):
                logger.warning(f"Non-admin user {admin['username']} attempted to access locks endpoint")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin access required"
                )

            locks = DatabaseManagerV2.execute(
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
    def force_release_lock(
            lock_name: str,
            admin: dict = Depends(get_current_admin),
            confirm: bool = Query(False, description="Confirm force release")
    ):
        """Force-release a lock (administrators only)."""
        from keepup_db import DatabaseManagerV2
        try:
            if not user_roles.has_role(admin, ROLE_ADMIN):
                logger.warning(f"Non-admin user {admin['username']} attempted to release lock {lock_name}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin access required"
                )

            existing_lock = DatabaseManagerV2.execute_one(
                "SELECT * FROM distributed_locks WHERE lock_name = :lock_name",
                {"lock_name": lock_name}
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

            result = DatabaseManagerV2.execute_commit(
                "DELETE FROM distributed_locks WHERE lock_name = :lock_name",
                {"lock_name": lock_name}
            )

            if result:
                logger.warning(
                    f"Lock '{lock_name}' force-released by admin {admin['username']}. "
                    f"Was held by instance: {existing_lock['instance_id']}, "
                    f"acquired at: {existing_lock['acquired_at']}"
                )

                # Counted and published as a sample by whoever collects it;
                # never written into `system_metrics`, which belongs to the
                # metrics capability -- a lock is the kernel's business
                # (keepup-109).
                global _force_released

                _force_released += 1

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
    def get_locks_stats(admin: dict = Depends(get_current_admin)):
        """Return lock statistics (administrators only)."""
        try:
            if not user_roles.has_role(admin, ROLE_ADMIN):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin access required"
                )

            stats = lock_manager.lock_stats()

            logger.info(f"Admin {admin['username']} viewed locks statistics")
            return stats

        except Exception as e:
            logger.error(f"Error retrieving locks stats: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error retrieving lock statistics"
            )
