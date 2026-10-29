import pytest

from app.pagination import page_bounds


def test_first_page_default_size():
    assert page_bounds(1, None) == (0, 50)


def test_clamps_page_size():
    assert page_bounds(2, 1000) == (200, 200)


def test_rejects_page_zero():
    with pytest.raises(ValueError):
        page_bounds(0, 10)
