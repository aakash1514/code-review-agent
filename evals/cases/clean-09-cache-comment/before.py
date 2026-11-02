"""Exchange rates with a short-lived in-process cache."""
import time

from app.rates_api import fetch_rate

TTL_SECONDS = 300
_cache: dict[str, tuple[float, float]] = {}


def get_rate(pair: str) -> float:
    """The exchange rate for pair, e.g. "EUR/INR"."""
    rate = fetch_rate(pair)
    _cache[pair] = (rate, time.monotonic())
    return rate
