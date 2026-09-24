"""The two endpoints of the external sign-in, and what happens between them.

``/api/auth/oidc/login`` sends the browser to the provider;
``/api/auth/oidc/callback`` receives it back, checks everything
(:mod:`keepup.auth.oidc`), decides whether somebody unknown may have an account
(:mod:`keepup.auth.oidc_policy`), and ends by issuing the ordinary session of
this application -- the same token and the same cookie a local sign-in gives,
so the panel, the renewal and the logout know nothing about how the person got
in.

Registered only when the application configured a provider. Without one the
paths do not exist: the possibility is off, not broken, and a 404 there is the
truthful answer.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse

from keepup.auth import oidc, oidc_policy, panel_session
from keepup.auth.dependencies import issue_session_token
from keepup.db import DatabaseManagerV2
from keepup.roles import ROLE_ADMIN, ROLE_CLIENT

logger = logging.getLogger(__name__)

#: Permissions that make somebody an administrator here. Mapping a claim onto
#: one of them is how a provider's role becomes a role in this application.
ADMIN_PERMISSIONS = ("admin", "manage_users", "manage_settings")


def _refuse(reason: str) -> HTTPException:
    """One answer for every failed check; the reason stays in the log.

    Telling the caller which check failed says which ones they passed, and that
    is a map for whoever is probing.
    """
    logger.warning(f"OIDC sign-in refused: {reason}")
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=oidc.REFUSAL)


# --- the account behind the claims --------------------------------------------

def find_account(issuer: str, subject: str) -> Optional[Dict[str, Any]]:
    """The account tied to this provider's subject, if there is one.

    Looked up by subject rather than by email: an address at a provider changes
    hands, and matching on it would let a new employee into the account of the
    person whose address they inherited.
    """
    rows = DatabaseManagerV2.execute(
        "SELECT id, username, status, role FROM users "
        "WHERE auth_source = :source AND external_id = :subject",
        {"source": issuer, "subject": str(subject)},
    )
    return dict(rows[0]) if rows else None


def propose_username(claims: Dict[str, Any]) -> str:
    """A name for the new account, taken from what the provider said."""
    for claim in ("preferred_username", "email", "sub"):
        value = claims.get(claim)
        if value:
            return str(value).strip().lower()
    return "user"


def free_username(proposed: str) -> str:
    """The proposed name, or the first free variant of it.

    Names are unique here and come from somewhere else entirely; two providers,
    or two people at one provider, can propose the same one.
    """
    candidate = proposed
    suffix = 1
    while DatabaseManagerV2.execute_one(
            "SELECT id FROM users WHERE username = :name", {"name": candidate}):
        suffix += 1
        candidate = f"{proposed}-{suffix}"
    return candidate


def create_account(issuer: str, claims: Dict[str, Any],
                   decision: oidc_policy.AccountDecision) -> Dict[str, Any]:
    """Create the account the application's policy agreed to.

    The password column is filled with an unusable value rather than left
    empty: this account has no password here, and a blank one is something a
    local sign-in might one day accept.
    """
    username = free_username(propose_username(claims))
    DatabaseManagerV2.execute_commit(
        "INSERT INTO users (username, password_hash, status, role, email, full_name, "
        "auth_source, external_id, agree_terms) "
        "VALUES (:username, :password_hash, :status, :role, :email, :full_name, "
        ":source, :subject, :agree_terms)",
        {
            "username": username,
            "password_hash": "!external",
            "status": decision.status,
            "role": decision.role,
            "email": claims.get("email"),
            "full_name": claims.get("name"),
            "source": issuer,
            "subject": str(claims["sub"]),
            "agree_terms": False,
        },
    )
    logger.info(f"OIDC account created for {issuer} subject: {username} ({decision.reason})")
    account = find_account(issuer, claims["sub"])
    if account is None:
        raise _refuse("the account was created but cannot be read back")
    return account


# --- roles out of the claims ---------------------------------------------------

def external_roles(claims: Dict[str, Any], roles_claim: str) -> List[str]:
    """The provider's roles, however this provider chose to shape them."""
    value = claims.get(roles_claim)
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in value.replace(",", " ").split() if part]
    if isinstance(value, (list, tuple)):
        return [str(part) for part in value if part]
    return []


def permissions_for(issuer: str, roles: List[str]) -> Dict[str, bool]:
    """Map the provider's roles onto permissions here.

    Through the same table the directory provider used, keyed by the issuer:
    which outside role means what is a property of the deployment, not of the
    code.
    """
    granted: Dict[str, bool] = {}
    for role in roles:
        rows = DatabaseManagerV2.execute(
            "SELECT internal_permission_name FROM external_role_mappings "
            "WHERE auth_source = :source AND external_role_name = :role",
            {"source": issuer, "role": role},
        )
        for row in rows:
            granted[row["internal_permission_name"]] = True
    return granted


