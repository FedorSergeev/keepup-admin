"""The management of users: their own profile, and the administration of
accounts and their roles.

Split out of `keepup.auth.routes` (keepup-59), which keeps signing in and the
panel session: two subjects with different readers. `register_auth_routes`
registers these routes too, so an application changes nothing; the names that
moved are still answered by `keepup.auth.routes`, with a warning.
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import List, Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from keepup.auth import panel_session, user_roles
from keepup.auth.dependencies import (
    get_all_users,
    get_current_admin,
    get_current_user,
    get_user_by_id,
    update_user,
)
from keepup.db import DatabaseManagerV2
from keepup.instance import get_instance_id

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "UserResponse",
    "register_user_routes",
    "update_user_profile",
]

logger = logging.getLogger(__name__)


class UserResponse(BaseModel):
    """A user as the API describes them; no password field ever leaves here."""

    id: int
    username: str
    email: Optional[str] = None
    phone: Optional[str] = None
    full_name: Optional[str] = None
    agree_terms: bool = False
    status: str
    #: The roles this account holds. `role` below is the deprecated mirror of
    #: this set and goes away in keepup 0.3.0 (keepup-51).
    roles: List[str] = []
    role: str
    created_at: datetime
    updated_at: Optional[datetime] = None
class UserRolesUpdate(BaseModel):
    """The set of roles a user is to hold."""

    roles: List[str]


class UserUpdateRequest(BaseModel):
    """What an administrator may change about a user."""

    status: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    full_name: Optional[str] = None

    @field_validator('email')
    def validate_email(cls, v):
        if v and not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError('Invalid email format')
        return v


class UserProfileUpdate(BaseModel):
    """What a user may change about themselves."""

    email: Optional[str] = None
    phone: Optional[str] = None
    full_name: Optional[str] = None

    @field_validator('email')
    def validate_email(cls, v):
        if v and not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError('Invalid email format')
        return v
def _replace_roles(user_id: int, names, admin: dict):
    """The administrator's change of a role set: declared names, an administrator kept."""
    roles = user_roles.normalise(names)
    user_roles.ensure_an_administrator_remains(user_id, roles, admin.get("id"))
    return user_roles.set_roles(user_id, roles)


def _checked_single_role(user_id: int, name: str, admin: dict) -> str:
    """The deprecated single role, held to the same rules as the set."""
    roles = user_roles.normalise([name])
    user_roles.ensure_an_administrator_remains(user_id, roles, admin.get("id"))
    return roles[0]


def update_user_profile(user_id: int, email: str = None, phone: str = None, full_name: str = None):
    """Update a user's profile."""
    changes = {"email": email, "phone": phone, "full_name": full_name}
    changes = {column: value for column, value in changes.items() if value is not None}
    if not changes:
        return False

    # The column names come from the fixed set above, never from the caller.
    assignments = ", ".join(f"{column} = :{column}" for column in changes)
    query = f"UPDATE users SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = :id"
    params = {**changes, "id": user_id}

    try:
        result = DatabaseManagerV2.execute_commit(query, params)
        return result > 0
    except Exception as e:
        logger.error(f"Error updating user profile: {str(e)}")
        return False
class UserPasswordUpdate(BaseModel):
    """A password change, held to the application's own rule."""

    new_password: str = Field(..., min_length=6, description="New password, at least 6 characters")
async def notify_account_blocked(manager, user_id: int, admin: dict) -> None:
    """Let every plugin that stops something for a blocked account do so."""
    if manager is None:
        return
    for plugin_id, plugin in manager.plugins.items():
        handler = (plugin.get_handlers() or {}).get("on_account_blocked") \
            if getattr(plugin, "initialized", False) else None
        if not handler:
            continue
        try:
            await handler(user_id, admin)
        except Exception as e:
            logger.error(f"Plugin {plugin_id} failed to stop what blocked account {user_id} runs: {e}")


