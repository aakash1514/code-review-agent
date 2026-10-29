import pytest

from app.shipping import shipping_cost


def test_light_parcel_gets_base_rate():
    assert shipping_cost(1.0) == 499


def test_heavy_parcel_gets_heavy_rate():
    assert shipping_cost(12.0) == 1299


def test_express_doubles():
    assert shipping_cost(1.0, express=True) == 998


def test_rejects_zero_weight():
    with pytest.raises(ValueError):
        shipping_cost(0)
