"""Retry policy for calls to the partner API."""
import random


def backoff_delay(attempt: int, status_code: int | None = None) -> float | None:
    """Seconds to wait before retry number `attempt` (1-based), or None to give up."""
    if attempt > 7:
        return None
    delay = min(0.25 * 2 ** attempt, 32)
    if status_code == 429:
        delay *= 1.5
    elif status_code in (502, 503):
        delay = max(delay, 4)
    return delay * (0.8 + 0.4 * random.random())


def should_retry(status_code: int) -> bool:
    """True for responses worth retrying: rate limits and server errors."""
    return status_code == 429 or 500 <= status_code < 600
