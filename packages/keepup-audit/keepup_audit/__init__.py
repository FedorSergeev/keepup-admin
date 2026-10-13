"""Incoming calls, the application's events, the administrator's trail and their retention.

This distribution provides `audit, events` and declares one plugin,
`audit`, in the `keepup.plugins` group. It depends on `keepup-admin`
for the constructor -- the descriptor, the catalogue, the service registry and
the lifecycle -- and on nothing of another capability.

The code moves here in keepup-124: until then the plugin lives in the framework's
own `builtin/audit.py` and this package is the home it is moving to.
"""

__version__ = "0.5.0"


#: Names the old path answered and the package answers now, on access.
_AUDIT_NAMES = {
    "IncomingRequestLogger", "incoming_requests_buffer", "init_incoming_requests_table",
    "log_api_request", "configure", "keep_as_is", "names_only",
    "audit_retention_background", "background_buffer_flusher",
}


def __getattr__(name):
    if name in _AUDIT_NAMES:
        from keepup_audit import audit

        return getattr(audit, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
