"""The headers that cross the gateway, in each direction.

What describes the connection rather than the message (RFC 9110 7.6.1) stays on
its side; what only the gateway may say about the caller is cut from the
client's request on every route, signed in or not -- a header a service trusts
must never be one a client can write. Pure: headers in, headers out.
"""

import re
import uuid
from typing import Iterable, List, Mapping, Optional, Tuple

from keepup_gateway.identity import REQUEST_ID, is_identity_header

__all__ = ["HOP_BY_HOP", "request_id_of", "to_upstream", "to_client"]

HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailer", "trailers", "transfer-encoding", "upgrade", "proxy-connection",
})
#: Recomputed on each side: the body is re-framed by whoever sends it on.
FRAMING = frozenset({"content-length", "host"})

#: A request id a client may choose: short, and nothing a log line could be broken by.
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

Pairs = List[Tuple[str, str]]


def request_id_of(headers: Mapping[str, str]) -> str:
    """The client's request id when it is a usable one, a new one otherwise."""
    given = _get(headers, REQUEST_ID)
    if given and _REQUEST_ID.match(given):
        return given
    return uuid.uuid4().hex


def to_upstream(pairs: Iterable[Tuple[str, str]], *, client_host: str, scheme: str,
                host: str, prefix: str, request_id: str,
                added: Optional[Mapping[str, str]] = None) -> Pairs:
    """The headers a service is sent.

    Args:
        pairs: the client's headers, as (name, value) pairs -- a name may repeat.
        client_host: the address the request came from.
        scheme: ``http`` or ``https``, as the client used it.
        host: the host the client asked for.
        prefix: the route's prefix, for a service that builds its own links.
        request_id: the id this request travels under.
        added: the route's own headers and the signed identity.
    """
    pairs = list(pairs)
    named = _connection_names(pairs)
    forwarded_for = None
    out: Pairs = []
    for name, value in pairs:
        lowered = name.lower()
        if lowered in HOP_BY_HOP or lowered in FRAMING or lowered in named:
            continue
        if is_identity_header(lowered) or lowered.startswith("x-forwarded-") \
                or lowered == REQUEST_ID.lower():
            if lowered == "x-forwarded-for":
                forwarded_for = value
            continue
        out.append((name, value))
    chain = f"{forwarded_for}, {client_host}" if forwarded_for else client_host
    out.extend([
        ("X-Forwarded-For", chain),
        ("X-Forwarded-Proto", scheme),
        ("X-Forwarded-Host", host),
        ("X-Forwarded-Prefix", prefix),
        (REQUEST_ID, request_id),
    ])
    for name, value in (added or {}).items():
        out.append((name, value))
    return out


def to_client(pairs: Iterable[Tuple[str, str]], request_id: str) -> Pairs:
    """The headers the client is sent back: the service's, minus the connection's."""
    pairs = list(pairs)
    named = _connection_names(pairs)
    out = [(name, value) for name, value in pairs
           if name.lower() not in HOP_BY_HOP and name.lower() not in FRAMING
           and name.lower() not in named and name.lower() != REQUEST_ID.lower()]
    out.append((REQUEST_ID, request_id))
    return out


def _connection_names(pairs: Pairs) -> frozenset:
    """The headers ``Connection`` names: they belong to this hop too."""
    names = set()
    for name, value in pairs:
        if name.lower() == "connection":
            names.update(part.strip().lower() for part in value.split(",") if part.strip())
    return frozenset(names)


def _get(headers: Mapping[str, str], name: str) -> str:
    """A header's value, whatever the case of its name."""
    value = headers.get(name) if hasattr(headers, "get") else None
    if value is None and isinstance(headers, dict):
        value = {key.lower(): item for key, item in headers.items()}.get(name.lower())
    return str(value or "")
