from unittest.mock import patch

from app.fx import rates


def test_fresh_cache_is_used():
    rates._cache.clear()
    with patch.object(rates, "fetch_rate", return_value=90.0) as fetch:
        assert rates.get_rate("EUR/INR") == 90.0
        assert rates.get_rate("EUR/INR") == 90.0
    fetch.assert_called_once()


def test_stale_cache_is_refetched():
    rates._cache.clear()
    rates._cache["EUR/INR"] = (80.0, -1e9)
    with patch.object(rates, "fetch_rate", return_value=91.0):
        assert rates.get_rate("EUR/INR") == 91.0
