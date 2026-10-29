import pytest

from app.money import Money


def test_add_same_currency():
    assert Money(150, "EUR") + Money(275, "EUR") == Money(425, "EUR")


def test_add_different_currency_fails():
    with pytest.raises(ValueError):
        Money(1, "EUR") + Money(1, "USD")


def test_rejects_bad_currency():
    with pytest.raises(ValueError):
        Money(1, "eur")


def test_str_negative():
    assert str(Money(-105, "USD")) == "-1.05 USD"
