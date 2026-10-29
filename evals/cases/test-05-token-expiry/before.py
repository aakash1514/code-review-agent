"""Validation of signed API access tokens (already signature-checked by the caller)."""
from datetime import datetime


def is_token_valid(claims: dict, now: datetime) -> bool:
    """True if the token's `exp` claim (a Unix timestamp) is in the future."""
    return claims["exp"] > now.timestamp()
