"""A service that is a plugin of this runtime: answered by a call, not a proxy.

A subsystem does not have to be a process of its own to stand behind the gateway.
A plugin of the same runtime registers its prefix and a handler with the gateway
service, and a request to that prefix is answered by calling the handler -- the
same table, the same check of the sign-in and the right at the edge, the same
request id, and no network in between.

The handler is handed a :class:`LocalCall` and answers with something JSON can
carry, or with a response of its own; it refuses with ``HTTPException``. It is
given the caller the gateway recognised, not headers to verify: there is no
network for anybody to forge them on.
"""

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from keepup_gateway.identity import Caller

__all__ = ["LocalCall", "answer_of"]


@dataclass(frozen=True)
class LocalCall:
    """One request, as a local service receives it.

    Attributes:
        method: the method.
        path: the path, without the prefix when the route strips it.
        query: the query, one value per name (the last one given).
        headers: the client's headers, minus the connection's and the gateway's own.
        body: the body, read up to the route's limit.
        caller: who is calling, when the route asks for sign-in; None otherwise.
        request_id: the id this request travels under.
        route: the route's id.
    """

    method: str
    path: str
    query: Mapping[str, str]
    headers: Mapping[str, str]
    body: bytes
    caller: Optional[Caller]
    request_id: str
    route: str

    def json(self) -> Any:
        """The body as JSON, or None for an empty one.

        Raises:
            ValueError: for a body that is not JSON.
        """
        return json.loads(self.body) if self.body else None


def answer_of(value: Any, request_id: str):
    """The response a handler's answer becomes."""
    from fastapi.responses import JSONResponse, Response

    if isinstance(value, Response):
        value.headers["X-Request-Id"] = request_id
        return value
    if value is None:
        return Response(status_code=204, headers={"X-Request-Id": request_id})
    return JSONResponse(value, headers={"X-Request-Id": request_id})
