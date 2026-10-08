"""What is written down about a call, and who writes it: the kernel's names.

The kernel records nothing itself. It owns the two names a deployment puts its
recording behind -- ``audit``, what happened on the way in, and ``events``, what
the application decided -- and the shape of each
(``doc/service-catalogue.md``). A transport writes the audit of the calls it
carries; a capability emits events through the other.

This module is also the seam the route runtime reaches through, for the same
reason ``keepup/kernel/security.py`` is one: the wrapper FastAPI was handed is a
module-level callable, and there is nowhere else for it to find the recording of
the process it serves. Until the transport owns the application (keepup-123) it
is set at start-up, and a deployment that sets nothing records nothing --
which is a deployment, not a failure.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Optional

__all__ = [
    "SERVICE_AUDIT",
    "SERVICE_EVENTS",
    "Recording",
    "emit",
    "end_call",
    "recording",
    "set_recording",
    "start_call",
]

#: The service that writes down a call.
SERVICE_AUDIT = "audit"
#: The service that writes down what the application decided.
SERVICE_EVENTS = "events"


@dataclass
class Recording:
    """What a deployment put behind ``audit`` and ``events``.

    Attributes:
        start: ``start(method=..., endpoint=..., host=..., request_data=...)``
            -- an async context manager answering a request id.
        end: ``await end(request_id, http_status, response_data=..., error_message=...)``.
        emitters: what emits an event, in the shape the application's own event
            log already takes: ``await emit(event_type, payload)``.
        name: what to call it in a log line.
    """

    start: Optional[Callable[..., Any]] = None
    end: Optional[Callable[..., Awaitable[Any]]] = None
    emitters: list = field(default_factory=list)
    name: str = ""

    @property
    def records(self) -> bool:
        """Whether this deployment writes a call down at all."""
        return callable(self.start) and callable(self.end)


#: What the deployment registered; one per process, set at start-up.
_recording: Optional[Recording] = None


def set_recording(recording: Optional[Recording]) -> None:
    """Put a recording behind the two services, or forget the previous one."""
    global _recording  # noqa: PLW0603 - the seam described above, and it is one name
    _recording = recording


def recording() -> Optional[Recording]:
    """What is behind the two services, or None."""
    return _recording


@asynccontextmanager
async def start_call(method: str, endpoint: str, host: str,
                     request_data: Mapping[str, Any]) -> AsyncIterator[Any]:
    """Open the record of one call, or a record that writes nowhere.

    Args:
        method: the method the audit names.
        endpoint: the route's path as declared.
        host: what the call came through.
        request_data: what is known about the request.

    Yields:
        The request id the call is finished with.
    """
    current = recording()
    if current is None or not current.records:
        yield None
        return
    async with current.start(method=method, endpoint=endpoint, host=host,
                             request_data=request_data) as request_id:
        yield request_id


async def end_call(request_id: Any, http_status: int, response_data: Any = None,
                   error_message: Any = None) -> None:
    """Close the record of one call. Does nothing when nothing is recorded."""
    current = recording()
    if request_id is None or current is None or not current.records:
        return
    await current.end(request_id=request_id, http_status=http_status,
                      response_data=response_data, error_message=error_message)


async def emit(event_type: str, payload: Mapping[str, Any] = None) -> int:
    """Write an event down, through every emitter the deployment registered.

    Args:
        event_type: what happened.
        payload: what to record with it.

    Returns:
        How many emitters took it.
    """
    current = recording()
    if current is None:
        return 0
    written = 0
    for emitter in current.emitters:
        await emitter(event_type, payload or {})
        written += 1
    return written
