"""What the framework's own tests need, and nothing of an application's.

Task keepup-6. These tests used to live under the repository's tests/ and got
their environment from its conftest: a throwaway database, a signing key, a
stubbed prometheus_client -- and, mixed in with those, values that belong to
one product (the build node's callback secret). Once the framework is a
repository of its own, none of that is there.

So this file says what the framework needs on its own terms. Everything here
has a reason to be here; a value that belongs to a product does not, however
convenient it would be.

Run the suite by path, or through ci/tests/keepup.sh:

    python3 -m pytest keepup/tests/runtime_tests.py -v
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# --- the database ------------------------------------------------------------

# DatabaseConfig reads these on import, so they have to be set before anything
# imports keepup.db -- which is why they sit at module level in a conftest and
# not in a fixture. Without them the configuration falls back to
# config/postgres.properties, a real server: the tests would either hang on an
# unreachable host or run DDL against somebody's database.
#
# An explicit DB_TYPE in the environment still wins, so a run against a real
# database can be arranged deliberately.
if not os.environ.get("DB_TYPE"):
    _session_database = tempfile.mkdtemp(prefix="keepup_framework_tests_")
    os.environ["DB_TYPE"] = "sqlite"
    os.environ["DB_PATH"] = str(Path(_session_database) / "session.db")

# --- the signing key ----------------------------------------------------------

# Code that mints or checks a token refuses to work without one, and refuses
# the placeholders it knows (keepup/auth/signing_key.py). Tests are not a
# deployment, so they bring their own -- and a key set in the environment still
# wins.
os.environ.setdefault("SECRET_KEY", "framework-tests-only-key-not-for-deployments")

# --- prometheus_client --------------------------------------------------------

# Replaced before anything imports it: a real collector registry is global, so
# two tests that both build an application would collide on the second
# registration of the same metric.
_prometheus = MagicMock()
_metric = MagicMock()
_metric.labels.return_value = _metric
for _factory in ("Gauge", "Counter", "Histogram"):
    getattr(_prometheus, _factory).return_value = _metric
sys.modules["prometheus_client"] = _prometheus


@pytest.fixture(autouse=True)
def framework_state_restored():
    """Put back everything create_app() sets process-wide.

    Building an application hands the settings to the whole framework. In a
    real process that is right -- there is one application, and the last word
    is its. In a test session there are dozens, and the last one built would
    otherwise decide how the rest of the run behaves.
    """
    from keepup import audit, logging_setup, web
    from keepup.auth import dependencies, routes

    saved = (
        web.STATIC_DIR, web.CLIENT_PAGE, web.VERSION_FILE, web.FAVICON_FILE,
        audit.redact, logging_setup.PROJECT_NAME, logging_setup.REMOTE_LOG_URL,
        logging_setup.REMOTE_LOG_TOKEN,
        routes.password_rule, routes.record_login, dependencies.pending_documents,
    )
    yield
    (web.STATIC_DIR, web.CLIENT_PAGE, web.VERSION_FILE, web.FAVICON_FILE,
     audit.redact, logging_setup.PROJECT_NAME, logging_setup.REMOTE_LOG_URL,
     logging_setup.REMOTE_LOG_TOKEN,
     routes.password_rule, routes.record_login, dependencies.pending_documents) = saved
