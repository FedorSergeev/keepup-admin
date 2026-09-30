"""Signing in and the panel session.

The management of users -- profiles, the administration of accounts, roles --
is `keepup.auth.user_routes` (keepup-59); `register_auth_routes` registers both,
and the names that moved are still answered here, with a warning.

The endpoints here are the framework's: they work against the ``users`` table
and the configured authentication provider, and know nothing about what the
application sells. What the application does add -- documents a user must
accept, a notice when an account is blocked -- arrives through the plugin
handlers and through the panel gate in ``keepup.auth.dependencies``.
"""

import inspect
import asyncio
import logging
import os
import re
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response, status
import jwt
from jwt import PyJWTError as JWTError
from pydantic import BaseModel, Field, field_validator

from keepup.auth.usernames import is_valid_username
from keepup.auth import panel_session, seed_accounts, user_routes
from keepup.auth.dependencies import (
    _authenticated_by_cookie,
    _bearer_of,
    authenticate,
    create_user,
    get_current_user,
    issue_session_token,
    refuse_foreign_session,
)
from keepup.auth.dto.token import Token
from keepup.auth.external_accounts import AccountUnavailable
from keepup.auth.identity import access
from keepup.auth.identity import runtime as identity_runtime
from keepup.auth.identity.contract import IdentityRejected, ProviderUnavailable
from keepup.auth.providers.base import ALGORITHM
from keepup.auth.signing_key import resolve_signing_key
from keepup.db import DatabaseManagerV2

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "SELF_REGISTRATION_ENV",
    "UserCreate",
    "configure",
    "get_user_from_database_by_username",
    "refresh_access_token",
    "register",
    "configure_public_config",
    "self_registration_enabled",
]

logger = logging.getLogger(__name__)

#: Whether anyone may create an account without an invitation.
SELF_REGISTRATION_ENV = "SELF_REGISTRATION_ENABLED"

#: Supplied by the application: how strict a password must be, and where a
#: successful sign-in is written down. The framework has no opinion on either
#: -- the password rule is shared with the application's invitation path, and
#: what belongs in an audit trail is the application's catalogue of events.
password_rule = None
record_login = None


_UNSET = object()


def _documents_pending(user: dict) -> bool:
    """Whether the application says this person has documents left to accept.

    Args:
        user: The signed-in user.

    Returns:
        False when the application declared no documents at all -- which is
        every application but one, and then the panel shell must not ask for
        them: the route belongs to the application that has them.
    """
    from keepup.auth import dependencies

    rule = dependencies.pending_documents
    if rule is None:
        return False
    try:
        return bool(rule(user))
    except Exception as error:
        # A failure here must not stand between a person and the panel: the
        # gate itself refuses on its own terms, this is only the hint for the
        # shell.
        logger.warning(f"Could not tell whether documents are pending: {error}")
        return False


def configure(password=_UNSET, login_record=_UNSET):
    """Supply the application's password rule and sign-in record.

    None is a value, not a silence: an application that holds passwords to no
    rule of its own has to be able to say so without inheriting the previous
    one's.
    """
    global password_rule, record_login
    if password is not _UNSET:
        password_rule = password
    if login_record is not _UNSET:
        record_login = login_record



class UserBase(BaseModel):
    """The fields every shape of a user shares."""

    username: str
class LoginRequest(BaseModel):
    """Credentials presented at sign-in."""

    username: str
    password: str
class UserCreate(UserBase):
    """A registration, validated before it reaches the database."""

    password: str
    email: Optional[str] = None
    phone: Optional[str] = None
    full_name: Optional[str] = None
    agree_terms: bool = Field(False, description="Agreement with the terms of use")

    @field_validator('username')
    def validate_username(cls, v):
        # Held to a plain shape: it is shown back in the panel (keepup-62).
        if not is_valid_username(v):
            raise ValueError("Username may contain letters, digits and . _ @ + -, "
                             "at most 64 characters")
        return v

    @field_validator('email')
    def validate_email(cls, v):
        if v and not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError('Invalid email format')
        return v

    @field_validator('phone')
    def validate_phone(cls, v):
        if v:
            v = re.sub(r'\D', '', v)
            if len(v) < 10:
                raise ValueError('Phone number must be at least 10 digits')
        return v

    @field_validator('agree_terms')
    def validate_agree_terms(cls, v):
        if not v:
            raise ValueError('You must agree to the terms of use')
        return v
