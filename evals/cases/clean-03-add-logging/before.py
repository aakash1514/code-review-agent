"""Syncs product prices from the supplier feed."""
from app.feed import fetch_prices
from app.repos import products


def sync_prices(supplier_id):
    """Update stored prices from the supplier's feed; returns how many changed."""
    changed = 0
    for sku, price_cents in fetch_prices(supplier_id).items():
        product = products.get(sku)
        if product is None or product.price_cents == price_cents:
            continue
        products.update_price(sku, price_cents)
        changed += 1
    return changed
