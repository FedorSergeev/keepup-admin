"""The provider plugin for the homegrown system, written the way its author would.

Everything the framework needs from the other system goes through here, and
nothing of that system leaks past it: the framework sees ExternalIdentity,
AccessRequest and the two exceptions, and never the system's headers, session
strings or verdicts. A deployment names it in its configmap:

    identity_provider:
      name: homegrown-as
      plugin: keepup.tests.homegrown_as.provider:HomegrownProvider
      settings:
        base_url: https://as.example/
        service_key: ${HOMEGROWN_AS_KEY}
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

import httpx

from keepup.auth.identity import (
    AccessRequest,
    ExternalIdentity,
    IdentityProvider,
    IdentityRejected,
    ProviderUnavailable,
)

from keepup.tests.homegrown_as.service import KEY_HEADER, SESSION_HEADER, SESSION_PREFIX


class HomegrownProvider(IdentityProvider):
    """Tokens, passwords and decisions of the homegrown system."""

    def __init__(self, settings: Mapping[str, Any]):
        super().__init__(settings)
        missing = [key for key in ("base_url", "service_key") if not self.settings.get(key)]
        if missing:
            raise ValueError(f"missing settings: {', '.join(missing)}")
        self._client = httpx.AsyncClient(
            base_url=self.settings["base_url"],
            headers={KEY_HEADER: self.settings["service_key"]},
            timeout=float(self.settings.get("timeout", 5)),
        )

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise ProviderUnavailable(f"{type(error).__name__}")
        if response.status_code >= 500:
            raise ProviderUnavailable(f"the system answered {response.status_code}")
        if response.status_code == 403:
            # Our key was refused: a deployment problem, not the person's.
            raise ProviderUnavailable("the system does not accept this service key")
        return response

    @staticmethod
    def _identity(principal: Mapping[str, Any], valid_until: Optional[str]) -> ExternalIdentity:
        grants = principal.get("grants") or []
        return ExternalIdentity(
            subject=principal["uid"],
            username=principal.get("login"),
            email=principal.get("mail"),
            full_name=principal.get("displayName"),
            roles=tuple(principal.get("groups") or ()),
            # "action@*" is the right everywhere; a right on one object is for
            # decide() to judge, not a right in general.
            permissions=tuple(g.split("@")[0] for g in grants if g.endswith("@*")),
            expires_at=datetime.fromisoformat(valid_until) if valid_until else None,
            attributes={"uid": principal["uid"]},
        )

    async def verify_token(self, token: str) -> ExternalIdentity:
        if not token.startswith(SESSION_PREFIX):
            # Not one of this system's sessions at all: no reason to ask it.
            raise IdentityRejected("not a session of the homegrown system")
        response = await self._request("GET", "/api/sessions/current",
                                       headers={SESSION_HEADER: token})
        if response.status_code == 404:
            raise IdentityRejected("no such session")
        if response.status_code != 200:
            raise ProviderUnavailable(f"unexpected answer {response.status_code}")
        body = response.json()
        return self._identity(body["principal"], body.get("validUntil"))

    async def verify_password(self, username: str, password: str) -> ExternalIdentity:
        response = await self._request("POST", "/api/sessions",
                                       json={"login": username, "secret": password})
        if response.status_code == 401:
            raise IdentityRejected("bad credentials")
        if response.status_code != 200:
            raise ProviderUnavailable(f"unexpected answer {response.status_code}")
        body = response.json()
        return self._identity(body["principal"], body.get("validUntil"))

    async def decide(self, identity: Optional[ExternalIdentity], user: Mapping[str, Any],
                     request: AccessRequest) -> bool:
        if identity is None:
            # The system knows its own people only; a local account is not one.
            return False
        target = next(iter(request.path_params.values()), None)
        response = await self._request("POST", "/api/decisions", json={
            "uid": identity.attributes.get("uid", identity.subject),
            "action": request.permission,
            "object": target,
        })
        if response.status_code != 200:
            raise ProviderUnavailable(f"unexpected answer {response.status_code}")
        return response.json().get("verdict") == "permit"

    async def close(self) -> None:
        await self._client.aclose()