def get_user_from_database_by_username(username: str):
    """Read a user straight from the database, bypassing the auth provider."""
    try:
        query = '''
        SELECT 
            id, username, password_hash, status, role,
            email, phone, full_name, agree_terms,
            created_at, updated_at,
            external_id, auth_source, last_external_sync
        FROM users 
        WHERE username = :username
        '''
        return DatabaseManagerV2.execute_one(query, {"username": username})
    except Exception as e:
        logger.error(f"Error getting user from database by username {username}: {str(e)}")
        return None
def self_registration_enabled(environ=None) -> bool:
    """Whether the registration form is open in this deployment."""
    environ = environ if environ is not None else os.environ
    return str(environ.get(SELF_REGISTRATION_ENV, "")).strip().lower() in (
        "1", "true", "yes", "on")
async def refresh_access_token(current_user: dict, request: Request = None,
                               response: Response = None):
    """Exchange a still-valid token for a new one with a full lifetime.

    Authorised by the token being exchanged and nothing else: the holder has
    already proved its right by presenting it, and the host agent keeps no
    password in memory.

    An expired token cannot be exchanged -- get_current_user rejects it before
    this handler runs -- because renewing one would make the expiry mean
    nothing: a token issued once would live forever. A blocked account is
    refused for the same reason it is refused everywhere else: status is
    checked on every request, not only at login.
    """
    from keepup.auth import session_lifetime

    # Before the window: a caller with the provider's token has no session
    # here to renew, and must not be handed one (keepup-91).
    refuse_foreign_session(current_user)

    session_started_at = current_user.get("session_started_at")
    if not session_lifetime.is_renewable(session_started_at):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This session has run its course. Sign in again.",
        )

    # Carried forward, not restarted: the window counts from the login, and the
    # session stays the one a logout will revoke.
    issued = await asyncio.to_thread(
        issue_session_token, current_user["id"], current_user["username"],
        sid=current_user.get("session_id"), session_started_at=session_started_at)
    if request is not None and response is not None and _authenticated_by_cookie(request):
        panel_session.set_cookies(response, request, issued["access_token"], issued["expires_in"])

    logger.info(f"Refreshed access token for {current_user['username']}")

    return {
        "access_token": issued["access_token"],
        "token_type": "bearer",
        "expires_in": issued["expires_in"],
        "success": True
    }


async def _provider_sign_in(username: str, password: str):
    """The account of these credentials if the identity provider vouches for them.

    None when there is no provider, it does not take passwords, or it says no;
    a 503 when it cannot be asked -- "wrong password" would be untrue.
    """
    runtime = identity_runtime.current()
    if runtime is None or not runtime.config.password_sign_in:
        return None
    try:
        identity = await runtime.identity_for_password(username, password)
        return await runtime.user_for_identity(identity)
    except IdentityRejected as refusal:
        logger.info(f"Identity provider {runtime.config.name} refused a sign-in: {refusal}")
        return None
    except (ProviderUnavailable, AccountUnavailable) as error:
        logger.warning(f"Sign-in through {runtime.config.name} unavailable: "
                       f"{type(error).__name__}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=access.UNAVAILABLE)


async def register(user: UserCreate):
    if not self_registration_enabled():
        # Not "your account is blocked", which is what a person used to meet
        # after filling this in: that answer names a state they cannot act on,
        # while this one names the way in.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is by invitation. Ask our Telegram bot for an "
                   "invitation link, and it will let you set your own password.",
        )

    # The same password rule as the invitation path. Two ways into one system
    # cannot hold a password to two different standards -- and this way held it
    # to none at all, while an invitation asked for ten characters.
    problem = password_rule(user.password, user.username) if password_rule else None
    if problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)

    try:
        user_id = await create_user(
            username=user.username,
            password=user.password,
            email=user.email,
            phone=user.phone,
            full_name=user.full_name,
            agree_terms=user.agree_terms
        )

        return {
            "success": True,
            "message": "User registered successfully. Please wait for administrator activation.",
            "user_id": user_id
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in user registration: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error registering user"
        )


