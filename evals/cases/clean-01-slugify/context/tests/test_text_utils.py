from app.text_utils import slugify


def test_slugify_basic():
    assert slugify("Hello, World!") == "hello-world"


def test_slugify_accents():
    assert slugify("Crème brûlée") == "creme-brulee"


def test_slugify_truncates_without_trailing_dash():
    assert slugify("a" * 59 + " b", max_length=60) == "a" * 59


def test_slugify_empty():
    assert slugify("!!!") == ""