def register_user_routes(app, manager):
    """Register the profile, user administration and role routes."""
    # The password rule is the application's, configured on the sign-in module.
    from keepup.auth import routes as sign_in

    @app.put("/api/auth/profile", response_model=dict)
    def update_profile(
        profile_data: UserProfileUpdate,
        current_user: dict = Depends(get_current_user)
    ):
        """Update the current user's profile."""
        success = update_user_profile(
            user_id=current_user["id"],
            email=profile_data.email,
            phone=profile_data.phone,
            full_name=profile_data.full_name
        )

        if success:
            return {"success": True, "message": "Profile updated successfully"}
        else:
            raise HTTPException(status_code=500, detail="Error updating profile")
    @app.put("/api/admin/users/{user_id}/password", response_model=dict)
    def admin_change_user_password(
            user_id: int,
            password_data: UserPasswordUpdate,
            admin: dict = Depends(get_current_admin)
    ):
        """Change a user's password as an administrator.

        Requires:
        - administrator rights
        - a new password of at least 6 characters.
        """
        try:
            user = get_user_by_id(user_id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # The application's rule, the same one registration is held to. It
            # used to apply on one of the two paths that set a password: a
            # deployment asking for twelve characters got them at registration
            # and lost them here, without a word (task keepup-14).
            problem = (sign_in.password_rule(password_data.new_password, user['username'])
                       if sign_in.password_rule else None)
            if problem:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)

            new_password_hash = bcrypt.hashpw(
                password_data.new_password.encode('utf-8'),
                bcrypt.gensalt()
            ).decode('utf-8')

            DatabaseManagerV2.execute_commit(
                "UPDATE users SET password_hash = :password_hash WHERE id = :id",
                {"password_hash": new_password_hash, "id": user_id})
            # A new password that leaves the old sessions working changes nothing
            # for whoever took the old one.
            sessions_revoked = panel_session.revoke_all(user_id, panel_session.REASON_PASSWORD_CHANGED)
            logger.info(
                f"Administrator {admin['username']} changed the password of "
                f"{user['username']} (ID: {user_id})"
            )

            DatabaseManagerV2.execute_commit('''
            INSERT INTO system_metrics (metric_name, metric_value, app_instance, tags)
            VALUES (:metric_name, :metric_value, :app_instance, :tags)
            ''', {"metric_name": "admin_password_change", "metric_value": 1,
                "app_instance": get_instance_id(),
                "tags": f"admin:{admin['username']},target_user:{user['username']},user_id:{user_id}"})

            return {
                "success": True,
                "message": f"Password for user {user['username']} changed successfully",
                "sessions_revoked": sessions_revoked,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Could not change the password of user {user_id}: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="Error changing password"
            )
    @app.get("/api/admin/users", response_model=List[UserResponse])
    def get_all_users_endpoint(admin: dict = Depends(get_current_admin)):
        users = get_all_users()
        return users
    @app.get("/api/admin/users/{user_id}", response_model=UserResponse)
    def get_user_endpoint(user_id: int, admin: dict = Depends(get_current_admin)):
        user = get_user_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return dict(user)
    @app.get("/api/admin/users/{user_id}/roles", response_model=dict)
    async def get_user_roles_endpoint(user_id: int, admin: dict = Depends(get_current_admin)):
        """The roles this account holds, and the roles this deployment has."""
        user = await asyncio.to_thread(get_user_by_id, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        known = await asyncio.to_thread(user_roles.known_roles)
        return {"roles": user.get("roles", []), "known_roles": known}

    @app.put("/api/admin/users/{user_id}/roles", response_model=dict)
    async def set_user_roles_endpoint(
            user_id: int,
            payload: UserRolesUpdate,
            admin: dict = Depends(get_current_admin)
    ):
        """Replace the roles this account holds.

        An empty set is refused: access is taken away by blocking the account,
        and an empty set is how the framework recognises a row that predates the
        set at all. So is a role nothing declares -- `role_modules` is joined by
        the exact name, and a role written in another case would look granted and
        grant nothing.
        """
        user = await asyncio.to_thread(get_user_by_id, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        try:
            held = await asyncio.to_thread(_replace_roles, user_id, payload.roles, admin)
        except (user_roles.EmptyRoleSet, user_roles.UnknownRole,
                user_roles.AdministratorKept) as refusal:
            raise HTTPException(status_code=400, detail=str(refusal))
        return {"success": True, "roles": held}

    @app.patch("/api/admin/users/{user_id}", response_model=dict)
    async def update_user_endpoint(
            user_id: int,
            user_data: UserUpdateRequest,
            admin: dict = Depends(get_current_admin)
    ):
        user = await asyncio.to_thread(get_user_by_id, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user_data.status and user_data.status != "active" and int(admin["id"]) == int(user_id):
            raise HTTPException(status_code=400, detail="An administrator cannot block their own account")

        # The single role is held to what the deployment declares, like the set
        # is, and to the same rule about the administrator role: this field
        # wrote whatever it was given (keepup-71).
        role = None
        if user_data.role is not None:
            try:
                role = await asyncio.to_thread(_checked_single_role, user_id, user_data.role, admin)
            except (user_roles.EmptyRoleSet, user_roles.UnknownRole,
                    user_roles.AdministratorKept) as refusal:
                raise HTTPException(status_code=400, detail=str(refusal))

        await asyncio.to_thread(
            update_user,
            user_id,
            user_data.status,
            role,
            user_data.email,
            user_data.phone,
            user_data.full_name
        )

        # Leaving "active" here is a block too, and must not skip what a block stops.
        if user.get("status") == "active" and user_data.status and user_data.status != "active":
            # The sessions go first. An HTTP request is refused anyway -- the
            # status is read on every one -- but a WebSocket authenticates once
            # at the handshake and then runs, so a blocked account kept whatever
            # socket it already had open (task keepup-14).
            await asyncio.to_thread(
                panel_session.revoke_all, int(user_id), panel_session.REASON_ACCOUNT_BLOCKED)
            await notify_account_blocked(manager, int(user_id), admin)

        return {"success": True, "message": "User updated successfully"}
