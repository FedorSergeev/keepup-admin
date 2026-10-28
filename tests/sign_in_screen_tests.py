"""The sign-in screen belongs to the framework, not to one deployment's bot (keepup-178).

The panel shell once knew where an invitation came from and where a password was
reset: the registration refusal named a Telegram bot, the "forgot password" line
told people to send it `/password`, and the shell reached for that bot's address
until keepup-27 made the address the application's. The shell is installed in
every application, so a mention of one product's bot is a mention in all of them
-- and a link to a bot that another deployment does not have is a dead end.

The address, when a deployment has one, comes from its own public config. What
is checked here is that the screen offers it and names no bot of its own.

    python3 -m pytest tests/sign_in_screen_tests.py -v
"""

import re
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from keepup.settings import KeepupSettings
from keepup_http.factory import create_app

PACKAGE = Path(__file__).resolve().parents[1]
STATIC = PACKAGE / "packages" / "keepup-http" / "keepup_http" / "static"
SHELL = STATIC / "js" / "main_new.js"
PAGES = ("index_new.html", "index_nebula.html")
AUTH_ROUTES = PACKAGE / "packages" / "keepup-auth" / "keepup_auth" / "routes.py"

#: A bot is not the only thing this framework does not know: the check names the
#: two things a person may read -- the messenger and the command a bot answers.
BOT_TEXT = re.compile(r"telegram|\bbot\b|/password", re.IGNORECASE)


def make_client():
    app = create_app(KeepupSettings(
        title="Sign-in screen",
        project_name="sign-in-screen",
        plugin_manager=Mock(),
        static_mounts=(),
    ))
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("page", PAGES)
def test_the_page_names_no_bot(page):
    markup = (STATIC / page).read_text(encoding="utf-8")

    found = BOT_TEXT.findall(markup)
    assert not found, f"{page} still names a bot: {found}"


def test_the_reset_line_is_offered_only_when_the_deployment_has_one():
    """The framework knows of no reset page; the application supplies the address."""
    for page in PAGES:
        markup = (STATIC / page).read_text(encoding="utf-8")
        hint = re.search(r'<p id="forgotPasswordHint"[^>]*>', markup)
        assert hint is not None, f"{page} has no reset line"
        assert "hidden" in hint.group(0), f"{page} shows a link with nowhere to go"
        assert 'id="forgotPasswordLink"' in markup


def test_the_shell_asks_the_application_for_the_address():
    source = SHELL.read_text(encoding="utf-8")

    assert "config.password_reset_url" in source
    assert "hint.classList.remove('hidden')" in source
    found = BOT_TEXT.findall(source)
    assert not found, f"the shell still names a bot: {found}"


def test_the_registration_refusal_names_a_way_in_and_no_messenger():
    client = make_client()

    answer = client.post("/api/auth/register", json={
        "username": "invitation-only",
        "password": "a-long-password-for-the-screen",
        "agree_terms": True,
    })

    assert answer.status_code == 403, answer.text
    detail = answer.json()["detail"]
    assert "invitation" in detail.lower()
    assert not BOT_TEXT.findall(detail), detail


def test_the_server_names_no_bot_where_a_person_signs_in():
    source = AUTH_ROUTES.read_text(encoding="utf-8")

    found = BOT_TEXT.findall(source)
    assert not found, f"the sign-in's own module names a bot: {found}"
