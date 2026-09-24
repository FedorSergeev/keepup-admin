"""What to do with somebody the application has never seen before.

The provider has just proved who this person is. It has not said anything
about whether they may use this product -- that is not a question an identity
provider can answer, and the framework cannot answer it either: for one
application everyone at the provider is welcome, for another access is by
invitation and the provider is only a way of proving a name.

So the decision belongs to the application, and the framework's default is to
refuse. The opposite default would turn switching a provider on into open
registration for a product that never asked for it -- and it would do so
quietly, which is the worst way for it to happen.
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional

from keepup.roles import ROLE_CLIENT

logger = logging.getLogger(__name__)


@dataclass
class AccountDecision:
    """Either "no", or the account to create."""

    admit: bool
    role: str = ROLE_CLIENT
    status: str = "active"
    #: Why, for the log. Never shown to whoever is signing in.
    reason: str = ""

    @classmethod
    def refuse(cls, reason: str) -> "AccountDecision":
        return cls(admit=False, reason=reason)

    @classmethod
    def create(cls, role: str = ROLE_CLIENT, status: str = "active",
               reason: str = "") -> "AccountDecision":
        return cls(admit=True, role=role, status=status, reason=reason)


def refuse_unknown() -> Callable[[Dict[str, Any]], AccountDecision]:
    """The default: prove who you are all you like, there is no account here."""

    def decide(claims: Dict[str, Any]) -> AccountDecision:
        return AccountDecision.refuse("the application admits nobody it does not already know")

    return decide


def create_account(role: str = ROLE_CLIENT,
                   status: str = "active") -> Callable[[Dict[str, Any]], AccountDecision]:
    """Anyone the provider vouches for gets an account.

    Reasonable when the provider is the company's own directory and everybody
    behind it is staff. Open registration when the provider is a public one --
    which is the application's call to make, deliberately.
    """

    def decide(claims: Dict[str, Any]) -> AccountDecision:
        return AccountDecision.create(role=role, status=status,
                                      reason="the application admits everyone the provider vouches for")

    return decide


def create_if_email_domain(domains: Iterable[str], role: str = ROLE_CLIENT,
                           status: str = "active") -> Callable[[Dict[str, Any]], AccountDecision]:
    """An account for staff, nothing for everyone else at the same provider.

    The email is compared only when the provider says it verified it: an
    unverified address is a string the person typed, and several public
    providers will hand it over unchecked.
    """
    allowed = {domain.lower().lstrip("@") for domain in domains}

    def decide(claims: Dict[str, Any]) -> AccountDecision:
        email = (claims.get("email") or "").lower()
        if not email or "@" not in email:
            return AccountDecision.refuse("no email in the provider's claims")
        if claims.get("email_verified") is False:
            return AccountDecision.refuse("the provider did not verify this email")
        domain = email.rsplit("@", 1)[1]
        if domain not in allowed:
            return AccountDecision.refuse(f"email domain {domain!r} is not admitted")
        return AccountDecision.create(role=role, status=status,
                                      reason=f"email domain {domain!r} is admitted")

    return decide


def decide(policy: Optional[Callable], claims: Dict[str, Any]) -> AccountDecision:
    """Ask the application, and refuse if it has not said anything.

    A policy that raises is a refusal too: an application whose rule fell over
    has not admitted anybody, and treating the failure as consent is how a door
    opens by accident.
    """
    if policy is None:
        return refuse_unknown()(claims)
    try:
        decision = policy(claims)
    except Exception as error:
        logger.error(f"The account policy raised; refusing: {error}")
        return AccountDecision.refuse("the application's policy failed")
    if not isinstance(decision, AccountDecision):
        logger.error(f"The account policy returned {type(decision).__name__}, not a decision; refusing")
        return AccountDecision.refuse("the application's policy answered nothing usable")
    return decision
