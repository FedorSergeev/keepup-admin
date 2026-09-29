"""What every authentication provider has to be able to do.

The interface exists so that where accounts live stays a deployment decision:
the rest of the framework asks a provider to authenticate and to describe a
user, and never learns whether that meant a table, a directory or something
else. Token issuing is shared here rather than reimplemented per provider --
two implementations of signing are two places to get the signature wrong.
"""

import asyncio
import logging
import threading
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

import bcrypt

from fastapi import HTTPException
from fastapi.security import OAuth2PasswordBearer
import jwt
from starlette import status

from keepup.auth.usernames import is_valid_username
from keepup.auth.signing_key import resolve_signing_key
from keepup.auth import user_roles
from keepup.roles import ROLE_CLIENT
from keepup.auth.config import auth_config
from keepup.db import DatabaseManagerV2

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    "ALGORITHM",
    "AuthProvider",
]

logger = logging.getLogger(__name__)

#: The signing key lives in the environment -- see app/auth/signing_key.py.
#: It is resolved per call rather than bound here, so a deployment that
#: changes it does not depend on which module was imported first.
ALGORITHM = "HS256"

#: How long an issued token lives, in minutes. Read from the deployment's
#: authentication configuration rather than fixed here: `jwt.expire_minutes`
#: was parsed out of config/auth.yaml and then read by nobody, so a deployment
#: that asked for fifteen minutes went on handing out day-long tokens and had
#: no way to tell (task keepup-14).
ACCESS_TOKEN_EXPIRE_MINUTES = getattr(auth_config, "jwt_expire_minutes", 1440) or 1440

class AuthProvider(ABC):
    """Abstract base class for authentication providers."""

    def __init__(self):
        self.type = "base"


    @abstractmethod
    async def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Authenticate a user."""
        pass

    @abstractmethod
    async def get_user_info(self, username: str) -> Optional[Dict[str, Any]]:
        """Return information about a user."""
        pass

    def lookup_user(self, username: str) -> Optional[Dict[str, Any]]:
        """The user, read synchronously -- for callers that cannot await.

        A provider whose accounts are a local read overrides this with the read
        itself (the local provider does). This default serves a provider that
        only has the asynchronous get_user_info(): called from a running loop it
        has to run that coroutine on a loop of its own in a worker thread, which
        is exactly what the framework's own path no longer does -- it is kept so
        that such a provider goes on working, not as a way to be used.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.get_user_info(username))

        result: Dict[str, Any] = {}

        def run_in_thread():
            loop = asyncio.new_event_loop()
            try:
                result["user"] = loop.run_until_complete(self.get_user_info(username))
            except Exception as e:
                logger.error(f"Could not look up user {username}: {e}")
            finally:
                # Closed on every path: a loop holds an epoll descriptor and a
                # self-pipe, and a long-lived process would run out of them.
                loop.close()

        thread = threading.Thread(target=run_in_thread)
        thread.start()
        thread.join(timeout=10)
        return result.get("user")

    @abstractmethod
    async def get_user_permissions(self, username: str) -> Dict[str, bool]:
        """Return a user's permissions."""
        pass

    @abstractmethod
    async def create_user(self, user_data: Dict[str, Any]) -> bool:
        """Create a user, where the provider supports it."""
        pass

    def _determine_role_from_permissions(self, permissions: Dict[str, bool]) -> str:
        """Derive the user's role from their permissions (base implementation)."""
        if permissions.get("admin", False):
            return "ADMIN"
        elif permissions.get("manage_users", False):
            return "MANAGER"
        elif permissions.get("manage_products", False):
            return "MANAGER"
        elif permissions.get("view_reports", False):
            return "SUPPORT"
        else:
            return auth_config.default_role

    async def get_user_info_from_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Return user information carried by a JWT token (base implementation)."""
        try:
            payload = jwt.decode(
                token,
                resolve_signing_key(),
                algorithms=[auth_config.jwt_algorithm if hasattr(auth_config, 'jwt_algorithm') else ALGORITHM]
            )

            username = payload.get("sub")
            auth_source = payload.get("auth_source", "local")

            if username:
                if auth_source == self.get_auth_source():
                    return await self.get_user_info(username)

                logger.warning(
                    f"Token auth_source ({auth_source}) doesn't match provider ({self.get_auth_source()})")

            return None

        except jwt.ExpiredSignatureError:
            logger.warning("JWT token expired")
            return None
        except jwt.PyJWTError as e:
            logger.error(f"JWT error: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Error getting user info from token: {str(e)}")
            return None

    def get_auth_source(self) -> str:
        """Return the identifier of this authentication source."""
        # Subclasses are expected to override this.
        return "unknown"

    def create_user_in_db(
            self,
            username: str,
            password: str,
            email: str = None,
            phone: str = None,
            full_name: str = None,
            agree_terms: bool = False
    ):
        """Create a user, including the extra fields and the terms agreement."""

        # Every path that creates an account ends here, whatever route or plugin
        # started it: the name is held to the shape the panel can show (keepup-62).
        if not is_valid_username(username):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username may contain letters, digits and . _ @ + -, at most 64 characters"
            )

        if not agree_terms:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You must agree to the terms of use"
            )

        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        query = """
        INSERT INTO users (username, password_hash, email, phone, full_name, agree_terms, status, role, auth_source) 
        VALUES (:username, :password_hash, :email, :phone, :full_name, :agree_terms, :status, :role, :auth_source)
        """

        try:
            row = DatabaseManagerV2.execute_commit_returning(query, {
                "username": username, "password_hash": password_hash, "email": email,
                "phone": phone, "full_name": full_name, "agree_terms": agree_terms,
                "status": "blocked", "role": ROLE_CLIENT, "auth_source": self.type,
            })
            user_id = row["id"] if row else None
            if user_id is not None:
                # The roles this account holds, which is what decides what it
                # sees (keepup/auth/user_roles.py).
                user_roles.set_roles(user_id, [ROLE_CLIENT])

            logger.info(f"New user registered: {username} (agree_terms: {agree_terms})")

            return user_id

        except Exception as e:
            if "unique constraint" in str(e).lower() or "duplicate" in str(e).lower():
                raise HTTPException(status_code=400, detail="Username already exists")
            logger.error(f"Error creating user: {str(e)}")
            raise e


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login", auto_error=False)
