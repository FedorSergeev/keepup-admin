"""Finer-grained rights than the two roles.

`keepup.roles` has ADMIN and CLIENT, which is enough to decide who may reach an
administrative route and not enough for anything else. A permission is checked
per action, by decorator or by dependency, so a route can be opened to a
non-administrator without opening the section around it.
"""

from functools import wraps
from fastapi import HTTPException, status, Depends

from keepup.roles import ROLE_ADMIN
from keepup_auth.dependencies import get_all_users, get_current_user
from keepup_auth.identity import access
from keepup_auth.identity.contract import AccessRequest


def require_permission(permission_name: str):
    """Decorator checking that the user holds the given permission."""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current_user = kwargs.get('current_user')

            if not current_user:
                for arg in args:
                    if isinstance(arg, dict) and 'username' in arg:
                        current_user = arg
                        break

            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required"
                )

            # The same rule as a plugin route's `permission` and the
            # require_permission dependency (keepup/auth/identity/access.py).
            # It read current_user['permissions'], which nothing fills in, so
            # it refused everybody -- administrators included (keepup-91).
            await access.check(current_user, AccessRequest(permission=permission_name))

            return await func(*args, **kwargs)

        return wrapper

    return decorator


# Example of how this is used on an endpoint:
#@app.get("/api/admin/users")
@require_permission(ROLE_ADMIN)
async def get_all_users_endpoint(
        current_user: dict = Depends(get_current_user)
):
    users = (
get_all_users())
    return users