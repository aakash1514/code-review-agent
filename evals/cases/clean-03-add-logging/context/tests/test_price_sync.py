from app.price_sync import sync_prices


def test_sync_prices_counts_changes(fake_feed, fake_products):
    fake_feed.prices = {"A": 100, "B": 200, "C": 300}
    fake_products.add("A", 100)
    fake_products.add("B", 250)
    assert sync_prices("acme") == 1
    assert fake_products.get("B").price_cents == 200
