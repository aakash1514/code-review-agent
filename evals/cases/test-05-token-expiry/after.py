"""Validation of signed API access tokens (already signature-checked by the caller)."""
from datetime import datetime

CLOCK_SKEW_SECONDS = 60


def is_token_valid(claims: dict, now: datetime, audience: str | None = None) -> bool:
    """True if the token is usable at `now`.

    Allows CLOCK_SKEW_SECONDS of skew on both `exp` and `nbf` (Unix timestamps).
    When `audience` is given, the token's `aud` claim (a string or a list) must
    contain it.
    """
    ts = now.timestamp()
    if claims["exp"] + CLOCK_SKEW_SECONDS < ts:
        return False
    if "nbf" in claims and claims["nbf"] - CLOCK_SKEW_SECONDS > ts:
        return False
    if audience is not None:
        aud = claims.get("aud", [])
        if isinstance(aud, str):
            aud = [aud]
        if audience not in aud:
            return False
    return True
