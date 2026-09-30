"""Tokens somebody made up, kept too long, or took from somebody else (keepup-94).

Each forgery pretends to be an administrator -- the account worth pretending to
be -- and knocks on three doors: a framework route, a plugin route and an
administrative route. The real token of the same account opens all three, which
is what makes every 401 here mean "refused" rather than "the harness is broken".

On the identity profile a token the framework did not sign goes to the provider;
the checks there also say which forgeries the provider never gets to see.

    python3 -m pytest keepup/tests/security_audit_tests/token_forgery_tests.py -v
"""

import pytest

import audit_actors
from audit_profiles import PLUGIN_SIGNED

pytestmark = pytest.mark.area("tokens")

DOORS = ("/api/auth/me", PLUGIN_SIGNED, "/api/admin/users")
PROFILES = ("plugins", "identity", "https")


def knock(running, token):
    return {door: running.client.get(door, headers={"Authorization": f"Bearer {token}"}).status_code
            for door in DOORS}


@pytest.mark.parametrize("profile_name", PROFILES)
def test_the_real_token_opens_every_door(start, profile_name):
    running = start(profile_name)
    admin = audit_actors.admin()
    assert set(knock(running, admin.token).values()) == {200}


@pytest.mark.parametrize("forgery", sorted(audit_actors.FORGERIES))
@pytest.mark.parametrize("profile_name", PROFILES)
def test_a_forged_token_opens_no_door(start, profile_name, forgery):
    running = start(profile_name)
    token = audit_actors.FORGERIES[forgery](audit_actors.admin())
    assert set(knock(running, token).values()) == {401}


@pytest.mark.parametrize("profile_name", PROFILES)
def test_a_live_session_of_another_account_is_not_borrowed(start, profile_name):
    """Our key, the administrator's name, a client's live session (keepup-64)."""
    running = start(profile_name)
    token = audit_actors.someone_elses_session(audit_actors.admin(), audit_actors.client_actor())
    assert set(knock(running, token).values()) == {401}


@pytest.mark.parametrize("profile_name", PROFILES)
def test_a_blocked_account_s_live_token_is_refused(start, profile_name):
    running = start(profile_name)
    blocked = audit_actors.make_actor(status="blocked")
    assert set(knock(running, blocked.token).values()) == {403}


def test_a_signed_out_session_is_over(start):
    running = start("plugins")
    admin = audit_actors.admin()
    assert set(knock(running, admin.token).values()) == {200}
    running.client.post("/api/auth/logout", headers=admin.bearer())
    assert set(knock(running, admin.token).values()) == {401}


@pytest.mark.parametrize("value", ["", "Bearer", "null", "undefined", "Bearer " + "A" * 5000])
def test_rubbish_in_the_header_is_refused_not_failed(start, value):
    running = start("plugins")
    for door in DOORS:
        assert running.client.get(door, headers={"Authorization": value}).status_code == 401


# --- what the provider gets to see ------------------------------------------------------

@pytest.mark.parametrize("forgery", ["expired", "no-session", "revoked-session"])
def test_a_token_of_ours_is_never_shown_to_the_provider(start, forgery):
    """Signed with our key, refused for another reason: refused here, not asked about."""
    running = start("identity")
    before = running.other.state.introspections
    token = audit_actors.FORGERIES[forgery](audit_actors.admin())
    assert set(knock(running, token).values()) == {401}
    assert running.other.state.introspections == before


@pytest.mark.parametrize("forgery", ["alg-none", "other-key", "rs256"])
def test_a_token_of_nobody_goes_to_the_provider_and_is_refused_there(start, forgery):
    running = start("identity")
    token = audit_actors.FORGERIES[forgery](audit_actors.admin())
    assert set(knock(running, token).values()) == {401}


def test_a_made_up_session_of_the_other_system_is_refused(start):
    running = start("identity")
    before = running.other.state.introspections
    assert set(knock(running, "hg_made-up-session-id").values()) == {401}
    assert running.other.state.introspections > before
