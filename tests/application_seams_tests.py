"""Where an application attaches to the framework, and what happens without one.

Task keepup-27. The panel shell and eight sections belong to the framework and
ship inside the package, so everything they reach for is reached for in every
application -- including the two that never had it. Three seams came out of
that, and each is here with the case that used to go wrong:

* the sign-in screen's public config: the framework answers what it knows
  itself and the application fills in the rest, where before the whole endpoint
  belonged to one application and the shell called it in all of them;
* whether a person has documents to accept: the framework says so, and only
  then does the shell ask the application for them;
* actions on a row of the users section: modules add their own, and the
  framework knows nothing about what they are -- a message over Telegram used
  to be written into the framework's own section.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/application_seams_tests.py -v
"""

import re
from pathlib import Path

import pytest
from unittest.mock import Mock
from fastapi.testclient import TestClient

from keepup.auth import dependencies as auth_dependencies
from keepup.auth import routes as auth_routes
from keepup.factory import create_app
from keepup.settings import KeepupSettings

PACKAGE = Path(__file__).resolve().parents[1]
USERS_SECTION = PACKAGE / "static" / "modules" / "js" / "users.js"
SHELL = PACKAGE / "static" / "js" / "main_new.js"


def client(**settings):
    """A client over an application built with these settings.

    The plugin manager is a stub because the framework registers the sign-in
    routes only when there is one -- blocking an account has to reach the
    plugins that run things for that account. Lifespan is deliberately not
    run (no ``with``): it would start the schedulers and the buses, and what
    is asked here is one endpoint's answer.
    """
    app = create_app(KeepupSettings(static_mounts=(), plugin_manager=Mock(), **settings))
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def seams_released():
    """Both seams are module-level state; a test must not leave one set."""
    yield
    auth_routes.configure_public_config(None)
    auth_dependencies.configure_panel_gate(pending=None)


# --- what the sign-in screen is told ------------------------------------------

def test_the_framework_serves_the_public_config_on_its_own():
    """An application that supplies nothing still gets a working sign-in screen."""
    answer = client().get("/api/public/config")

    assert answer.status_code == 200
    assert "self_registration" in answer.json()


def test_the_application_fills_in_the_rest():
    talking = client(public_config=lambda: {"terms": "Be nice", "terms_version": 3})
    config = talking.get("/api/public/config").json()

    assert config["terms"] == "Be nice"
    assert config["terms_version"] == 3
    assert "self_registration" in config, "the framework's own answer was lost"


def test_an_application_that_fails_does_not_take_the_screen_with_it():
    """No terms on the register form is bad; no sign-in screen at all is worse."""
    def broken():
        raise RuntimeError("the terms file is not there")

    answer = client(public_config=broken).get("/api/public/config")

    assert answer.status_code == 200
    assert "self_registration" in answer.json()


def test_the_application_may_answer_asynchronously():
    async def later():
        return {"password_reset_url": "https://example.invalid/reset"}

    config = client(public_config=later).get("/api/public/config").json()

    assert config["password_reset_url"] == "https://example.invalid/reset"


# --- whether there is anything to accept --------------------------------------

def test_without_documents_the_framework_says_there_are_none():
    """Which is what keeps the shell from asking for a route that is not there."""
    auth_dependencies.configure_panel_gate(pending=None)

    assert auth_routes._documents_pending({"id": 1}) is False


def test_with_documents_pending_it_says_so():
    auth_dependencies.configure_panel_gate(pending=lambda user: ["terms"])

    assert auth_routes._documents_pending({"id": 1}) is True


def test_nothing_pending_is_not_the_same_as_no_documents_at_all():
    auth_dependencies.configure_panel_gate(pending=lambda user: [])

    assert auth_routes._documents_pending({"id": 1}) is False


def test_a_rule_that_throws_does_not_stand_between_a_person_and_the_panel():
    """The gate refuses on its own terms; this is only the hint for the shell."""
    def broken(user):
        raise RuntimeError("the acceptance table is not there")

    auth_dependencies.configure_panel_gate(pending=broken)

    assert auth_routes._documents_pending({"id": 1}) is False


def test_the_shell_asks_for_documents_only_after_that_answer():
    """Read from the shell itself: the call is behind the framework's own flag."""
    shell = SHELL.read_text(encoding="utf-8")
    gate = re.search(r"async function ensureAgreements\(\) \{(.*?)\n\}", shell, re.S)

    assert gate, "ensureAgreements is gone from the shell"
    assert "documentsPending()" in gate.group(1), (
        "the shell asks every application for documents again")


# --- actions on a row of the users section ------------------------------------

def test_the_users_section_knows_nothing_of_any_application_feature():
    """A message over Telegram used to be written into this framework section.

    Not a boundary nicety: the section fetched that application's endpoint in
    every application, and the button was part of the package.
    """
    section = USERS_SECTION.read_text(encoding="utf-8")
    code = "\n".join(line for line in section.splitlines() if not line.lstrip().startswith("//"))

    assert "/api/telegram" not in code
    assert "telegram" not in code.lower()


def test_the_section_offers_a_place_for_them_instead():
    section = USERS_SECTION.read_text(encoding="utf-8")

    assert "window.KeepupUserActions" in section
    assert "usersPrepareActions" in section and "usersExtraActions" in section


def test_the_users_section_carries_no_subscription_model():
    """Two hundred lines of one application's billing used to live here.

    A plan column, a plan dialog, a dropdown that changed a subscription and a
    price formatter -- shipped inside the package to everyone who installed it
    (keepup-28). It never failed at runtime: the column was already hidden
    where the plugin did not answer. What it did was put one application's
    business model in a framework a stranger installs.
    """
    section = USERS_SECTION.read_text(encoding="utf-8")
    code = "\n".join(line for line in section.splitlines() if not line.lstrip().startswith("//"))

    for trace in ("tariff", "Tariff", "/api/admin/users/${user.id}/tariff",
                  "formatPrice", "allTariffs", "subscription"):
        assert trace not in code, f"the users section still carries {trace!r}"


def test_a_column_can_be_added_to_the_users_table():
    """The seam the billing left through, stated as what it is.

    A row action was not enough: a column needs a heading of its own, a cell
    per row, and a moment after the table is in the page to wire its controls.
    """
    section = USERS_SECTION.read_text(encoding="utf-8")

    assert "window.KeepupUserColumns" in section
    for part in ("usersPrepareColumns", "usersExtraCells",
                 "usersPlaceExtraHeadings", "usersColumnsRendered"):
        assert part in section, f"the column registry has no {part}"
