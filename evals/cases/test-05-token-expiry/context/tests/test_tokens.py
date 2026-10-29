from datetime import datetime, timezone

from app.tokens import is_token_valid

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_future_exp_is_valid():
    assert is_token_valid({"exp": NOW.timestamp() + 600}, NOW)


def test_past_exp_is_invalid():
    assert not is_token_valid({"exp": NOW.timestamp() - 600}, NOW)
