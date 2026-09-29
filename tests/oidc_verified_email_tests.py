"""The email-domain policy admits only an email the provider verified (keepup-73).

Only the boolean False was refused: a provider that sent no email_verified
claim at all, or sent it as the string "false", had an address the person
typed compared with the admitted domains.

    python3 -m pytest keepup/tests/oidc_verified_email_tests.py -v
"""

import pytest

from keepup.auth import oidc_policy

POLICY = oidc_policy.create_if_email_domain(["example.com"])


def decision(**claims):
    return oidc_policy.decide(POLICY, {"sub": "s", "email": "person@example.com", **claims})


@pytest.mark.parametrize("verified", [True, "true", "True", " TRUE "])
def test_a_verified_email_of_the_domain_is_admitted(verified):
    assert decision(email_verified=verified).admit is True


@pytest.mark.parametrize("verified", [False, "false", "False", "", "yes", 1, None])
def test_anything_short_of_true_is_refused(verified):
    refused = decision(email_verified=verified)
    assert refused.admit is False
    assert "did not verify" in refused.reason


def test_a_missing_claim_is_refused():
    refused = oidc_policy.decide(POLICY, {"sub": "s", "email": "person@example.com"})
    assert refused.admit is False
