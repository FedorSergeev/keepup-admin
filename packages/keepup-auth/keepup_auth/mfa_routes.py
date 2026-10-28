"""The endpoints of the second factor: enrol, confirm, verify, disable.

The panel is a client here: ``start`` answers a provisioning URI and a secret,
``confirm`` answers the recovery codes once, and a sign-in that needs a code
answers ``mfa_required`` and a five-minute challenge. Nothing in this module
knows how a client renders a QR code (keepup-174).
"""

import asyncio
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from keepup_auth import auth_rate_limit, dependencies, mfa, panel_session
from keepup_auth.dependencies import get_current_user, issue_session_token

logger = logging.getLogger(__name__)

__all__ = ["register_mfa_routes"]


class MfaCode(BaseModel):
    """One code from the authenticator, or one recovery code."""

    code: str


class MfaVerify(BaseModel):
    """The challenge a password earned, and the code that finishes it."""

    mfa_token: str
    code: Optional[str] = None
    recovery_code: Optional[str] = None


def register_mfa_routes(app) -> None:
    """Register the second factor's four endpoints on the application."""

    @app.get("/api/auth/mfa")
    async def mfa_status(current_user: dict = Depends(get_current_user)):
        """Whether this account has a confirmed second factor."""
        return await asyncio.to_thread(mfa.status, current_user["id"])

    @app.post("/api/auth/mfa/totp/start")
    async def mfa_start(current_user: dict = Depends(get_current_user)):
        """A new secret and the URI an authenticator app reads."""
        try:
            begun = await asyncio.to_thread(mfa.begin, current_user["id"])
        except ValueError as refusal:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(refusal))
        return {
            **begun,
            "otpauth_uri": mfa.otpauth_uri(begun["secret"], current_user["username"]),
        }

    @app.post("/api/auth/mfa/totp/confirm")
    async def mfa_confirm(code: MfaCode, current_user: dict = Depends(get_current_user)):
        """Confirm the secret with a code; the recovery codes are shown once."""
        codes = await asyncio.to_thread(mfa.confirm, current_user["id"], code.code)
        if codes is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="That code is not right for this secret")
        return {"recovery_codes": codes}

    @app.post("/api/auth/mfa/totp/disable")
    async def mfa_disable(code: MfaCode, current_user: dict = Depends(get_current_user)):
        """Forget the factor, after one more code proves the holder."""
        allowed = await asyncio.to_thread(mfa.verify_for_user, current_user["id"],
                                          code=code.code)
        if not allowed:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="That code is not right")
        await asyncio.to_thread(mfa.disable, current_user["id"])
        return {"success": True}

    @app.post("/api/auth/mfa/verify", response_model=None)
    async def mfa_verify(payload: MfaVerify, request: Request, response: Response):
        """Exchange the password's challenge and a code for a session."""
        auth_rate_limit.check(request)
        username = mfa.read_challenge(payload.mfa_token)
        if not username:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="This challenge has run out. Sign in again.")
        user = await asyncio.to_thread(dependencies.get_user_by_username, username)
        if not user or user.get("status") != "active":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Could not validate credentials")
        allowed = await asyncio.to_thread(mfa.verify_for_user, user["id"],
                                          code=payload.code,
                                          recovery=payload.recovery_code)
        if not allowed:
            logger.warning("Second factor refused for %r", username)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="That code is not right")

        # The box ticked at the password still holds after the code (keepup-177).
        remember = mfa.challenge_remember(payload.mfa_token)
        refresh_secret = panel_session.new_refresh_secret() if remember else None

        issued = await asyncio.to_thread(
            issue_session_token, user["id"], user["username"], mfa=True,
            remember=remember,
            refresh_hash=panel_session.refresh_digest(refresh_secret))
        panel_session.set_cookies(
            response, request, issued["access_token"],
            panel_session.remember_cookie_age() if remember else None,
            refresh=refresh_secret)
        return {**issued, "token_type": "bearer", "success": True}
