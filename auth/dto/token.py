"""What a sign-in hands back, and what a token carries.

Two shapes rather than dictionaries: the reply is part of the API and the claim
set is read on every request, and both used to be assembled by hand in several
places that disagreed about the field names.
"""

from typing import Optional

from pydantic import BaseModel

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = [
    "Token",
    "TokenData",
]


class Token(BaseModel):
    """What a successful sign-in hands back."""

    access_token: str
    token_type: str
    success: bool = True


class TokenData(BaseModel):
    """The claims read back out of a token."""

    username: Optional[str] = None