"""Incoming calls, the application's events, the administrator's trail and their retention.

This distribution provides `audit, events` and declares one plugin,
`audit`, in the `keepup.plugins` group. It depends on `keepup-admin`
for the constructor -- the descriptor, the catalogue, the service registry and
the lifecycle -- and on nothing of another capability.

The code moves here in keepup-124: until then the plugin lives in the framework's
own `builtin/audit.py` and this package is the home it is moving to.
"""

__version__ = "0.6.0"


# The names below live in the module that owns them since keepup-155: the
# recording and its redaction in `keepup_audit.audit`, the events and their
# retention in `keepup_audit.events`, the trail in `keepup_audit.trail`. The
# package answers none of them for the old path any more -- `from keepup_audit
# import audit` is the module, and `audit.log_api_request` is the call.