def apply_permissions(user_id: int, permissions: Dict[str, bool]) -> None:
    """Write this sign-in's permissions, replacing what was there.

    Replaced rather than added to: a permission taken away at the provider has
    to disappear here as well, and that is the whole reason a central sign-in
    is worth having.
    """
    DatabaseManagerV2.execute_commit(
        "DELETE FROM user_permissions WHERE user_id = :user_id", {"user_id": user_id})
    for name, granted in permissions.items():
        DatabaseManagerV2.execute_commit(
            "INSERT INTO user_permissions (user_id, permission_name, granted) "
            "VALUES (:user_id, :name, :granted)",
            {"user_id": user_id, "name": name, "granted": bool(granted)},
        )


def role_from_permissions(permissions: Dict[str, bool], default_role: str) -> str:
    """An administrator is somebody holding an administrative permission."""
    if any(permissions.get(name) for name in ADMIN_PERMISSIONS):
        return ROLE_ADMIN
    return default_role or ROLE_CLIENT


def sync_roles(account: Dict[str, Any], issuer: str, claims: Dict[str, Any],
               settings) -> str:
    """Recompute what this person may do, from what the provider said just now."""
    permissions = permissions_for(issuer, external_roles(claims, settings.roles_claim))
    apply_permissions(account["id"], permissions)

    role = role_from_permissions(permissions, settings.default_role)
    if role != account.get("role"):
        DatabaseManagerV2.execute_commit(
            "UPDATE users SET role = :role WHERE id = :user_id",
            {"role": role, "user_id": account["id"]},
        )
        logger.info(f"OIDC role for {account['username']}: {account.get('role')} -> {role}")
    return role


# --- the endpoints -------------------------------------------------------------

def register_oidc_routes(app, settings) -> None:
    """Register external sign-in, if this application configured a provider."""
    if settings.oidc is None:
        return

    provider = settings.oidc
    directory = oidc.ProviderDirectory(provider)
    app.state.oidc_directory = directory

    @app.get("/api/auth/oidc/login", include_in_schema=False)
    async def oidc_login(request: Request):
        """Send the browser to the provider to have the person identified.

        Takes the request only to decide whether the flow cookie may be marked
        Secure -- the same question the session cookie asks.
        """
        try:
            metadata = await directory.metadata()
        except Exception as error:
            logger.error(f"OIDC provider unreachable: {error}")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="The identity provider is unavailable")

        state = oidc.secrets.token_urlsafe(16)
        nonce = oidc.secrets.token_urlsafe(16)
        verifier, challenge = oidc.make_pkce_pair()

        response = RedirectResponse(
            oidc.authorization_url(metadata, provider, state, nonce, challenge),
            status_code=status.HTTP_303_SEE_OTHER,
        )
        response.set_cookie(
            oidc.FLOW_COOKIE,
            oidc.issue_flow_token(state, nonce, verifier),
            max_age=oidc.FLOW_COOKIE_MAX_AGE,
            path="/api/auth/oidc",
            httponly=True,
            samesite="lax",
            # It carries the PKCE verifier, the nonce and the state. This was
            # False unconditionally -- not even asking, the way the session
            # cookie does -- so on an HTTPS deployment it still went out over a
            # forced plain request to the same host (task keepup-14).
            secure=panel_session.is_https(request),
        )
        return response

    @app.get("/api/auth/oidc/callback", include_in_schema=False)
    async def oidc_callback(request: Request, code: str = None, state: str = None,
                            error: str = None):
        """Receive the person back and, if everything checks out, let them in."""
        if error:
            raise _refuse(f"the provider answered with an error: {error}")
        if not code or not state:
            raise _refuse("the return carries no code or no state")

        try:
            flow = oidc.read_flow_token(request.cookies.get(oidc.FLOW_COOKIE))
            if flow.get("state") != state:
                raise oidc.SignInRefused("the state does not belong to this attempt")

            tokens = await oidc.exchange_code(directory, provider, code, flow["verifier"])
            claims = await oidc.verify_id_token(directory, provider,
                                                tokens["id_token"], flow["nonce"])
        except oidc.SignInRefused as refusal:
            raise _refuse(str(refusal))
        except Exception as error:
            raise _refuse(f"the exchange failed: {error}")

        issuer = (await directory.metadata()).issuer
        account = find_account(issuer, claims["sub"])

        if account is None:
            decision = oidc_policy.decide(settings.oidc_account_policy, claims)
            if not decision.admit:
                raise _refuse(f"policy refused a new account: {decision.reason}")
            account = create_account(issuer, claims, decision)

        if account.get("status") != "active":
            # Proving who you are is not the same as being allowed in.
            raise _refuse(f"the account {account['username']} is not active")

        sync_roles(account, issuer, claims, provider)

        issued = issue_session_token(account["id"], account["username"])
        response = RedirectResponse(provider.after_login_path,
                                    status_code=status.HTTP_303_SEE_OTHER)
        panel_session.set_cookies(response, request, issued["access_token"],
                                  issued["expires_in"])
        # One attempt, one use: the cookie is spent, so a replayed return finds
        # nothing to check itself against.
        response.delete_cookie(oidc.FLOW_COOKIE, path="/api/auth/oidc")

        logger.info(f"OIDC sign-in: {account['username']} via {issuer}")
        if settings.record_login is not None:
            await settings.record_login(dict(account))
        return response
