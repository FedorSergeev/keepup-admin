"""Accounts kept in this application's own `users` table.

The only provider since task 170. It answers three questions -- is this the
password, who is this user, what may they do -- against the database the
application already has, which is what makes a deployment need no directory
service to stand up.
"""

import asyncio
import os
import logging
from typing import Optional, Dict, Any

import bcrypt
from fastapi import HTTPException
# The same library the rest of the package signs and reads tokens with. This
# was a bare `import jwt`, which resolves to whichever of two installed
# distributions won the directory -- and the one pinned in requirements.txt is
# not the one with a module-level decode(). The call fell into `except
# Exception` and returned None, so the path was closed by accident rather than
# by decision (task keepup-15).
import jwt
from starlette import status

from keepup.auth import user_roles
from keepup.roles import ROLE_CLIENT
from keepup.auth.config import auth_config
from keepup.db import DatabaseManagerV2
from keepup.auth.signing_key import resolve_signing_key
from .base import AuthProvider, ALGORITHM

logger = logging.getLogger()

def get_user_by_username(username: str):
    """Return a user by name, including the extra fields."""
    return DatabaseManagerV2.execute_one('''
    SELECT id, username, email, phone, full_name, agree_terms, password_hash, status, role, created_at, updated_at 
    FROM users WHERE username = :username
    ''', {"username": username})

def get_user_by_id(user_id: int):
    """Return a user by id, for any configured auth provider."""
    return DatabaseManagerV2.execute_one("SELECT * FROM users WHERE id = :id", {"id": user_id})

def verify_password(plain_password: str, hashed_password: str):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

_STAND_IN_HASH = None


def _stand_in_hash() -> str:
    """A bcrypt hash of nothing anyone knows, at the cost real hashes are made with."""
    global _STAND_IN_HASH
    if _STAND_IN_HASH is None:
        _STAND_IN_HASH = bcrypt.hashpw(os.urandom(16).hex().encode(), bcrypt.gensalt()).decode()
    return _STAND_IN_HASH


class LocalAuthProvider(AuthProvider):
    """Local authentication provider backed by the existing database."""

    def _determine_role_from_permissions(self, permissions: Dict[str, bool]) -> str:
        """Derive the user's role from their permissions."""
        # Local users get their role at creation time, so the stored role wins over
        # the permission set. This override exists for the cases where permissions
        # should decide instead.
        if permissions.get("admin", False):
            return "ADMIN"
        elif permissions.get("manager", False):
            return "MANAGER"
        elif permissions.get("support", False):
            return "SUPPORT"
        else:
            return auth_config.default_role

    async def get_user_info_from_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Return user information carried by a JWT token."""
        try:
            payload = jwt.decode(
                token,
                resolve_signing_key(),
                algorithms=[auth_config.jwt_algorithm if hasattr(auth_config, 'jwt_algorithm') else ALGORITHM]
            )

            username = payload.get("sub")
            if username:
                return await self.get_user_info(username)

            return None

        except Exception as e:
            logger.error(f"Error getting user info from token: {str(e)}")
            return None

    async def create_user(self, user_data: Dict[str, Any]) -> bool:
        """Create a local user."""
        try:
            from keepup.auth.dependencies import create_user as create_user_func

            user_id = await create_user_func(
                username=user_data["username"],
                password=user_data["password"],
                email=user_data.get("email"),
                phone=user_data.get("phone"),
                full_name=user_data.get("full_name"),
                agree_terms=user_data.get("agree_terms", False)
            )

            if user_id:
                logger.info(f"Local user {user_data['username']} created with ID {user_id}")
                return True

            return False

        except Exception as e:
            logger.error(f"Error creating local user: {str(e)}")
            return False

    async def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        # A read and a bcrypt check -- a quarter of a second of CPU by design --
        # neither of which belongs on the event loop.
        return await asyncio.to_thread(self._authenticate, username, password)

    @staticmethod
    def _authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
        user = get_user_by_username(username)
        if not user:
            # The same bcrypt work as for a real account: answered at once, a
            # missing name was told apart from a wrong password by time alone --
            # 0.002 s against 0.27 s (audit keepup-52, finding 4).
            verify_password(password, _stand_in_hash())
            return None

        stored = user.get("password_hash") or ""
        if not stored.startswith("$2"):
            # No password here: an account an outside identity owns carries a
            # value that is not a hash (keepup/auth/external_accounts.py), and
            # bcrypt raised on it -- a 500 on the sign-in form instead of a no.
            # The stand-in keeps the answer as slow as a real check.
            verify_password(password, _stand_in_hash())
            return None

        if not verify_password(password, stored):
            return None

        return user

    def lookup_user(self, username: str) -> Optional[Dict[str, Any]]:
        """The account, read from this application's table."""
        return get_user_by_username(username)

    async def get_user_info(self, username: str) -> Optional[Dict[str, Any]]:
        # The read is a blocking query; outside the loop it holds nobody up.
        return await asyncio.to_thread(get_user_by_username, username)

    async def get_user_permissions(self, username: str) -> Dict[str, bool]:
        user = await asyncio.to_thread(get_user_by_username, username)
        if not user:
            return {}

        permissions = await DatabaseManagerV2.execute_async('''
            SELECT permission_name, granted 
            FROM user_permissions 
            WHERE user_id = :id
        ''', {"id": user["id"]})

        return {p["permission_name"]: p["granted"] for p in permissions}

    def create_user_sync(
            self,
            username: str,
            password: str,
            email: str = None,
            phone: str = None,
            full_name: str = None,
            agree_terms: bool = False
    ):
        """Create a user, including the extra fields and the terms agreement."""

        if not agree_terms:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You must agree to the terms of use"
            )

        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        query = """
        INSERT INTO users (username, password_hash, email, phone, full_name, agree_terms, status, role) 
        VALUES (:username, :password_hash, :email, :phone, :full_name, :agree_terms, :status, :role)
        """

        try:
            row = DatabaseManagerV2.execute_commit_returning(query, {
                "username": username, "password_hash": password_hash, "email": email,
                "phone": phone, "full_name": full_name, "agree_terms": agree_terms,
                "status": "blocked", "role": ROLE_CLIENT,
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