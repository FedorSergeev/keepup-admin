"""The log of calls this application makes to other systems.

The counterpart of the incoming request audit: when an outside system answers
with a 500 or a gateway times out, this is where the request, the response and
the timing are, without which "their side or ours" cannot be settled after the
fact.

Calls are recorded against a user, and background work has no user of its own
-- hence the service account, looked up here rather than invented, so that an
unsigned row never appears.
"""

import json
import logging
import time
from datetime import datetime
from typing import Dict, Optional


from keepup.db import DatabaseManager
from keepup.auth.dependencies import get_user_by_id, get_user_by_username

logger = logging.getLogger(__name__)

from keepup.auth import seed_accounts


class IntegrationLogger:
    """Records calls made to external systems."""

    @staticmethod
    async def log_request(
            user_id: int,
            username: str,
            host: str,
            endpoint: str,
            method: str,
            request_body: Optional[Dict] = None,
            response_body: Optional[Dict] = None,
            status_code: Optional[int] = None,
            duration_ms: Optional[int] = None
    ):
        """Record a request to an external API."""
        try:
            # Bodies are capped: a single large payload would otherwise dominate the log table.
            request_body_str = json.dumps(request_body, ensure_ascii=False) if request_body else None
            response_body_str = json.dumps(response_body, ensure_ascii=False) if response_body else None

            if request_body_str and len(request_body_str) > 10000:
                request_body_str = request_body_str[:10000] + "... [truncated]"

            if response_body_str and len(response_body_str) > 10000:
                response_body_str = response_body_str[:10000] + "... [truncated]"

            DatabaseManager.execute_commit_only('''
            INSERT INTO integration_logs 
            (user_id, username, host, endpoint, method, request_body, response_body, status_code, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                user_id,
                username,
                host,
                endpoint,
                method.upper(),
                request_body_str,
                response_body_str,
                status_code,
                duration_ms
            ))

        except Exception as e:
            logger.error(f"Could not log the outgoing request: {str(e)}")

    @staticmethod
    def get_logs(
            user_id: Optional[int] = None,
            username: Optional[str] = None,
            host: Optional[str] = None,
            start_date: Optional[datetime] = None,
            end_date: Optional[datetime] = None,
            limit: int = 100,
            offset: int = 0
    ):
        """Return log entries matching the given filters."""
        query = """
        SELECT * FROM integration_logs 
        WHERE 1=1
        """
        params = []

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)

        if username:
            query += " AND username = ?"
            params.append(username)

        if host:
            query += " AND host = ?"
            params.append(host)

        if start_date:
            query += " AND created_at >= ?"
            params.append(start_date)

        if end_date:
            query += " AND created_at <= ?"
            params.append(end_date)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        return DatabaseManager.execute_sql(query, tuple(params))

    @staticmethod
    def get_logs_count(
            user_id: Optional[int] = None,
            username: Optional[str] = None,
            host: Optional[str] = None,
            start_date: Optional[datetime] = None,
            end_date: Optional[datetime] = None
    ):
        """Return the number of log entries matching the given filters."""
        query = """
        SELECT COUNT(*) as count FROM integration_logs 
        WHERE 1=1
        """
        params = []

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)

        if username:
            query += " AND username = ?"
            params.append(username)

        if host:
            query += " AND host = ?"
            params.append(host)

        if start_date:
            query += " AND created_at >= ?"
            params.append(start_date)

        if end_date:
            query += " AND created_at <= ?"
            params.append(end_date)

        result = DatabaseManager.execute_sql_one(query, tuple(params))
        return result['count'] if result else 0


from functools import wraps


def log_external_request(host: str, endpoint: str):
    """Decorator recording calls to an external service."""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status_code = 200
            response_body = ""

            try:
                result = await func(*args, **kwargs)
                response_body = str(result)[:1000] if result else ""
                return result

            except Exception as e:
                status_code = 500
                response_body = str(e)[:1000]
                raise

            finally:
                try:
                    user_id = extract_user_id_from_args(args, kwargs)
                    user = get_user_by_id(user_id)
                    username = 'unknown'
                    if not user:
                        logger.warning(f"User {user_id} not found, skipping log")
                        user = get_user_by_username('system')
                        username = user.get('username', 'unknown')
                        user_id = user.get('id')
                    else:
                        username = user.get('username', 'unknown')

                    DatabaseManager.execute_commit_only('''
                    INSERT INTO integration_logs 
                    (user_id, username, host, endpoint, method, request_body, response_body, status_code, duration_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        user_id, username, host, endpoint, func.__name__,
                        str(kwargs.get('data', ''))[:1000],
                        response_body,
                        status_code,
                        int((time.time() - start_time) * 1000)
                    ))

                except Exception as log_error:
                    logger.error(f"Could not log the outgoing request: {log_error}")

        return wrapper

    return decorator


def extract_user_id_from_args(args, kwargs) -> Optional[int]:
    """Extract user_id from the wrapped call's arguments, tolerating any shape."""
    try:
        if 'user_id' in kwargs:
            return int(kwargs['user_id'])
        if 'current_user' in kwargs:
            user = kwargs['current_user']
            return user.get('id') if isinstance(user, dict) else getattr(user, 'id', None)

        for arg in args:
            if isinstance(arg, dict) and 'id' in arg:
                return arg.get('id')
            elif hasattr(arg, 'id'):
                return getattr(arg, 'id', None)
            elif isinstance(arg, int) and arg > 0:
                return arg

        # Some integrations carry the user only inside a settings object.
        for arg in args:
            if isinstance(arg, dict) and 'client_id' in arg:
                # Calls made outside any user context are attributed to the system user.
                return get_system_user_id()

    except (TypeError, ValueError, AttributeError) as e:
        logger.debug(f"Error extracting user_id: {e}")

    return None


def get_system_user_id():
    """Return the id of the system user that background work is signed with.

    The account itself is created once, at start-up, by
    `keepup.auth.seed_accounts` -- without a password anyone could sign in with.
    Creating it here as well is what let three spellings of it drift apart; a
    missing account now means the schema has not been initialised, and inventing
    one with a password of our own would only hide that.
    """
    try:
        system_user = get_user_by_username(seed_accounts.SYSTEM_USERNAME)
        if system_user:
            return system_user['id']
        logger.error(
            "The service account '%s' is not in the database: the schema was "
            "not initialised. Background work will go unsigned.",
            seed_accounts.SYSTEM_USERNAME)
        return None
    except Exception as e:
        logger.error(f"Error getting system user: {e}")
        return None