#: Set by the application through configure(): what else the sign-in screen
#: needs. The framework has no terms and no password-reset address of its own.
application_public_config = None


def configure_public_config(supplier):
    global application_public_config
    application_public_config = supplier


def register_auth_routes(app, manager):
    """Register sign-in, session and user management on the application.

    ``manager`` is the plugin manager: blocking an account has to reach the
    plugins that run things for that account.
    """

    @app.get("/api/public/config")
    async def public_config_endpoint():
        """What the sign-in screen needs before anyone has signed in.

        The framework answers what it knows itself and lets the application
        fill in the rest. Before task keepup-27 the whole endpoint belonged to
        one application, and the panel shell -- which the framework ships --
        called it in every application, including the two that never had it.
        """
        config = {"self_registration": self_registration_enabled()}
        if application_public_config is None:
            return config
        try:
            extra = application_public_config()
            if inspect.isawaitable(extra):
                extra = await extra
            config.update(extra or {})
        except Exception as error:
            # The sign-in screen must come up: without the terms the register
            # form shows none, which is better than no screen at all.
            logger.warning(f"Could not read the application's public config: {error}")
        return config

    @app.post("/api/auth/register", response_model=dict)
    async def register_endpoint(user: UserCreate):
        return await register(user)

    @app.post("/api/auth/login", response_model=Token)
    async def login(login_data: LoginRequest, request: Request, response: Response):
        from keepup.auth import login_throttle

        # Guessing a password had no cost: every attempt was answered as fast as the
        # first. The count is per name and lives in the database, because the next
        # attempt may well be served by another replica.
        locked_until = await asyncio.to_thread(login_throttle.locked_until, login_data.username)
        if locked_until:
            # Deliberately says nothing about whether the name exists or the password
            # was right -- answering that here would turn the lockout into an oracle.
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many sign-in attempts. Try again later.",
            )

        # Counted before the password is looked at, so attempts sent at once are
        # numbered and the ones past the limit never reach the check (keepup-63).
        attempt = await asyncio.to_thread(login_throttle.reserve_attempt, login_data.username)
        if attempt > login_throttle.max_attempts():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many sign-in attempts. Try again later.",
            )

        # One read and one password check, whether or not the name exists: the
        # provider checks a stand-in hash for a missing account, so the time of
        # the answer does not say which names are there (keepup-63).
        user = await authenticate(login_data.username, login_data.password)
        if not user:
            # Not a local password: the other system's, if the deployment lets
            # people in with it. Local first, so that a local administrator
            # still gets in while that system is down (keepup-91).
            user = await _provider_sign_in(login_data.username, login_data.password)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        await asyncio.to_thread(login_throttle.record_success, login_data.username)

        # The administrator's password from earlier builds is in the source and in
        # the breach lists: it proves nothing about who is signing in (keepup-74).
        if seed_accounts.is_retired_admin_password(login_data.username, login_data.password):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This password is public and can no longer be used. Set "
                       f"{seed_accounts.ADMIN_PASSWORD_ENV} and restart the server, "
                       "or ask another administrator to change it.",
            )

        if user["status"] != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is blocked. Please contact administrator.",
            )

        issued = await asyncio.to_thread(issue_session_token, user["id"], user["username"])
        access_token = issued["access_token"]
        # The panel keeps the session in a cookie its scripts cannot read; the token
        # in the body stays for the host agent and scripts, which send it as Bearer.
        panel_session.set_cookies(response, request, access_token, issued["expires_in"])

        if record_login is not None:
            await record_login(user)

        return {
            "access_token": access_token,
            "token_type": "bearer",
            # How long the token has, so the holder need not decode the JWT to
            # learn what the server already knows. The agent schedules its renewal
            # off this: an expired token cannot buy a websocket key, and the host
            # then drops out of the system with the machine still running.
            "expires_in": issued["expires_in"],
            "success": True
        }
    @app.post("/api/auth/refresh")
    async def refresh_access_token_endpoint(current_user: dict = Depends(get_current_user),
                                            request: Request = None, response: Response = None):
        """Exchange a still-valid token for a new one with a full lifetime."""
        return await refresh_access_token(current_user, request, response)

    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response):
        """End this session on the server and forget the cookies.

        Answers 200 even without a live session: the browser has to be able to clear
        a cookie whose session is already gone. A POST, with the CSRF check of any
        cookie request -- a logout by GET could be triggered by an image elsewhere.
        """
        token = panel_session.token_from_request(request, _bearer_of(request))
        revoked = False
        if token:
            try:
                payload = jwt.decode(token, resolve_signing_key(), algorithms=[ALGORITHM])
                sid = payload.get(panel_session.SESSION_CLAIM)
                if sid:
                    revoked = await asyncio.to_thread(
                        panel_session.revoke, sid, panel_session.REASON_LOGOUT) > 0
            except JWTError:
                pass
        panel_session.clear_cookies(response)
        return {"success": True, "revoked": revoked}
    @app.post("/api/auth/session")
    def exchange_token_for_session(request: Request, response: Response,
                                         current_user: dict = Depends(get_current_user)):
        """Move a session the page holds as a token into the cookie.

        For the invitation page, whose session is issued by a plugin that cannot set
        a cookie, and for a panel tab still holding a token from before the cookie.
        The token is not returned: the point is that the page stops holding one.
        """
        refuse_foreign_session(current_user)
        issued = issue_session_token(current_user["id"], current_user["username"],
                                     sid=current_user.get("session_id"),
                                     session_started_at=current_user.get("session_started_at"))
        panel_session.set_cookies(response, request, issued["access_token"], issued["expires_in"])
        return {"success": True, "expires_in": issued["expires_in"]}
    @app.get("/api/auth/verify")
    async def verify_token(current_user: dict = Depends(get_current_user)):
        """Validate the caller's token."""
        return {
            "success": True,
            "user": {
                "id": current_user["id"],
                "username": current_user["username"],
                "status": current_user["status"],
                "roles": current_user.get("roles", []),
                "role": current_user["role"],
                "created_at": current_user["created_at"]
            }
        }
    @app.get("/api/auth/me")
    async def get_me(current_user: dict = Depends(get_current_user)):
        """Return the current user."""
        return {
            "id": current_user["id"],
            "username": current_user["username"],
            "email": current_user.get("email"),
            "phone": current_user.get("phone"),
            "full_name": current_user.get("full_name"),
            "status": current_user["status"],
            "roles": current_user.get("roles", []),
            # The deprecated mirror of the set: ADMIN when it is held, otherwise
            # the first role granted. Read `roles` instead -- this goes away in
            # keepup 0.3.0 (keepup-51).
            "role": current_user["role"],
            "created_at": current_user["created_at"],
            "updated_at": current_user.get("updated_at"),
            # Whether this person has documents left to accept. The framework
            # has no documents of its own and does not serve them -- an
            # application that has them registers its own routes. What the
            # framework can say is whether there is anything to ask for, and
            # that is what keeps the panel shell from calling a route that
            # exists in one application out of three.
            "documents_pending": _documents_pending(current_user),
        }

    # Users are managed in a module of their own (keepup-59); the application
    # still registers everything through this one call.
    user_routes.register_user_routes(app, manager)


#: Names that moved to keepup.auth.user_routes with keepup-59.
_MOVED_TO_USER_ROUTES = {
    "UserResponse", "UserRolesUpdate", "UserUpdateRequest", "UserProfileUpdate",
    "UserPasswordUpdate", "update_user_profile", "notify_account_blocked",
}


def __getattr__(name):
    if name in _MOVED_TO_USER_ROUTES:
        import warnings
        warnings.warn(f"keepup.auth.routes.{name} moved to keepup.auth.user_routes.{name}",
                      DeprecationWarning, stacklevel=2)
        return getattr(user_routes, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
